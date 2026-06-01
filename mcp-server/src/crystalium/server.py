"""MCP server wiring for CRYSTALIUM.

Translates between the MCP protocol (tools/list, tools/call) and the
CRYSTALIUM tool implementations. Every call passes through the enforcement
chokepoint before any storage layer is touched.

Design anchors:
  D2: stdio is the default transport; Streamable-HTTP (run_http) added in v0.2.
      Both transports share _build_server + the @server.call_tool dispatch.
  D3: session_end triggers DreamScheduler.on_session_end() (enqueues; never inline).
  D4: every tool result emits ECL v2.0 sidecar via ecl.build_for_tool_result() +
      ecl.emit_sidecar().
  G7: sidecar written next to a per-call run_dir (data_dir/runs/<message_id>/).

Caller identity (D4 conservative):
  - MCP SDK (v0.1 Python SDK) does not expose per-request identity headers.
  - Default fallback: {eidolon: "unknown", version: "n/a", tier: Tier.T2}.
  - This is the OQ-3 "unknown" default; reviewed post-v0.1.

Pattern mirrors atlas-aci/mcp-server/src/atlas_aci/server.py (FINDING-001).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from crystalium import __version__
from crystalium.aetheryte.cache import RecallCache
from crystalium.aetheryte.redact import Redactor
from crystalium.aetheryte.retrieve import Aetheryte
from crystalium.composer import Composer
from crystalium.config import Config
from crystalium.dream.scheduler import DreamScheduler
from crystalium.dream.worker import DreamWorker
from crystalium.ecl import build_for_tool_result, emit_sidecar
from crystalium.enforcement import Enforcement
from crystalium.gate import PromotionGate
from crystalium.evb import make_evb_scorer
from crystalium.importance import importance_score
from crystalium.layers.episodic import EpisodicLayer
from crystalium.layers.execution import ExecutionLayer
from crystalium.layers.procedural import ProceduralLayer
from crystalium.layers.semantic import SemanticLayer
from crystalium.schemas import Provenance, Scope
from crystalium.storage.blob import BlobStore
from crystalium.storage.graph import GraphStore
from crystalium.storage.relational import RelationalStore
from crystalium.storage.vector import VectorStore
from crystalium.telemetry import emit_latency_panel, record_call, tool_span
from crystalium.trust import Tier

log = structlog.get_logger("crystalium.server")

# ---------------------------------------------------------------------------
# Default caller identity (D4 conservative, OQ-3)
# ---------------------------------------------------------------------------

_DEFAULT_CALLER: dict[str, Any] = {
    "eidolon": "unknown",
    "version": "n/a",
    "tier": "T2",
}


def _extract_caller_identity() -> dict[str, Any]:
    """Extract caller identity from MCP request context.

    In v0.1 the mcp Python SDK does not expose per-request identity headers,
    so this always returns the D4 conservative default.

    OQ-3: revisit when SDK surfaces identity headers or a crystalium-specific
    extension field is defined for callers to pass their identity.
    """
    return dict(_DEFAULT_CALLER)


def _caller_tier(caller: dict[str, Any]) -> Tier:
    """Parse the 'tier' field from a caller identity dict into a Tier enum."""
    raw = caller.get("tier", "T2")
    try:
        return Tier.from_str(str(raw))
    except ValueError:
        return Tier.T2


# ---------------------------------------------------------------------------
# Tool manifest (spec.yaml §tool_surface)
# ---------------------------------------------------------------------------


def build_tool_manifest() -> list[dict[str, Any]]:
    """Return the seven tool descriptors served via tools/list.

    Descriptions include enforcement bounds per spec.yaml §tool_surface.
    """
    return [
        {
            "name": "crystalium.recall",
            "description": (
                "Hybrid BM25 + dense + graph recall from CRYSTALIUM memory. "
                "Returns slot-budgeted, redacted CrystalSummary records. "
                "Universally allowed (all tiers). "
                "Rate-limited (200 calls/min)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["scope", "query"],
                "properties": {
                    "scope": {
                        "type": "object",
                        "description": "Scope filter: {project, agent_class_visibility, sensitivity_tag}",
                    },
                    "query": {"type": "string", "description": "Free-text recall query"},
                    "k": {
                        "type": "integer",
                        "default": 10,
                        "description": "Max records to return",
                    },
                    "layers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["episodic", "semantic", "procedural", "execution"]},
                        "description": "Subset of layers to search (default: all four)",
                    },
                },
            },
        },
        {
            "name": "crystalium.commit",
            "description": (
                "Commit a crystal to a memory layer. "
                "T3 callers: Episodic-quarantine only (G1). "
                "T2: Episodic or Procedural-candidate only (G1, G2). "
                "T1/T0: Semantic, Procedural, Execution allowed (with gate checks G4, G5). "
                "Bi-temporal: write-new, never hard-delete (P0-5)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["layer", "payload", "provenance"],
                "properties": {
                    "layer": {
                        "type": "string",
                        "enum": ["episodic", "semantic", "procedural", "execution"],
                    },
                    "payload": {
                        "type": "object",
                        "description": "Crystal payload (must include 'summary', 'scope')",
                    },
                    "provenance": {
                        "type": "object",
                        "description": "Provenance dict: {source, author_agent, task_id, created_at}",
                    },
                },
            },
        },
        {
            "name": "crystalium.ingest",
            "description": (
                "Ingest a roster ECL handoff envelope (v1.x or v2.x) as a crystal. "
                "Maps any artifact to crystal.v1 (native payload preserved verbatim in "
                "encoding_context); commits THROUGH the chokepoint so MIN-trust is "
                "preserved. T3/tool-origin artifacts land Episodic-quarantined; never "
                "laundered to a higher tier."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["envelope", "payload"],
                "properties": {
                    "envelope": {
                        "type": "object",
                        "description": "Inbound ECL envelope (11 fields; envelope_version 1.x/2.x).",
                    },
                    "payload": {
                        "description": "The artifact payload the envelope wraps (object or string).",
                    },
                    "payload_encoding": {
                        "type": "string",
                        "enum": ["utf8", "base64", "json"],
                        "description": "How to decode a string payload (default utf8).",
                    },
                },
            },
        },
        {
            "name": "crystalium.update",
            "description": (
                "Bi-temporal update: invalidate old crystal (t_valid_to=now, superseded_by=new_id), "
                "write new revision. Never hard-delete (P0-5). "
                "Same tier rules as crystalium.commit for the target crystal's layer."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["id", "patch", "reason"],
                "properties": {
                    "id": {"type": "string", "description": "CrystalId of the record to update"},
                    "patch": {
                        "type": "object",
                        "description": "Partial payload dict to merge with existing crystal",
                    },
                    "reason": {"type": "string", "description": "Human-readable reason for update"},
                },
            },
        },
        {
            "name": "crystalium.skill_invoke",
            "description": (
                "Run a procedural verifier skill in a sandboxed subprocess (G3). "
                "D5 bounds: timeout_s<=30, output_cap_bytes<=8192, "
                "workdir MUST resolve under /sandbox/<skill_id>. "
                "OPERATOR WARNING: container IS the sandbox boundary."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["skill_id"],
                "properties": {
                    "skill_id": {"type": "string", "description": "Procedural skill identifier"},
                    "args": {"type": "object", "description": "Arguments passed to the skill"},
                    "timeout_s": {"type": "integer", "maximum": 30, "default": 30},
                    "output_cap_bytes": {"type": "integer", "maximum": 8192, "default": 8192},
                    "workdir": {
                        "type": "string",
                        "description": "Workdir path; MUST resolve under /sandbox/<skill_id>",
                    },
                },
            },
        },
        {
            "name": "crystalium.plan_checkpoint",
            "description": (
                "Write a TTL-bound plan checkpoint to the Execution layer. "
                "T0/T1 only (G1: T2/T3 blocked). "
                "Expires after execution TTL (default 24h)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["state"],
                "properties": {
                    "state": {
                        "type": "object",
                        "description": "Plan state snapshot dict (must include 'scope')",
                    },
                },
            },
        },
        {
            "name": "crystalium.plan_replan",
            "description": (
                "Append a plan replan diff to the Execution layer. "
                "Bi-temporal: if diff.supersedes_id is set, invalidates the old plan node. "
                "T0/T1 only (G1: T2/T3 blocked)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["diff"],
                "properties": {
                    "diff": {
                        "type": "object",
                        "description": "Plan diff dict; set supersedes_id to chain bi-temporally",
                    },
                },
            },
        },
        {
            "name": "crystalium.session_end",
            "description": (
                "Signal end-of-session. Enqueues a Dream consolidation run (G8). "
                "Returns {enqueued: bool, dream_run_id: str|null}. "
                "Deduped: concurrent calls yield at most one enqueued run. "
                "D3: fast-path complements the 60s idle-poll floor."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Optional free-text reason for session end",
                    },
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# Server factory — builds all internal components from Config
# ---------------------------------------------------------------------------


def _build_components(
    config: Config,
) -> tuple[
    Enforcement,
    Aetheryte,
    EpisodicLayer,
    SemanticLayer,
    ProceduralLayer,
    ExecutionLayer,
    PromotionGate,
    DreamScheduler,
    "RelationalStore",
]:
    """Instantiate all server components from *config*.

    Storage adapters are created lazily; the vector/graph stores are optional
    (they raise ImportError if the heavy deps are absent).
    """
    blob_store = BlobStore(root=config.blob_root)
    relational = RelationalStore(db_path=config.sqlite_path)
    enforcement = Enforcement(config)
    redactor = Redactor(config=config)
    composer = Composer(config)

    # Optional heavy stores — fall back to None-like stubs on ImportError
    try:
        vector_store: Any = VectorStore(lance_dir=config.lance_path)
    except Exception as exc:
        log.warning("vector_store_unavailable", error=str(exc))
        vector_store = _NullVectorStore()

    try:
        graph_store: Any = GraphStore(kuzu_dir=config.kuzu_path)
    except Exception as exc:
        log.warning("graph_store_unavailable", error=str(exc))
        graph_store = _NullGraphStore()

    gate = PromotionGate(config, relational, enforcement)

    # W2: single swap point for the importance function. When evb_enabled, EVB
    # (Gain x Need) replaces the legacy importance_score everywhere it is injected
    # (eviction, composer, layers). Default OFF (ablation-or-revert). The
    # enforcement chokepoint never receives importance_fn — it stays untouched.
    importance_fn = (
        make_evb_scorer(config.evb_gain_weights, config.evb_need_weights)
        if config.evb_enabled
        else importance_score
    )

    # W5 retrieval intelligence (all default OFF). Shared in-process recall cache
    # only exists when prefetch is on; layers learn co-occurrence edges only when
    # completion is on (so OFF arms stay byte-identical to W4).
    recall_cache = RecallCache() if config.recall_prefetch else None

    episodic = EpisodicLayer(
        blob_store=blob_store,
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_fn,
        dedup_merge=config.write_dedup_merge,
        sep_threshold=config.sep_threshold,
        link_cooccurrence=config.recall_completion,
    )
    semantic = SemanticLayer(
        blob_store=blob_store,
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        gate=gate,
        redactor=redactor,
        importance_fn=importance_fn,
        dedup_merge=config.write_dedup_merge,
        sep_threshold=config.sep_threshold,
        link_cooccurrence=config.recall_completion,
        write_conflict_detect=config.write_conflict_detect,
        conflict_tau_lo=config.conflict_tau_lo,
    )
    procedural = ProceduralLayer(
        blob_store=blob_store,
        relational=relational,
        enforcement=enforcement,
        gate=gate,
        redactor=redactor,
        importance_fn=importance_fn,
        data_dir=config.data_dir,
    )
    aetheryte = Aetheryte(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_fn,
        composer=composer,
        persist_dynamics=config.evb_enabled,
        forgetting_fsrs=config.forgetting_fsrs,
        r_floor=config.r_floor,
        fsrs_boost_factor=config.fsrs_boost_factor,
        fsrs_initial_stability=config.fsrs_initial_stability,
        fsrs_initial_difficulty=config.fsrs_initial_difficulty,
        fsrs_lapse_stability=config.fsrs_lapse_stability,
        completion=config.recall_completion,
        completion_max_hops=config.completion_max_hops,
        completion_decay=config.completion_decay,
        context_match=config.recall_context_match,
        recall_cache=recall_cache,
        recall_active_only=config.recall_active_only,
    )
    # Execution layer depends on aetheryte/recall_cache for W5 prefetch warming.
    execution = ExecutionLayer(
        blob_store=blob_store,
        relational=relational,
        enforcement=enforcement,
        importance_fn=importance_fn,
        aetheryte=aetheryte,
        recall_cache=recall_cache,
        recall_prefetch=config.recall_prefetch,
    )

    worker = DreamWorker(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        gate=gate,
        importance_fn=importance_fn,
        persist_dynamics=config.evb_enabled,
        replay_evb=config.dream_replay_evb,
        interleave=config.dream_interleave,
        interleave_ratio=config.dream_interleave_ratio,
        stc=config.dream_stc,
        stc_threshold=config.stc_threshold,
        stc_window_s=config.stc_window_s,
        forgetting_fsrs=config.forgetting_fsrs,
        r_floor=config.r_floor,
        resurface_floor=config.resurface_floor,
        evb_percentile=config.evb_percentile,
        fsrs_initial_stability=config.fsrs_initial_stability,
        fsrs_boost_factor=config.fsrs_boost_factor,
        fsrs_lapse_stability=config.fsrs_lapse_stability,
        drift_detect=config.drift_detect,
        drift_tau_lo=config.drift_tau_lo,
        drift_tau_hi=config.drift_tau_hi,
    )
    scheduler = DreamScheduler(config=config, worker=worker, store=relational)
    # Battle-test fix (CRITICAL): close the trigger→execute→release loop. The
    # scheduler's background tick drains worker.run_pending(); each run then calls
    # back into record_dream_completed to free the in-memory pending slot. Wired
    # here (post-construction) because worker is built before the scheduler exists.
    worker.on_run_complete = scheduler.record_dream_completed

    return enforcement, aetheryte, episodic, semantic, procedural, execution, gate, scheduler, relational


# ---------------------------------------------------------------------------
# Null stub stores (used when heavy deps absent — keeps tests light)
# ---------------------------------------------------------------------------


class _NullVectorStore:
    """No-op VectorStore — used when LanceDB / sentence-transformers unavailable."""

    def embed(self, text: str) -> list[float]:
        return []

    def dense_search(self, query_vec: list[float], layer_filter: str, k: int) -> list[dict]:
        return []


class _NullGraphStore:
    """No-op GraphStore — used when KuzuDB unavailable."""

    def neighbor_expand(self, seed_ids: list[str], depth: int = 1) -> list[str]:
        return []

    def decaying_walk(self, seed_ids: list[str], max_hops: int = 2, decay: float = 0.5) -> dict:
        return {}

    def add_node(self, *args, **kwargs) -> None:  # noqa: ARG002 — W5 edge writes no-op
        return None

    def add_edge(self, *args, **kwargs) -> None:  # noqa: ARG002 — W5 edge writes no-op
        return None


# ---------------------------------------------------------------------------
# ECL sidecar helper (G7)
# ---------------------------------------------------------------------------


def _emit_ecl_sidecar(
    tool_name: str,
    result_bytes: bytes,
    artifact_kind: str,
    run_dir: Path,
    caller: dict[str, Any],
    performative: str = "INFORM",
) -> None:
    """Build and write the ECL sidecar for a tool result (G7).

    Non-fatal: sidecar failure is logged but does not propagate to the caller.
    """
    try:
        envelope = build_for_tool_result(
            tool_name=tool_name,
            payload=result_bytes,
            artifact_kind=artifact_kind,
            caller_identity=caller,
            performative=performative,
        )
        emit_sidecar(payload=result_bytes, run_dir=run_dir, envelope=envelope)
    except Exception as exc:
        log.warning("ecl_sidecar_failed", tool=tool_name, error=str(exc))


# ---------------------------------------------------------------------------
# Main server runner
# ---------------------------------------------------------------------------


def _build_server(config: Config) -> tuple[Server, DreamScheduler]:
    """Build the MCP Server with all 7 tools wired, plus its DreamScheduler.

    Transport-agnostic: shared verbatim by both run_stdio and run_http (D2,
    v0.2). The caller is responsible for starting the scheduler and running the
    chosen transport. Components (_build_components) and the @server.call_tool
    dispatch are reused as-is across transports.
    """
    server = Server("crystalium")

    (
        enforcement,
        aetheryte,
        episodic,
        semantic,
        procedural,
        execution,
        gate,
        scheduler,
        relational,
    ) = _build_components(config)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [Tool(**t) for t in build_tool_manifest()]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        caller = _extract_caller_identity()
        caller_tier = _caller_tier(caller)
        tier_str = getattr(caller_tier, "name", str(caller_tier))
        t0 = time.monotonic()
        run_dir = config.data_dir / "runs" / str(uuid.uuid4())
        layer_hint: str | None = None

        try:
            enforcement.assert_rate_limit()

            # One OTel span per tool call (telemetry.tool_span); per-call latency
            # is recorded once on the success path below and once on the error
            # path. Layer/tool-specific telemetry inside the layers/dream worker
            # is preserved and nests under this span.
            with tool_span(name, tier=tier_str):
                performative = "INFORM"
                if name == "crystalium.recall":
                    result = _handle_recall(arguments, aetheryte, scheduler, caller_tier)
                    scheduler.record_activity()
                    result_bytes = json.dumps(
                        result if isinstance(result, dict) else result.model_dump(),
                        default=str,
                    ).encode()
                    artifact_kind = "recall-result"

                elif name == "crystalium.commit":
                    layer_hint = arguments.get("layer")
                    result = _handle_commit(
                        arguments, episodic, semantic, procedural, execution, caller_tier,
                        recall_cache=execution.recall_cache,
                    )
                    scheduler.record_activity()
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "commit-result"

                elif name == "crystalium.ingest":
                    result = _handle_ingest(
                        arguments, episodic, semantic, procedural, execution,
                        recall_cache=execution.recall_cache,
                    )
                    layer_hint = result.get("layer")
                    scheduler.record_activity()
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "ingest-receipt"

                elif name == "crystalium.update":
                    layer_hint = arguments.get("layer")
                    result = _handle_update(arguments, episodic, semantic, procedural, execution, enforcement, relational, caller_tier)
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "commit-result"

                elif name == "crystalium.skill_invoke":
                    layer_hint = "procedural"
                    result = _handle_skill_invoke(arguments, procedural, enforcement, config, caller_tier)
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "skill-result"

                elif name == "crystalium.plan_checkpoint":
                    layer_hint = "execution"
                    result = execution.checkpoint(
                        state=arguments.get("state", {}),
                        caller_tier=caller_tier,
                    )
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "plan-checkpoint"

                elif name == "crystalium.plan_replan":
                    layer_hint = "execution"
                    result = execution.replan(
                        diff=arguments.get("diff", {}),
                        caller_tier=caller_tier,
                    )
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "plan-replan"

                elif name == "crystalium.session_end":
                    result = _handle_session_end(arguments, scheduler)
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "session-end-receipt"
                    performative = "ACKNOWLEDGE"
                    # Surface the recall/commit/forget/dream latency panels +
                    # recall p95 (W8 SLO) at the close of the session.
                    emit_latency_panel()

                else:
                    from crystalium.enforcement import CrystaliumEnforcementError
                    raise CrystaliumEnforcementError(
                        f"Unknown tool {name!r}.",
                        reason_code="UNKNOWN_TOOL",
                    )

                _emit_ecl_sidecar(
                    name, result_bytes, artifact_kind, run_dir, caller,
                    performative=performative,
                )

            latency_ms = (time.monotonic() - t0) * 1000
            record_call(
                tool=name, layer=layer_hint, tier=tier_str,
                result="ok", latency_ms=latency_ms,
            )
            return [TextContent(type="text", text=result_bytes.decode())]

        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            outcome = "rejected" if hasattr(exc, "reason_code") else "error"
            error_code = getattr(exc, "reason_code", type(exc).__name__)
            enforcement.record(name, None, caller_tier, None, outcome, latency_ms, error=error_code)
            record_call(
                tool=name, layer=layer_hint, tier=tier_str,
                result=outcome, latency_ms=latency_ms, error=error_code,
            )
            log.exception("tool_error", tool=name, error=str(exc))
            err_payload: dict[str, Any] = {
                "error": error_code,
                "message": str(exc),
            }
            advice = getattr(exc, "advice", "")
            if advice:
                err_payload["advice"] = advice
            return [TextContent(type="text", text=json.dumps(err_payload, indent=2))]

    return server, scheduler


async def run_stdio(config: Config) -> None:
    """Start the CRYSTALIUM MCP server over stdio (the default transport)."""
    server, scheduler = _build_server(config)
    scheduler.start()
    log.info(
        "server_starting",
        transport="stdio",
        data_dir=str(config.data_dir),
        version=__version__,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def build_http_app(config: Config):
    """Build the Starlette ASGI app for the Streamable-HTTP transport (D2, v0.2).

    Returns (app, scheduler, session_manager). build_http_app starts nothing —
    the caller (run_http) starts the scheduler and serves the app — so tests can
    drive the app through an ASGI test client without a live socket or the
    APScheduler daemon. Every tool result still flows through the shared
    @server.call_tool dispatch, so the ECL v2.0 sidecar is emitted over HTTP too.
    """
    import contextlib

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    server, scheduler = _build_server(config)

    session_manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=config.http_json_response,
        stateless=config.http_stateless,
    )

    async def handle_streamable_http(scope: Any, receive: Any, send: Any) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any):
        async with session_manager.run():
            yield

    app = Starlette(
        routes=[Mount(config.http_path, app=handle_streamable_http)],
        lifespan=lifespan,
    )
    return app, scheduler, session_manager


async def run_http(config: Config) -> None:
    """Start the CRYSTALIUM MCP server over Streamable-HTTP (D2 unstubbed, v0.2)."""
    import uvicorn

    app, scheduler, _session_manager = build_http_app(config)
    scheduler.start()
    log.info(
        "server_starting",
        transport="streamable-http",
        host=config.http_host,
        port=config.http_port,
        path=config.http_path,
        data_dir=str(config.data_dir),
        version=__version__,
    )
    uv = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.http_host,
            port=config.http_port,
            log_level="warning",
        )
    )
    await uv.serve()


# ---------------------------------------------------------------------------
# Individual tool handlers
# ---------------------------------------------------------------------------


def _handle_recall(
    args: dict[str, Any],
    aetheryte: Aetheryte,
    scheduler: DreamScheduler,
    caller_tier: Tier,
) -> Any:
    """Handle crystalium.recall."""
    raw_scope = args.get("scope", {})
    scope = Scope(
        project=raw_scope.get("project", "default"),
        agent_class_visibility=raw_scope.get("agent_class_visibility"),
        sensitivity_tag=raw_scope.get("sensitivity_tag"),
    )
    query = args.get("query", "")
    # Battle-test fix: clamp k to a non-negative int (a negative k slices the
    # candidate list from the tail — silent mis-ranking); default 10 on garbage.
    try:
        k = max(0, int(args.get("k", 10)))
    except (TypeError, ValueError):
        k = 10
    layers = args.get("layers")

    result = aetheryte.recall(
        scope=scope,
        query=query,
        k=k,
        layers=layers,
        caller_tier=caller_tier,
    )
    return result.model_dump() if hasattr(result, "model_dump") else result


def _handle_commit(
    args: dict[str, Any],
    episodic: EpisodicLayer,
    semantic: SemanticLayer,
    procedural: ProceduralLayer,
    execution: ExecutionLayer,
    caller_tier: Tier,
    recall_cache: Any = None,
) -> dict[str, Any]:
    """Dispatch crystalium.commit to the correct layer adapter."""
    import datetime as _dt
    layer = args.get("layer", "episodic")
    payload = args.get("payload", {})
    raw_prov = args.get("provenance", {})
    now_iso = _dt.datetime.now(_dt.timezone.utc)
    created_at_raw = raw_prov.get("created_at", now_iso)
    if isinstance(created_at_raw, str):
        created_at_raw = _dt.datetime.fromisoformat(created_at_raw)
    provenance = Provenance(
        source=raw_prov.get("source", "unverified_agent"),
        author_agent=raw_prov.get("author_agent"),
        task_id=raw_prov.get("task_id"),
        created_at=created_at_raw,
    )

    # W5: a write into a project invalidates that project's cached recalls
    # (staleness guard). No-op when prefetch is off (recall_cache is None).
    if recall_cache is not None:
        project = (payload.get("scope") or {}).get("project") if isinstance(payload, dict) else None
        if project:
            recall_cache.invalidate_project(project)

    if layer == "episodic":
        return episodic.commit(payload=payload, provenance=provenance, caller_tier=caller_tier)
    elif layer == "semantic":
        return semantic.commit(payload=payload, provenance=provenance, caller_tier=caller_tier)
    elif layer == "procedural":
        return procedural.commit(payload=payload, provenance=provenance, caller_tier=caller_tier)
    elif layer == "execution":
        return execution.checkpoint(state=payload, caller_tier=caller_tier)
    else:
        from crystalium.enforcement import CrystaliumEnforcementError
        raise CrystaliumEnforcementError(
            f"Unknown layer {layer!r}. Must be one of: episodic, semantic, procedural, execution.",
            reason_code="UNKNOWN_LAYER",
        )


def _handle_ingest(
    args: dict[str, Any],
    episodic: EpisodicLayer,
    semantic: SemanticLayer,
    procedural: ProceduralLayer,
    execution: ExecutionLayer,
    recall_cache: Any = None,
) -> dict[str, Any]:
    """Ingest a roster ECL handoff envelope as a crystal (W7).

    Validates the inbound envelope (version-tolerant), maps it to a crystal.v1
    payload via the generic adapter (native artifact preserved verbatim), then
    commits THROUGH the existing layer.commit — so the chokepoint (rate-limit +
    tier + ceiling) fires and MIN-trust is preserved. A T3 artifact lands episodic-
    quarantined and is never laundered to a higher tier.
    """
    import datetime as _dt

    from crystalium.ecl import UnsupportedEnvelopeVersion, compute_sha256, validate_inbound_envelope
    from crystalium.enforcement import CrystaliumEnforcementError
    from crystalium.ingest_adapter import map_envelope_to_crystal, resolve_caller_tier

    envelope = args.get("envelope")
    if not isinstance(envelope, dict):
        raise CrystaliumEnforcementError(
            "ingest requires an 'envelope' object.", reason_code="INGEST_BAD_ENVELOPE"
        )
    payload = args.get("payload")
    payload_encoding = args.get("payload_encoding", "utf8")

    try:
        validate_inbound_envelope(envelope)
    except UnsupportedEnvelopeVersion as exc:
        raise CrystaliumEnforcementError(
            str(exc), reason_code="UNSUPPORTED_ENVELOPE_VERSION"
        ) from exc
    except ValueError as exc:
        raise CrystaliumEnforcementError(
            f"malformed inbound envelope: {exc}", reason_code="INGEST_BAD_ENVELOPE"
        ) from exc

    # Battle-test fix (G7 integrity binding): validate_inbound_envelope only checks
    # the envelope's internal self-consistency (integrity.value == artifact.sha256).
    # The trust boundary also requires the *actual payload bytes* to hash to the
    # declared artifact.sha256 — otherwise a sender can deliver content that does not
    # match its attested hash. Hash the RAW transport bytes (what the sender hashed:
    # payload.encode()), NOT the decoded form, so legitimate base64/json encodings are
    # not falsely rejected. Skipped only for structured (dict/list) payloads, whose
    # canonical byte form is not defined here.
    declared_sha = (envelope.get("artifact") or {}).get("sha256")
    if declared_sha and isinstance(payload, (str, bytes)):
        raw_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
        actual_sha = compute_sha256(raw_bytes)
        if actual_sha != declared_sha:
            raise CrystaliumEnforcementError(
                "inbound payload bytes do not hash to artifact.sha256 (G7 binding).",
                reason_code="INGEST_PAYLOAD_HASH_MISMATCH",
            )

    caller_tier = resolve_caller_tier(envelope)
    layer, crystal_payload = map_envelope_to_crystal(
        envelope, payload, caller_tier, payload_encoding
    )

    prov = crystal_payload.get("provenance", {}) or {}
    created_raw = prov.get("created_at")
    if isinstance(created_raw, str):
        try:
            created_at = _dt.datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            created_at = _dt.datetime.now(_dt.timezone.utc)
    else:
        created_at = _dt.datetime.now(_dt.timezone.utc)
    provenance = Provenance(
        source=prov.get("source", "unverified_agent"),
        author_agent=prov.get("author_agent"),
        task_id=prov.get("task_id"),
        created_at=created_at,
    )

    # W5 staleness guard: a write into a project invalidates its cached recalls.
    if recall_cache is not None:
        project = (crystal_payload.get("scope") or {}).get("project")
        if project:
            recall_cache.invalidate_project(project)

    if layer == "semantic":
        commit_result = semantic.commit(payload=crystal_payload, provenance=provenance, caller_tier=caller_tier)
    elif layer == "procedural":
        commit_result = procedural.commit(payload=crystal_payload, provenance=provenance, caller_tier=caller_tier)
    elif layer == "execution":
        commit_result = execution.checkpoint(state=crystal_payload, caller_tier=caller_tier)
    else:  # episodic (default; only layer accepting T3 -> quarantined)
        commit_result = episodic.commit(payload=crystal_payload, provenance=provenance, caller_tier=caller_tier)

    return {
        "status": "ingested",
        "id": commit_result.get("id"),
        "layer": layer,
        "trust_tier": str(caller_tier),
        "validation_state": commit_result.get("validation_state"),
        "source_eidolon": provenance.author_agent,
        "artifact_kind": (envelope.get("artifact", {}) or {}).get("kind"),
        "envelope_version": str(envelope.get("envelope_version", "")),
        "commit_status": commit_result.get("status"),
    }


# Identity- and trust-bearing fields that an update patch must never overwrite
# (else a low-trust caller could launder a crystal's tier/visibility). The new
# revision inherits these from the existing record. See _handle_update.
_UPDATE_PROTECTED_FIELDS = frozenset(
    {"id", "layer", "trust_tier", "validation_state", "provenance", "protected", "status"}
)


def _handle_update(
    args: dict[str, Any],
    episodic: EpisodicLayer,
    semantic: SemanticLayer,
    procedural: ProceduralLayer,
    execution: ExecutionLayer,
    enforcement: Enforcement,
    relational: "RelationalStore",
    caller_tier: Tier,
) -> dict[str, Any]:
    """Handle crystalium.update — bi-temporal update on the appropriate layer.

    Enforcement order (spec.yaml §tool_surface crystalium.update):
      1. assert_rate_limit (universal, already called in _call_tool)
      2. assert_tier_allowed(op='commit') — same tier rules as commit
      3. fetch_existing via relational.get_crystal(id)
      4. dispatch to layer adapter's update() method
      5. update() calls relational.mark_superseded(old_id, new_id, now)
         then inserts new revision with t_valid_from=now

    Layer dispatch by existing crystal's layer field.
    Falls back to a safe relational-level update for Episodic and Execution
    layers that do not yet expose a standalone update() method.
    """
    crystal_id = args.get("id", "")
    patch = args.get("patch", {})
    reason = args.get("reason", "")

    if not crystal_id:
        from crystalium.enforcement import CrystaliumEnforcementError
        raise CrystaliumEnforcementError(
            "crystalium.update requires a non-empty 'id'.",
            reason_code="MISSING_CRYSTAL_ID",
        )

    enforcement.assert_rate_limit()

    # Fetch the existing crystal to determine its layer
    existing = relational.get_crystal(crystal_id)
    if existing is None:
        from crystalium.enforcement import CrystaliumEnforcementError
        raise CrystaliumEnforcementError(
            f"Crystal not found: {crystal_id!r}",
            reason_code="CRYSTAL_NOT_FOUND",
        )

    # W2 outcome event: an outcome-only patch records utility.outcome_success_score
    # IN PLACE (a fact about the existing crystal — not a bi-temporal supersession).
    # Feeds EVB's Gain term + promotion precision; EVB refreshes on the next
    # recall/Dream recompute.
    if "outcome_success_score" in patch and set(patch.keys()) <= {"outcome_success_score"}:
        import datetime as _dt

        from crystalium.enforcement import CrystaliumEnforcementError

        # Battle-test fix (HIGH): outcome_success_score feeds EVB's Gain term, which
        # assumes the value lives in [0, 1]. Validate the bound (and that it is
        # numeric) here — an out-of-range or non-numeric score silently corrupts the
        # promotion-precision signal otherwise.
        try:
            score = float(patch["outcome_success_score"])
        except (TypeError, ValueError) as exc:
            raise CrystaliumEnforcementError(
                "outcome_success_score must be a number in [0, 1].",
                reason_code="INVALID_OUTCOME_SCORE",
            ) from exc
        if not (0.0 <= score <= 1.0):
            raise CrystaliumEnforcementError(
                f"outcome_success_score must be in [0, 1]; got {score}.",
                reason_code="INVALID_OUTCOME_SCORE",
            )
        relational.record_outcome(crystal_id, score, now=_dt.datetime.now(_dt.timezone.utc))
        return {
            "status": "outcome_recorded",
            "id": crystal_id,
            "outcome_success_score": score,
        }

    layer = existing.get("layer", "episodic")
    log.info("update_dispatch", crystal_id=crystal_id, layer=layer, reason=reason)

    if layer == "semantic":
        return semantic.update(
            record_id=crystal_id,
            patch=patch,
            reason=reason,
            caller_tier=caller_tier,
        )
    else:
        # Episodic, Procedural, Execution: relational-level bi-temporal update
        # (mark_superseded + insert new row). Full per-layer update() adapters
        # for non-Semantic are deferred to v0.2.
        import datetime as _dt
        import uuid as _uuid

        # Battle-test fix (CRITICAL — trust-laundering / G1·G2·D7): this fallback
        # previously wrote straight to the relational store with NO tier check, so a
        # T2/T3 caller — who can never *commit* to procedural (G2) or execution (G1) —
        # could freely mutate any existing crystal there. The semantic branch gates via
        # semantic.update(); this branch must apply the same rule. Mirror commit's tier
        # matrix for the target layer.
        enforcement.assert_tier_allowed("crystalium.update", layer, caller_tier, "commit")

        now = _dt.datetime.now(_dt.timezone.utc)
        new_id = str(_uuid.uuid4())

        # Battle-test fix (CRITICAL — field laundering): a free-form patch could set
        # trust_tier / layer / validation_state / protected and launder a low-trust
        # crystal to T0/validated/protected. Strip identity- and trust-bearing fields
        # from the patch so they retain their existing values; only content/metadata
        # fields are mutable via update (matching the layer.commit adapters).
        safe_patch = {k: v for k, v in patch.items() if k not in _UPDATE_PROTECTED_FIELDS}

        # Apply patch to existing record
        new_record = dict(existing)
        new_record.update(safe_patch)
        new_record["id"] = new_id
        new_record["status"] = "active"

        temporal = new_record.get("temporal") or {}
        if isinstance(temporal, str):
            import json as _json
            temporal = _json.loads(temporal)
        temporal["t_valid_from"] = now.isoformat()
        temporal.pop("t_valid_to", None)
        temporal.pop("superseded_by", None)
        new_record["temporal"] = temporal

        # Write new revision first, then invalidate old (bi-temporal, P0-5)
        relational.insert_crystal(new_record)
        relational.mark_superseded(crystal_id, new_id, now)

        # Battle-test fix (recall-after-update gap): re-embed the new revision into the
        # shared vector store. Without this the new revision lives only in the
        # relational/FTS index and is MISSING from dense recall — while
        # recall_active_only excludes the superseded original — so an updated fact
        # silently vanished from recall. Best-effort; null/SKIP_SLOW embedder -> no-op.
        _vstore = getattr(episodic, "vector_store", None)
        if _vstore is not None:
            try:
                _text = new_record.get("summary") or ""
                _vec = _vstore.embed(_text)
                if _vec:
                    _vstore.upsert(crystal_id=new_id, vector=_vec, metadata={"layer": layer})
            except Exception as _exc:  # noqa: BLE001
                log.warning("update_vector_reindex_skipped", crystal_id=new_id, error=str(_exc))

        return {
            "status": "updated",
            "id": new_id,
            "supersedes": crystal_id,
            "layer": layer,
            "patch_keys": list(safe_patch.keys()),
            "reason": reason,
        }


def _handle_skill_invoke(
    args: dict[str, Any],
    procedural: ProceduralLayer,
    enforcement: Enforcement,
    config: Config,
    caller_tier: Tier,
) -> dict[str, Any]:
    """Handle crystalium.skill_invoke (G3 sandbox contract).

    Enforcement order:
      1. assert_rate_limit (universal — already called in _call_tool)
      2. assert_no_path_escape(workdir, /sandbox/<skill_id>)
      3. warn_skill_invoke (operator warning, D5)
      4. subprocess.run(timeout=timeout_s)
      5. cap_output(output_cap_bytes)
      6. return result dict
    """
    skill_id = args.get("skill_id", "")
    invoke_args = args.get("args", {})
    timeout_s = min(int(args.get("timeout_s", config.skill_invoke_timeout_s)), 30)
    output_cap_bytes = min(
        int(args.get("output_cap_bytes", config.skill_invoke_output_cap_bytes)), 8192
    )
    sandbox_root_str = str(config.sandbox_root)
    workdir_str = args.get("workdir", f"{sandbox_root_str}/{skill_id}")

    # Enforce workdir is under <sandbox_root>/<skill_id> (string prefix check for non-existent paths)
    workdir = Path(workdir_str)
    sandbox_root = config.sandbox_root / skill_id
    resolved_str = str(workdir_str)
    if not resolved_str.startswith(f"{sandbox_root_str}/{skill_id}"):
        from crystalium.enforcement import PathEscape
        raise PathEscape(workdir, sandbox_root)

    skill_result = procedural.invoke(
        skill_id=skill_id,
        args=invoke_args,
        caller_tier=caller_tier,
        timeout_s=timeout_s,
        output_cap_bytes=output_cap_bytes,
        workdir=workdir,
    )

    return {
        "skill_id": skill_id,
        "exit_code": skill_result.exit_code,
        "stdout": skill_result.stdout.decode("utf-8", errors="replace"),
        "stderr": skill_result.stderr.decode("utf-8", errors="replace"),
        "overflow_flag": skill_result.overflow_flag,
        "passed": skill_result.passed,
    }


def _handle_session_end(
    args: dict[str, Any],
    scheduler: DreamScheduler,
) -> dict[str, Any]:
    """Handle crystalium.session_end — enqueue Dream run (G8, D3).

    Returns {enqueued: bool, dream_run_id: str|null}.
    """
    reason = args.get("reason", "")
    log.info("session_end_called", reason=reason)
    run_id = scheduler.on_session_end()
    return {
        "enqueued": run_id is not None,
        "dream_run_id": run_id,
    }
