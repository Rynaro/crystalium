"""MCP server wiring for CRYSTALIUM.

Translates between the MCP protocol (tools/list, tools/call) and the
CRYSTALIUM tool implementations. Every call passes through the enforcement
chokepoint before any storage layer is touched.

Design anchors:
  D2: stdio is the default transport; Streamable-HTTP (run_http) added in v0.2.
      Both transports share _build_server + the tools/call dispatch (mcp 2.x
      Server.add_request_handler; crystalium#39, was the @server.call_tool()
      decorator pre-v1.12.0).
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

import importlib.metadata
import json
import time
import uuid
from pathlib import Path
from typing import Any

import jsonschema
import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

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
from crystalium.export.graph_export import ExportFlags, GraphExporter
from crystalium.gate import PromotionGate
from crystalium.evb import make_evb_scorer
from crystalium.importance import importance_score
from crystalium.ingest_adapter import _HOST_EIDOLONS, _ROSTER_EIDOLONS, source_for_tier
from crystalium.layers.episodic import EpisodicLayer
from crystalium.layers.execution import ExecutionLayer
from crystalium.layers.procedural import ProceduralLayer
from crystalium.layers.semantic import SemanticLayer
from crystalium.quality import POOR_SUMMARY_ADVICE, is_poor_summary
from crystalium.schemas import Provenance, Scope
from crystalium.scope import canonical_project_key, default_recall_project, normalize_write_scope
from crystalium.storage.blob import BlobStore
from crystalium.storage.graph import GraphStore
from crystalium.storage.relational import RelationalStore
from crystalium.storage.vector import VectorStore
from crystalium.telemetry import (
    emit_latency_panel,
    record_call,
    register_relational_store,
    tool_span,
)
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


def _eidolon_identity_tier(eidolon: str) -> Tier:
    """Map a process-level eidolon name to its trust tier.

    Reuses the same roster sets as ingest_adapter (single source of truth):
      _HOST_EIDOLONS  -> T0 (host LLM / cortex)
      _ROSTER_EIDOLONS -> T1 (verified Eidolon agent)
      anything else   -> T3 (unknown / tool-origin; most conservative)
    """
    key = eidolon.strip().lower()
    if key in _HOST_EIDOLONS:
        return Tier.T0
    if key in _ROSTER_EIDOLONS:
        return Tier.T1
    return Tier.T3


def _extract_caller_identity() -> dict[str, Any]:
    """Extract caller identity from process-level env vars (v1.2.0).

    Reads CRYSTALIUM_CALLER_EIDOLON and CRYSTALIUM_CALLER_TIER to resolve the
    process-level trust tier for non-ingest MCP calls. All six Eidolons share
    one server process, so identity is correctly a process-level env var.

    Resolution order (MIN-trust: higher Tier int = less trust wins):
      1. If CRYSTALIUM_CALLER_EIDOLON is set, map it through the roster sets
         imported from ingest_adapter (_ROSTER_EIDOLONS / _HOST_EIDOLONS) to
         get identity_tier (T0/T1/T3).
      2. If CRYSTALIUM_CALLER_TIER is set, parse it via Tier.from_str() to get
         declared_tier.
      3. final_tier = max(declared_tier, identity_tier)  — MIN-trust convention.
      4. If neither env var is set, fall back to the D4 conservative T2 default
         (backward-compatible with pre-v1.2.0 deployments).

    OQ-3 (original): env-var identity is the v1.2 answer; per-request SDK
    headers remain future work.
    """
    import os

    raw_eidolon = os.environ.get("CRYSTALIUM_CALLER_EIDOLON")
    raw_tier = os.environ.get("CRYSTALIUM_CALLER_TIER")

    if raw_eidolon is None and raw_tier is None:
        # Neither set → preserve backward-compatible T2 default (D4 conservative).
        return dict(_DEFAULT_CALLER)

    # Determine identity_tier from eidolon name (if provided).
    if raw_eidolon is not None:
        identity_tier: Tier = _eidolon_identity_tier(raw_eidolon)
    else:
        # No eidolon name; identity alone doesn't constrain — start at T0 (most
        # trusted) so the declared tier below drives the result.
        identity_tier = Tier.T0

    # Determine declared_tier from explicit tier override (if provided).
    if raw_tier is not None:
        try:
            declared_tier: Tier = Tier.from_str(raw_tier)
        except ValueError:
            # Unparseable tier string → fall back to identity tier (conservative).
            declared_tier = identity_tier
    else:
        # No explicit tier override → identity alone decides.
        declared_tier = identity_tier

    # MIN-trust: final tier is the LESS trusted (higher int) of the two.
    final_tier = max(declared_tier, identity_tier)

    eidolon_name = raw_eidolon if raw_eidolon is not None else _DEFAULT_CALLER["eidolon"]
    return {
        "eidolon": eidolon_name,
        "version": _DEFAULT_CALLER["version"],
        "tier": str(final_tier),
    }


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
    """Return the nine tool descriptors served via tools/list.

    Descriptions include enforcement bounds per spec.yaml §tool_surface.
    """
    return [
        {
            "name": "recall",
            "description": (
                "Hybrid BM25 + dense + graph recall from CRYSTALIUM memory. "
                "Returns slot-budgeted, redacted CrystalSummary records. "
                "Universally allowed (all tiers). "
                "Rate-limited (200 calls/min). "
                "explain=true (v1.6) adds a diagnostic `explain` object to the "
                "result so a zero-record recall against a non-empty store is "
                "diagnosable without a separate `doctor` call. "
                "v1.9.0 (crystalium#36): query relevance is the primary composition "
                "signal — `k` is a hard cap on the number of returned records, and "
                "records are emitted in non-increasing `score` order (id ascending "
                "tiebreak). Every record carries a non-null `score` and the response "
                "always carries a `budget` object (total_cap, slots, k_requested, "
                "k_applied, truncated_count). Revertible to the pre-1.9.0 behaviour "
                "via Config.recall_relevance_primary=false. "
                "v1.10.0 (crystalium#38): `score` is now a WEIGHTED hybrid-retrieval "
                "RRF fusion value (previously unweighted) — ordering semantics are "
                "unchanged (still non-increasing, id-ascending tiebreak) but the "
                "magnitude and the relative order among candidates can differ from "
                "1.9.0. The graph and completion arms are collapsed into one "
                "correlated derived voter and the sparse arm receives a "
                "query-conditional selectivity boost; explain=true additionally "
                "surfaces a `fusion` object (per-arm weights + the selectivity "
                "inputs that produced them). Revertible via "
                "Config.recall_weighted_fusion=false (subsumed under "
                "recall_relevance_primary — either flag off restores the "
                "unweighted fusion)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["scope", "query"],
                "properties": {
                    "scope": {
                        "type": "object",
                        "description": (
                            "Scope filter: {project, agent_class_visibility, "
                            "sensitivity_tag}. project defaults to the canonical "
                            "(data-dir-derived) project key when omitted."
                        ),
                    },
                    "query": {"type": "string", "description": "Free-text recall query"},
                    "k": {
                        "type": "integer",
                        "default": 10,
                        "description": (
                            "Max records to return (hard cap when relevance-primary "
                            "composition is active, the default). Clamped to [1, 100]; "
                            "a non-numeric/non-coercible value falls back to 10."
                        ),
                    },
                    "layers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["episodic", "semantic", "procedural", "execution"]},
                        "description": "Subset of layers to search (default: all four)",
                    },
                    "explain": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "v1.6: attach a diagnostic `explain` object (candidate/filter "
                            "counts, arm status, store totals, distinct project keys "
                            "present) to the result. Bypasses the recall cache."
                        ),
                    },
                },
            },
        },
        {
            "name": "commit",
            "description": (
                "Commit a crystal to a memory layer. "
                "T3 callers: Episodic-quarantine only (G1). "
                "T2: Episodic or Procedural-candidate only (G1, G2). "
                "T1/T0: Semantic, Procedural, Execution allowed (with gate checks G4, G5). "
                "Bi-temporal: write-new, never hard-delete (P0-5). "
                "v1.6: scope.project is normalized to the canonical (data-dir-derived) "
                "project key (advisory: scope_normalized=true); summary is checked "
                "against a mechanical quality gate (advisory: summary_quality='poor')."
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
                        "description": (
                            "Provenance dict: {source, author_agent, task_id, created_at}. "
                            "source must be one of: "
                            + ", ".join(sorted(_VALID_PROVENANCE_SOURCES))
                            + " — an out-of-enum value is COERCED to a value derived "
                            "from the caller's trust tier (never 'human' for a non-T0 "
                            "caller) rather than rejected, with a `provenance_coercion` "
                            "advisory attached to the result."
                        ),
                    },
                },
            },
        },
        {
            "name": "ingest",
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
            "name": "update",
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
            "name": "skill_invoke",
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
            "name": "plan_checkpoint",
            "description": (
                "Write a TTL-bound plan checkpoint to the Execution layer. "
                "T0/T1 only (G1: T2/T3 blocked). "
                "Expires after execution TTL (default 24h). "
                "v1.6: scope.project normalized to the canonical project key; a "
                "missing/poor summary is auto-enriched (plan + scope + phase) "
                "instead of the bare 'plan_checkpoint:<id>' label; the last "
                "active checkpoint for a plan is never auto-deprecated by Dream."
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
            "name": "plan_replan",
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
            "name": "session_end",
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
        {
            "name": "graph_export",
            "description": (
                "Export the scoped memory graph as nodes[]+edges[] "
                "(JSON canonical, or graphml/cytoscape adapter). "
                "Nodes = redacted crystal summaries (never raw blob); "
                "edges = LINKS_TO (kuzu) + derived SUPERSEDES/MERGED_FROM/CONFLICTS_WITH. "
                "Read-only; bounded (limit<=10000); universally allowed (read op). "
                "Rate-limited (200 calls/min)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["scope"],
                "properties": {
                    "scope": {
                        "type": "object",
                        "description": "{project, agent_class_visibility, sensitivity_tag}",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["json", "graphml", "cytoscape"],
                        "default": "json",
                    },
                    "layers": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["episodic", "semantic", "procedural", "execution"],
                        },
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5000,
                        "description": "Node cap (clamped to 10000)",
                    },
                    "include": {
                        "type": "object",
                        "description": (
                            "Optional override flags (§6.4): include_quarantined, "
                            "include_deprecated, include_superseded, all_visibility, "
                            "include_content_ref, include_drift, synthesize_links, dangling_policy"
                        ),
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
    composer = Composer(config, recall_relevance_primary=config.recall_relevance_primary)

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

    # crystalium#43: link_cooccurrence is decoupled from recall_completion so a
    # caller (e.g. evals/retrieval_gate.py) can pin write-time graph edges OFF
    # without also disabling the recall-time completion faculty. None (the
    # default) preserves today's wiring exactly.
    link_cooccurrence = (
        config.recall_completion
        if config.link_cooccurrence is None
        else config.link_cooccurrence
    )

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
        link_cooccurrence=link_cooccurrence,
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
        link_cooccurrence=link_cooccurrence,
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
        recall_relevance_primary=config.recall_relevance_primary,
        recall_weighted_fusion=config.recall_weighted_fusion,
        fusion_weight_dense=config.fusion_weight_dense,
        fusion_weight_derived=config.fusion_weight_derived,
        fusion_sparse_boost_alpha=config.fusion_sparse_boost_alpha,
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

    # v1.6 `recall --explain` (item 3/4): marks this as a stub so the dense arm
    # reports "inactive(null_vector_store)" instead of silently returning
    # nothing with no visible reason — the MOTIVATING INCIDENT's root cause #4
    # (embedding_ref null on every crystal because deps were absent).
    _is_null = True

    def embed(self, text: str) -> list[float]:
        return []

    def dense_search(self, query_vec: list[float], layer_filter: str, k: int) -> list[dict]:
        return []


class _NullGraphStore:
    """No-op GraphStore — used when KuzuDB unavailable."""

    # v1.6 `recall --explain` / `doctor` (item 3/4): see _NullVectorStore._is_null.
    _is_null = True

    def neighbor_expand(self, seed_ids: list[str], depth: int = 1) -> list[str]:
        return []

    def decaying_walk(self, seed_ids: list[str], max_hops: int = 2, decay: float = 0.5) -> dict:
        return {}

    def add_node(self, *args, **kwargs) -> None:  # noqa: ARG002 — W5 edge writes no-op
        return None

    def add_edge(self, *args, **kwargs) -> None:  # noqa: ARG002 — W5 edge writes no-op
        return None

    def all_edges(self, **kwargs) -> list:  # noqa: ARG002 — GAP-2 no-kuzu fallback
        return []


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
    """Build the MCP Server with all 9 tools wired, plus its DreamScheduler.

    Transport-agnostic: shared verbatim by both run_stdio and run_http (D2,
    v0.2). The caller is responsible for starting the scheduler and running the
    chosen transport. Components (_build_components) and the tools/call
    dispatch (registered via Server.add_request_handler, crystalium#39) are
    reused as-is across transports.
    """
    # v1.6: pass version=__version__ explicitly. Without it, mcp.server.lowlevel
    # .Server.create_initialization_options() falls back to the installed `mcp`
    # SDK package's own version for serverInfo — silently reporting the wrong
    # component's version to MCP clients, not even the stale crystalium literal.
    server = Server("crystalium", version=__version__)

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

    # G1.3: wire the audit table so telemetry.record_call() writes each tool
    # call into tool_calls — DreamWorker._orient() can then read real counts.
    register_relational_store(relational)

    # W-GE5: build the graph exporter once; shared by the MCP dispatch handler.
    # graph_store is not returned from _build_components but is stored on aetheryte.
    exporter = GraphExporter(relational_store=relational, graph_store=aetheryte.graph_store)

    # crystalium#39: mcp 2.x's Server.add_request_handler validates params
    # BEFORE the handler runs but only against the RPC envelope shape
    # (CallToolRequestParams: name/arguments) — it does NOT replay the 1.x
    # @server.call_tool(validate_input=True) decorator's per-tool jsonschema
    # check against each tool's own advertised inputSchema. That check is
    # replicated below so a schema-violating call keeps its 1.x wire shape
    # (isError=true, "Input validation error: <message>") byte-identically.
    _tool_schemas: dict[str, dict[str, Any]] = {
        t["name"]: t["inputSchema"] for t in build_tool_manifest()
    }

    async def _list_tools(
        _ctx: Any, _params: PaginatedRequestParams
    ) -> ListToolsResult:
        return ListToolsResult(tools=[Tool(**t) for t in build_tool_manifest()])

    async def _call_tool(_ctx: Any, params: CallToolRequestParams) -> CallToolResult:
        name = params.name
        arguments = params.arguments or {}

        schema = _tool_schemas.get(name)
        if schema is not None:
            try:
                jsonschema.validate(instance=arguments, schema=schema)
            except jsonschema.ValidationError as exc:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Input validation error: {exc.message}")],
                    is_error=True,
                )

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
                if name == "recall":
                    result = _handle_recall(arguments, aetheryte, scheduler, caller_tier, config)
                    scheduler.record_activity()
                    result_bytes = json.dumps(
                        result if isinstance(result, dict) else result.model_dump(),
                        default=str,
                    ).encode()
                    artifact_kind = "recall-result"

                elif name == "commit":
                    layer_hint = arguments.get("layer")
                    result = _handle_commit(
                        arguments, episodic, semantic, procedural, execution, caller_tier, config,
                        recall_cache=execution.recall_cache,
                        composer=aetheryte.composer,
                    )
                    scheduler.record_activity()
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "commit-result"

                elif name == "ingest":
                    result = _handle_ingest(
                        arguments, episodic, semantic, procedural, execution, config,
                        recall_cache=execution.recall_cache,
                    )
                    layer_hint = result.get("layer")
                    scheduler.record_activity()
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "ingest-receipt"

                elif name == "update":
                    layer_hint = arguments.get("layer")
                    result = _handle_update(arguments, episodic, semantic, procedural, execution, enforcement, relational, caller_tier)
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "commit-result"

                elif name == "skill_invoke":
                    layer_hint = "procedural"
                    result = _handle_skill_invoke(arguments, procedural, enforcement, config, caller_tier)
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "skill-result"

                elif name == "plan_checkpoint":
                    layer_hint = "execution"
                    result = execution.checkpoint(
                        state=arguments.get("state", {}),
                        caller_tier=caller_tier,
                    )
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "plan-checkpoint"

                elif name == "plan_replan":
                    layer_hint = "execution"
                    result = execution.replan(
                        diff=arguments.get("diff", {}),
                        caller_tier=caller_tier,
                    )
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "plan-replan"

                elif name == "session_end":
                    result = _handle_session_end(arguments, scheduler)
                    result_bytes = json.dumps(result, default=str).encode()
                    artifact_kind = "session-end-receipt"
                    performative = "ACKNOWLEDGE"
                    # Surface the recall/commit/forget/dream latency panels +
                    # recall p95 (W8 SLO) at the close of the session.
                    emit_latency_panel()

                elif name == "graph_export":
                    # W-GE5: read op — universally allowed; inherits assert_rate_limit above.
                    # NO assert_tier_allowed for a commit (never writes crystals).
                    result = _handle_graph_export(arguments, exporter, aetheryte.redactor, caller_tier)
                    fmt = arguments.get("format", "json")
                    if fmt == "graphml":
                        from crystalium.export.adapters import to_graphml
                        payload_str = to_graphml(result)
                        result_bytes = payload_str.encode()
                    elif fmt == "cytoscape":
                        from crystalium.export.adapters import to_cytoscape
                        result_bytes = json.dumps(
                            to_cytoscape(result), sort_keys=True, default=str
                        ).encode()
                    else:
                        result_bytes = json.dumps(
                            result, sort_keys=True, default=str
                        ).encode()
                    artifact_kind = "graph-export"

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
            return CallToolResult(content=[TextContent(type="text", text=result_bytes.decode())])

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
            # v2.0.0 (crystalium#35, OUT OF SCOPE for #39): crystalium's own error
            # path deliberately does NOT set is_error=True here — today it returns
            # the error as ordinary content (isError: false). Preserved byte-for-
            # byte across the SDK migration; fixing this is a separate, later change.
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(err_payload, indent=2))])

    server.add_request_handler("tools/list", PaginatedRequestParams, _list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, _call_tool)

    return server, scheduler


def _resolved_mcp_sdk_version() -> str:
    """Return the installed `mcp` SDK distribution version (crystalium#39).

    Reads package METADATA via importlib.metadata rather than `mcp.__version__`
    — the SDK does not reliably expose that attribute at runtime, and METADATA
    is also what a cached image can silently disagree with source (the v1.9.0
    "cached images hide dependency breaks" lesson): this is the env-skew
    assertion that fails loudly next to server_starting instead of quietly
    running the old SDK's codepath.
    """
    return importlib.metadata.version("mcp")


async def run_stdio(config: Config) -> None:
    """Start the CRYSTALIUM MCP server over stdio (the default transport)."""
    server, scheduler = _build_server(config)
    scheduler.start()
    log.info(
        "server_starting",
        transport="stdio",
        data_dir=str(config.data_dir),
        version=__version__,
        mcp_sdk_version=_resolved_mcp_sdk_version(),
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
    tools/call dispatch, so the ECL v2.0 sidecar is emitted over HTTP too.
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
        mcp_sdk_version=_resolved_mcp_sdk_version(),
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


#: DP-3d / C-11 (crystalium#36): a numeric k is clamped into [1, 100]; a
#: non-coercible k (e.g. a garbage string) falls back to _K_DEFAULT rather than
#: raising — recall is a read path and must degrade, not reject. Single source
#: of truth for BOTH entry points: server._handle_recall AND the CLI `recall`
#: verb (__main__.py imports normalize_k from here).
_K_DEFAULT: int = 10
_K_MIN: int = 1
_K_MAX: int = 100


def normalize_k(raw: Any, default: int = _K_DEFAULT) -> int:
    """Clamp a caller-supplied `k` into [1, 100]; degrade to *default* on garbage.

    Replaces the pre-1.9.0 `max(0, int(k))` pattern, which let `k=0` through and
    — once `k` became a hard cap on the number of returned records (crystalium#36
    seam 3) — would have silently returned zero records for that input, a bug the
    fix would have introduced (DP-3d).
    """
    try:
        k = int(raw)
    except (TypeError, ValueError):
        return default
    return max(_K_MIN, min(_K_MAX, k))


def _handle_recall(
    args: dict[str, Any],
    aetheryte: Aetheryte,
    scheduler: DreamScheduler,
    caller_tier: Tier,
    config: Config,
) -> Any:
    """Handle crystalium.recall."""
    raw_scope = args.get("scope", {})
    # v1.6: an omitted scope.project defaults to the canonical (data-dir-derived)
    # project key, not the literal string "default". An EXPLICIT scope.project is
    # never rewritten here — recall is a read filter, not a write of record, and
    # legacy/fragmented project keys must stay queryable (recall --explain
    # diagnoses that fragmentation instead of papering over it).
    canonical_project = canonical_project_key(config.data_dir)
    scope = Scope(
        project=default_recall_project(raw_scope, canonical_project),
        agent_class_visibility=raw_scope.get("agent_class_visibility"),
        sensitivity_tag=raw_scope.get("sensitivity_tag"),
    )
    query = args.get("query", "")
    # crystalium#36 / DP-3d / C-11: clamp to [1,100]; non-coercible -> default 10.
    k = normalize_k(args.get("k", _K_DEFAULT))
    layers = args.get("layers")
    explain = bool(args.get("explain", False))

    result = aetheryte.recall(
        scope=scope,
        query=query,
        k=k,
        layers=layers,
        caller_tier=caller_tier,
        explain=explain,
    )
    # exclude_none: `explain` is the only Optional field on RecallResult, so a
    # non-explain call's JSON shape is byte-identical to pre-v1.6.
    return result.model_dump(exclude_none=True) if hasattr(result, "model_dump") else result


# ---------------------------------------------------------------------------
# S1: provenance.source coercion helper
# ---------------------------------------------------------------------------

_VALID_PROVENANCE_SOURCES: frozenset[str] = frozenset(
    {"human", "verified_agent", "unverified_agent", "environment"}
)


def _coerce_provenance_source(
    raw: Any, caller_tier: "Tier"
) -> "tuple[str, bool]":
    """Coerce a raw provenance.source value to a valid trust-class enum string.

    Contract (total, deterministic):
      (a) IDENTITY  — if raw is a str and raw in VALID_SOURCES → return (raw, False)
          Byte-for-byte passthrough; no advisory attached (C-3 witness-independence).
      (b) FALLBACK  — else (None | '' | any other str | non-str) →
          return (source_for_tier(caller_tier), True)
      (c) INVARIANT — source_for_tier yields 'human' ONLY for Tier.T0, so a non-T0
          caller can never be coerced to 'human' (C-2 / RISK-2 / protection.py:54-58).
    """
    if isinstance(raw, str) and raw in _VALID_PROVENANCE_SOURCES:
        return (raw, False)
    return (source_for_tier(caller_tier), True)


def _handle_commit(
    args: dict[str, Any],
    episodic: EpisodicLayer,
    semantic: SemanticLayer,
    procedural: ProceduralLayer,
    execution: ExecutionLayer,
    caller_tier: Tier,
    config: Config,
    recall_cache: Any = None,
    composer: Any = None,
) -> dict[str, Any]:
    """Dispatch crystalium.commit to the correct layer adapter.

    *composer* (crystalium#36 / D5, optional — the oversized-summary advisory
    is skipped entirely when None, e.g. in tests that drive this handler with
    mocked layers and no full component graph) supplies the module-hardened
    tokenizer (composer.py, post-#32) for the oversized-summary advisory below.
    """
    import datetime as _dt
    layer = args.get("layer", "episodic")
    payload = args.get("payload", {})
    raw_prov = args.get("provenance", {})
    now_utc = _dt.datetime.now(_dt.timezone.utc)

    # v1.6 item 1: canonical project-key normalization (write path). Mutates a
    # COPY of payload/scope — the caller's dict is never mutated in place.
    canonical_project = canonical_project_key(config.data_dir)
    raw_scope = payload.get("scope") if isinstance(payload, dict) else None
    normalized_scope, scope_normalized = normalize_write_scope(raw_scope, canonical_project)
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["scope"] = normalized_scope

    # v1.6 item 2: mechanical summary-quality gate (soft — advisory only, never
    # blocks the write). Assessed on the caller-supplied summary at the
    # crystalium.commit boundary; per-layer fallback summaries (e.g. a
    # truncated payload repr) are inherently poor and correctly flagged too
    # once persisted, but this check runs on the pre-fallback value so the
    # advisory reflects what the caller actually supplied.
    summary_poor = is_poor_summary(payload.get("summary") if isinstance(payload, dict) else None)

    # S2 (GAP-1): tolerant created_at parse — mirrors the ingest path (server.py:1055-1061).
    # Handles: Z-suffixed ISO, epoch int/float, non-ISO garbage. Never crashes.
    created_at_raw = raw_prov.get("created_at")
    created_at_coerced = False
    if created_at_raw is None:
        created_at: _dt.datetime = now_utc
    elif isinstance(created_at_raw, (int, float)):
        try:
            created_at = _dt.datetime.fromtimestamp(created_at_raw, tz=_dt.timezone.utc)
        except (ValueError, OSError, OverflowError):
            created_at = now_utc
            created_at_coerced = True
    elif isinstance(created_at_raw, str):
        try:
            created_at = _dt.datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError:
            created_at = now_utc
            created_at_coerced = True
    else:
        created_at = now_utc
        created_at_coerced = True

    # S1: coerce provenance.source to a valid trust-class enum value.
    # IDENTITY passthrough for already-valid values preserves byte-equality (C-3).
    # FALLBACK via source_for_tier ensures non-T0 callers never get 'human' (C-2).
    raw_source = raw_prov.get("source")
    final_source, source_coerced = _coerce_provenance_source(raw_source, caller_tier)

    provenance = Provenance(
        source=final_source,
        author_agent=raw_prov.get("author_agent"),
        task_id=raw_prov.get("task_id"),
        created_at=created_at,
    )

    # W5: a write into a project invalidates that project's cached recalls
    # (staleness guard). No-op when prefetch is off (recall_cache is None).
    if recall_cache is not None:
        project = (payload.get("scope") or {}).get("project") if isinstance(payload, dict) else None
        if project:
            recall_cache.invalidate_project(project)

    if layer == "episodic":
        result = episodic.commit(payload=payload, provenance=provenance, caller_tier=caller_tier)
    elif layer == "semantic":
        result = semantic.commit(payload=payload, provenance=provenance, caller_tier=caller_tier)
    elif layer == "procedural":
        result = procedural.commit(payload=payload, provenance=provenance, caller_tier=caller_tier)
    elif layer == "execution":
        # execution.checkpoint() does its own scope/summary normalization (it is
        # also reachable directly via the crystalium.plan_checkpoint tool, which
        # bypasses this handler entirely) — by the time it runs here payload.scope
        # is already canonical, so that pass is a no-op; scope_normalized below
        # reflects the (real) normalization already performed above.
        result = execution.checkpoint(state=payload, caller_tier=caller_tier)
        if scope_normalized:
            result = dict(result)
            result["scope_normalized"] = True
        return result
    else:
        from crystalium.enforcement import CrystaliumEnforcementError
        raise CrystaliumEnforcementError(
            f"Unknown layer {layer!r}. Must be one of: episodic, semantic, procedural, execution.",
            reason_code="UNKNOWN_LAYER",
        )

    # S3 (DECISION-1 / G-ADVISORY-OBSERVABLE): attach provenance_coercion advisory
    # to the SUCCESS result ONLY when coercion actually fired. On the clean/IDENTITY
    # path no field is added — the result dict is byte-identical to today (RISK-5).
    coercions = []
    if source_coerced:
        coercions.append({"field": "source", "from": raw_source, "to": final_source})
    if created_at_coerced:
        coercions.append({"field": "created_at", "from": created_at_raw, "to": "server_now"})
    if coercions:
        result = dict(result)  # shallow copy — do not mutate the layer's return value
        result["provenance_coercion"] = coercions[0] if len(coercions) == 1 else coercions

    # v1.6 items 1 + 2: additive advisories, byte-identical result on the clean path.
    if scope_normalized:
        result = dict(result)
        result["scope_normalized"] = True
    if summary_poor:
        result = dict(result)
        result["summary_quality"] = "poor"
        result["advisory"] = POOR_SUMMARY_ADVICE

    # crystalium#36 / D5 (DP-8): advisory-only — the summary tokenises above its
    # destination layer's slot cap, so the composer may evict this crystal on
    # size alone at a future recall. Layer-aware (the FULL slot cap, not a
    # fraction — a fractional threshold would fire routinely and break the
    # clean-path byte-identity test_diagnosability.py locks in). NEVER a
    # rejection (the reporter proved shrinking the summary did not restore
    # retrievability — size is a hint, not the cause). C-4: any tokenizer
    # exception skips the advisory silently and never fails/delays the commit.
    if composer is not None:
        try:
            raw_summary_for_size = payload.get("summary") if isinstance(payload, dict) else None
            if isinstance(raw_summary_for_size, str) and raw_summary_for_size:
                summary_tokens = composer.tokenize(raw_summary_for_size)
                slot_cap = config.slots.get(layer)
                if slot_cap is not None and summary_tokens > slot_cap:
                    result = dict(result)
                    result["summary_size"] = "oversized"
                    result["summary_tokens"] = summary_tokens
                    result["slot_cap"] = slot_cap
                    oversized_advice = (
                        f"summary tokenises to {summary_tokens} tokens, above the "
                        f"'{layer}' slot cap of {slot_cap} — a future recall's "
                        "composer may evict this crystal on size alone. Advisory "
                        "only; the commit is not rejected."
                    )
                    # Don't clobber a co-occurring summary_quality advisory (rare in
                    # practice — a "poor" summary is short, an "oversized" one is
                    # not — but never silently drop one message for the other).
                    existing_advisory = result.get("advisory")
                    result["advisory"] = (
                        f"{existing_advisory} {oversized_advice}"
                        if existing_advisory
                        else oversized_advice
                    )
        except Exception as exc:  # noqa: BLE001 — C-4: never fail/delay the commit
            log.debug("oversized_summary_check_skipped", error=str(exc))

    return result


def _handle_ingest(
    args: dict[str, Any],
    episodic: EpisodicLayer,
    semantic: SemanticLayer,
    procedural: ProceduralLayer,
    execution: ExecutionLayer,
    config: Config,
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

    # v1.6 item 1: canonical project-key normalization (write path). The generic
    # adapter derives scope.project from thread_id/eidolon (ingest_adapter.py) —
    # exactly the kind of free-typed key the MOTIVATING INCIDENT fragmented on.
    canonical_project = canonical_project_key(config.data_dir)
    normalized_scope, scope_normalized = normalize_write_scope(
        crystal_payload.get("scope"), canonical_project
    )
    crystal_payload["scope"] = normalized_scope

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

    result = {
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
    # v1.6 item 1: additive advisory, byte-identical result on the clean path.
    if scope_normalized:
        result["scope_normalized"] = True
    return result


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


def _handle_graph_export(
    args: dict[str, Any],
    exporter: "GraphExporter",
    redactor: "Redactor",
    caller_tier: "Tier",
) -> dict[str, Any]:
    """Handle crystalium.graph_export (W-GE5, spec §8.2).

    Read-only; universally allowed; ECL envelope auto-inherited via
    _emit_ecl_sidecar in the dispatch loop.

    Steps (mirroring _handle_recall):
      1. Parse scope from arguments.
      2. Clamp limit to [0, 10000].
      3. Build ExportFlags from arguments.get("include", {}).
      4. Call exporter.export(...) — the one canonical core (G-GE6 parity).
      5. Return the canonical dict (format adaptation is done in the dispatch
         branch, not here, so the ECL payload hashes the actually-returned bytes).
    """
    from crystalium.storage.relational import MAX_EXPORT_NODES

    raw_scope = args.get("scope", {})
    scope = {
        "project": raw_scope.get("project", "default"),
        "agent_class_visibility": raw_scope.get("agent_class_visibility"),
        "sensitivity_tag": raw_scope.get("sensitivity_tag", "none") or "none",
    }

    try:
        limit = max(0, min(int(args.get("limit", 5000)), MAX_EXPORT_NODES))
    except (TypeError, ValueError):
        limit = 5000

    layers = args.get("layers")

    include = args.get("include") or {}
    flags = ExportFlags(
        include_quarantined=bool(include.get("include_quarantined", False)),
        include_deprecated=bool(include.get("include_deprecated", False)),
        include_superseded=bool(include.get("include_superseded", False)),
        all_visibility=bool(include.get("all_visibility", False)),
        include_content_ref=bool(include.get("include_content_ref", False)),
        include_drift=bool(include.get("include_drift", False)),
        synthesize_links=bool(include.get("synthesize_links", False)),
        # backfill_links is NOT exposed on MCP surface (write path is CLI-only)
        backfill_links=False,
        dangling_policy=str(include.get("dangling_policy", "drop")),
    )

    result = exporter.export(
        scope=scope,
        layers=layers,
        limit=limit,
        include_flags=flags,
        redactor=redactor,
    )
    # Inject caller_tier into generated_from for audit provenance
    result["generated_from"]["caller_tier"] = getattr(caller_tier, "name", str(caller_tier))
    return result
