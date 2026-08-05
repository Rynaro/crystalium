"""W4 tests: MCP server wiring.

Tests:
  test_session_end_enqueues_dream           — session_end → DreamScheduler.on_session_end()
  test_transport_http_raises_not_implemented — HTTP transport raises NotImplementedError
  test_record_activity_called_on_commit      — record_activity() called after commit
  test_caller_identity_falls_back_to_unknown_t2 — missing identity → {eidolon:'unknown', tier:T2}

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_server.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call
import uuid

import pytest

from crystalium.config import Config
from crystalium.server import (
    _extract_caller_identity,
    _caller_tier,
    _handle_session_end,
    _handle_commit,
    _DEFAULT_CALLER,
)
from crystalium.trust import Tier


# ---------------------------------------------------------------------------
# crystalium#39 — mcp SDK 2.x env-skew assertion
#
# Deliberately NOT marked slow (`make test-ci` runs SKIP_SLOW=1 WITH slow
# tests still selected, so a `slow`-marked test would still run there — but
# this check has no reason to be slow and belongs in the fast/default lane
# too). Reads installed package METADATA, not `mcp.__version__` (may not
# exist), so a stale venv or a cached image that still resolves mcp 1.x fails
# this loudly instead of silently re-running the removed decorator codepath
# (same lesson as the v1.9.0 cached-image gate).
# ---------------------------------------------------------------------------


def test_mcp_sdk_is_2x() -> None:
    """The resolved `mcp` distribution is on the 2.x major line (crystalium#39)."""
    import importlib.metadata

    resolved = importlib.metadata.version("mcp")
    major = int(resolved.split(".")[0])
    assert major == 2, (
        f"expected mcp SDK major version 2, got {resolved!r} (major={major}) — "
        "a stale venv or cached image is still resolving mcp 1.x"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "crystalium_data",
        transport="stdio",
        rate_limit_per_minute=1000,
    )


@pytest.fixture
def relational_store(tmp_path: Path):
    from crystalium.storage.relational import RelationalStore
    return RelationalStore(db_path=tmp_path / "test.sqlite")


@pytest.fixture
def blob_store(tmp_path: Path):
    from crystalium.storage.blob import BlobStore
    return BlobStore(root=tmp_path / "blobs")


@pytest.fixture
def enforcement(config: Config):
    from crystalium.enforcement import Enforcement
    return Enforcement(config)


# ---------------------------------------------------------------------------
# test_session_end_enqueues_dream
# ---------------------------------------------------------------------------


def test_session_end_enqueues_dream() -> None:
    """session_end calls scheduler.on_session_end() and returns enqueued=True when run_id is set."""
    run_id = str(uuid.uuid4())
    mock_scheduler = MagicMock()
    mock_scheduler.on_session_end.return_value = run_id

    result = _handle_session_end({"reason": "test session end"}, mock_scheduler)

    mock_scheduler.on_session_end.assert_called_once()
    assert result["enqueued"] is True
    assert result["dream_run_id"] == run_id


def test_session_end_enqueued_false_when_gap_not_met() -> None:
    """When scheduler.on_session_end() returns None (gap not met), enqueued=False."""
    mock_scheduler = MagicMock()
    mock_scheduler.on_session_end.return_value = None

    result = _handle_session_end({}, mock_scheduler)

    assert result["enqueued"] is False
    assert result["dream_run_id"] is None


def test_session_end_returns_correct_dict_keys() -> None:
    """session_end result always has 'enqueued' and 'dream_run_id' keys."""
    mock_scheduler = MagicMock()
    mock_scheduler.on_session_end.return_value = str(uuid.uuid4())

    result = _handle_session_end({}, mock_scheduler)
    assert "enqueued" in result
    assert "dream_run_id" in result


def test_session_end_passes_through_run_id() -> None:
    """dream_run_id in result matches what scheduler returns."""
    specific_id = "fixed-run-id-1234"
    mock_scheduler = MagicMock()
    mock_scheduler.on_session_end.return_value = specific_id

    result = _handle_session_end({"reason": "end"}, mock_scheduler)
    assert result["dream_run_id"] == specific_id


# ---------------------------------------------------------------------------
# Streamable-HTTP transport (D2 unstubbed, v0.2). stdio remains the default.
# ---------------------------------------------------------------------------


def _http_config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "http_data",
        transport="http",
        rate_limit_per_minute=1000,
    )


def test_stdio_is_default_transport() -> None:
    """Unsetting CRYSTALIUM_TRANSPORT yields stdio (default unchanged)."""
    assert Config(data_dir=Path("/tmp/x")).transport == "stdio"


def test_http_transport_builds_app(tmp_path: Path) -> None:
    """HTTP no longer raises NotImplementedError: build_http_app wires a Starlette app."""
    from starlette.applications import Starlette

    from crystalium.server import build_http_app

    app, scheduler, session_manager = build_http_app(_http_config(tmp_path))
    assert isinstance(app, Starlette)
    assert scheduler is not None
    assert session_manager is not None


def test_http_smoke_initialize(tmp_path: Path) -> None:
    """Host smoke test: an MCP `initialize` over the ASGI HTTP transport returns
    crystalium serverInfo. Exercises the real Streamable-HTTP request path."""
    from starlette.testclient import TestClient

    from crystalium.server import build_http_app

    cfg = _http_config(tmp_path)
    app, _scheduler, _sm = build_http_app(cfg)

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0.0"},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    # TestClient context enters the app lifespan (session_manager.run()).
    with TestClient(app) as client:
        resp = client.post(cfg.http_path, json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"]["serverInfo"]["name"] == "crystalium", body


def test_http_caller_identity_no_escalation() -> None:
    """[DISPUTED→resolved] Over HTTP the caller identity is NOT trusted/elevated:
    it stays the conservative unknown/T2 default, so enforcement gating is
    unchanged from stdio. HTTP must never grant a higher tier than stdio."""
    caller = _extract_caller_identity()
    assert _caller_tier(caller) == Tier.T2
    assert _caller_tier(_DEFAULT_CALLER) == Tier.T2


# ---------------------------------------------------------------------------
# test_record_activity_called_on_commit
# ---------------------------------------------------------------------------


def _drive_call_tool(server, name: str, arguments: dict):
    """Invoke the REAL registered tools/call dispatch handler (not a mock).

    Battle-test fix (MEDIUM): the old record_activity tests called the scheduler mock
    themselves and asserted it — exercising zero dispatch code. These now go through
    the actual tools/call handler so the assertion proves the dispatcher wires
    scheduler.record_activity().

    crystalium#39 (mcp SDK 2.x): the low-level Server no longer keys handlers by
    request TYPE on a public `request_handlers` dict — Server.add_request_handler
    registers by METHOD STRING on the private `_request_handlers`, retrievable via
    `Server.get_request_handler`, and the handler signature is `(ctx, params)`. The
    dispatch under test (crystalium's own _call_tool) never reads `ctx`, so a bare
    `None` stands in for the ServerRequestContext a live connection would build.
    """
    import asyncio

    import mcp.types as mt

    entry = server.get_request_handler("tools/call")
    params = mt.CallToolRequestParams(name=name, arguments=arguments)
    return asyncio.run(entry.handler(None, params))


def test_record_activity_called_on_commit(tmp_path: Path, monkeypatch) -> None:
    """The dispatcher calls scheduler.record_activity() after a real commit."""
    monkeypatch.setenv("CRYSTALIUM_SKIP_SLOW", "1")          # null embedder -> fast
    from crystalium.config import Config
    from crystalium.server import _build_server

    server, scheduler = _build_server(Config(data_dir=tmp_path / "ra-commit"))
    calls: list[int] = []
    orig = scheduler.record_activity
    monkeypatch.setattr(scheduler, "record_activity", lambda: (calls.append(1), orig()) and None)

    _drive_call_tool(server, "crystalium.commit", {
        "layer": "episodic",
        "payload": {"summary": "dispatch test",
                    "scope": {"project": "t", "agent_class_visibility": "all"}},
        "provenance": {"source": "verified_agent"},
    })
    assert calls, "dispatch must call scheduler.record_activity() after commit"


def test_record_activity_called_on_recall(tmp_path: Path, monkeypatch) -> None:
    """The dispatcher calls scheduler.record_activity() after a real recall."""
    monkeypatch.setenv("CRYSTALIUM_SKIP_SLOW", "1")
    from crystalium.config import Config
    from crystalium.server import _build_server

    server, scheduler = _build_server(Config(data_dir=tmp_path / "ra-recall"))
    before = scheduler._last_activity

    _drive_call_tool(server, "crystalium.recall",
                     {"scope": {"project": "t"}, "query": "anything", "k": 3})
    assert scheduler._last_activity > before, (
        "dispatch must advance scheduler._last_activity via record_activity() on recall"
    )


# ---------------------------------------------------------------------------
# test_caller_identity_falls_back_to_unknown_t2
# ---------------------------------------------------------------------------


def test_caller_identity_falls_back_to_unknown_t2() -> None:
    """_extract_caller_identity() returns D4 conservative default when no headers."""
    identity = _extract_caller_identity()

    assert identity["eidolon"] == "unknown", (
        f"Expected 'unknown', got {identity['eidolon']!r}"
    )
    assert identity["tier"] == "T2", (
        f"Expected 'T2', got {identity['tier']!r}"
    )
    assert identity["version"] == "n/a", (
        f"Expected 'n/a', got {identity['version']!r}"
    )


def test_caller_tier_parses_t2_from_default() -> None:
    """_caller_tier() converts the default identity dict to Tier.T2."""
    identity = _extract_caller_identity()
    tier = _caller_tier(identity)
    assert tier == Tier.T2


def test_caller_tier_parses_t0() -> None:
    """_caller_tier() correctly parses T0."""
    tier = _caller_tier({"eidolon": "operator", "version": "1.0", "tier": "T0"})
    assert tier == Tier.T0


def test_caller_tier_falls_back_to_t2_on_invalid() -> None:
    """_caller_tier() falls back to T2 on unrecognised tier string."""
    tier = _caller_tier({"eidolon": "unknown", "version": "n/a", "tier": "INVALID"})
    assert tier == Tier.T2


def test_default_caller_constant_matches_expected() -> None:
    """_DEFAULT_CALLER sentinel has the expected D4 conservative values."""
    assert _DEFAULT_CALLER["eidolon"] == "unknown"
    assert _DEFAULT_CALLER["version"] == "n/a"
    assert _DEFAULT_CALLER["tier"] == "T2"


# ---------------------------------------------------------------------------
# Additional server-level checks
# ---------------------------------------------------------------------------


def test_build_tool_manifest_returns_9_tools() -> None:
    """build_tool_manifest() must return exactly 9 tool definitions (W-GE5: +graph_export)."""
    from crystalium.server import build_tool_manifest
    tools = build_tool_manifest()
    assert len(tools) == 9


def test_build_tool_manifest_has_required_tool_names() -> None:
    """All 9 tool names are present (the 8 original + W-GE5 crystalium.graph_export)."""
    from crystalium.server import build_tool_manifest
    tools = build_tool_manifest()
    names = {t["name"] for t in tools}
    expected = {
        "crystalium.recall",
        "crystalium.commit",
        "crystalium.ingest",
        "crystalium.update",
        "crystalium.skill_invoke",
        "crystalium.plan_checkpoint",
        "crystalium.plan_replan",
        "crystalium.session_end",
        "crystalium.graph_export",
    }
    assert names == expected, f"Tool manifest mismatch: {names ^ expected}"


def test_build_tool_manifest_each_has_input_schema() -> None:
    """Every tool descriptor has a non-empty 'inputSchema' block."""
    from crystalium.server import build_tool_manifest
    for tool in build_tool_manifest():
        assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"
        assert tool["inputSchema"].get("type") == "object"


# ---------------------------------------------------------------------------
# S4 — harness-agnostic provenance coercion tests
# Covers: G-SOURCE-COERCE-DESCRIPTIVE, G-SOURCE-IDENTITY,
#         G-SOURCE-NEVER-HUMAN-NON-T0, G-CREATEDAT-TOLERANT, G-ADVISORY-OBSERVABLE
# (change: harness-agnostic-provenance-source-coercion)
# ---------------------------------------------------------------------------


def _stub_commit_result() -> dict:
    """Canonical stub return for a mocked layer.commit() call."""
    return {
        "status": "committed",
        "id": "stub-id",
        "layer": "episodic",
        "validation_state": "validated",
        "importance": 0.0,
        "content_ref": None,
    }


def _call_handle_commit(args: dict, caller_tier, config: "Config | None" = None) -> "tuple[dict, dict]":
    """Drive _handle_commit with a mocked episodic layer; return (result, captured).

    captured["provenance"] holds the Provenance object passed to layer.commit().
    config defaults to a throwaway Config (v1.6 canonicalization needs
    config.data_dir; these tests don't care what the canonical key resolves to).
    """
    from crystalium.server import _handle_commit
    from unittest.mock import MagicMock
    import tempfile
    from pathlib import Path as _Path

    if config is None:
        config = Config(data_dir=_Path(tempfile.mkdtemp()) / "chc-data", rate_limit_per_minute=1000)

    captured: dict = {}

    mock_episodic = MagicMock()

    def _capture(payload, provenance, caller_tier):  # noqa: ARG001
        captured["provenance"] = provenance
        return _stub_commit_result()

    mock_episodic.commit.side_effect = _capture

    result = _handle_commit(
        args,
        mock_episodic,
        MagicMock(),  # semantic
        MagicMock(),  # procedural
        MagicMock(),  # execution
        caller_tier=caller_tier,
        config=config,
    )
    return result, captured


class TestProvenanceSourceCoercion:
    """G-SOURCE-COERCE-DESCRIPTIVE, G-SOURCE-IDENTITY, G-SOURCE-NEVER-HUMAN-NON-T0."""

    def test_descriptive_source_coerced_to_verified_agent(self) -> None:
        """G-SOURCE-COERCE-DESCRIPTIVE: T1 caller with descriptive source gets verified_agent."""
        from crystalium.trust import Tier

        result, captured = _call_handle_commit(
            {
                "layer": "episodic",
                "payload": {"summary": "spectra handoff", "scope": {
                    "project": "p", "agent_class_visibility": "all",
                }},
                "provenance": {
                    "source": "spectra-planning-session",
                    "author_agent": "spectra-v1",
                },
            },
            caller_tier=Tier.T1,
        )

        assert result["status"] == "committed"
        # S1: coercion happened — source is now the trust-class enum value
        assert captured["provenance"].source == "verified_agent"
        # author_agent must be preserved verbatim (the human-meaningful label)
        assert captured["provenance"].author_agent == "spectra-v1"
        # S3: advisory is present because coercion fired
        assert "provenance_coercion" in result, "Advisory must be present when coercion fires"
        adv = result["provenance_coercion"]
        assert adv["field"] == "source"
        assert adv["from"] == "spectra-planning-session"
        assert adv["to"] == "verified_agent"

    @pytest.mark.parametrize("source", ["human", "verified_agent", "unverified_agent", "environment"])
    def test_valid_source_identity_passthrough_coerce_helper(self, source: str) -> None:
        """G-SOURCE-IDENTITY: each of the 4 valid source values is returned unchanged by the helper."""
        from crystalium.server import _coerce_provenance_source
        from crystalium.trust import Tier

        final, coerced = _coerce_provenance_source(source, Tier.T1)
        assert final == source, f"IDENTITY violation: {source!r} was rewritten to {final!r}"
        assert coerced is False

    def test_valid_source_no_advisory_on_commit(self) -> None:
        """G-SOURCE-IDENTITY: clean path (valid source) attaches no provenance_coercion field."""
        from crystalium.trust import Tier

        result, captured = _call_handle_commit(
            {
                "layer": "episodic",
                "payload": {"summary": "clean commit", "scope": {
                    "project": "p", "agent_class_visibility": "all",
                }},
                "provenance": {"source": "verified_agent"},
            },
            caller_tier=Tier.T1,
        )

        assert result["status"] == "committed"
        assert captured["provenance"].source == "verified_agent"
        assert "provenance_coercion" not in result, (
            "Advisory must be ABSENT on the identity path — "
            "result must be byte-identical to the pre-fix behaviour"
        )

    @pytest.mark.parametrize("tier_name,raw_source", [
        ("T1", "spectra-planning-session"),
        ("T1", ""),
        ("T1", None),
        ("T2", "some-tool-description"),
        ("T2", None),
        ("T3", "environment-output"),
        ("T3", ""),
    ])
    def test_non_t0_never_coerces_to_human(self, tier_name: str, raw_source) -> None:
        """G-SOURCE-NEVER-HUMAN-NON-T0: non-T0 coercion never yields source='human'."""
        from crystalium.server import _coerce_provenance_source
        from crystalium.trust import Tier

        tier = getattr(Tier, tier_name)
        final, _ = _coerce_provenance_source(raw_source, tier)
        assert final != "human", (
            f"_coerce_provenance_source({raw_source!r}, {tier_name}) yielded 'human' — "
            "this would set permanent forgetting-protection (protection.py:54-58) "
            "for a non-T0 caller (W4 breach / RISK-2)"
        )


class TestCreatedAtTolerance:
    """G-CREATEDAT-TOLERANT: Z-suffix, epoch int, garbage — no crash."""

    def _commit_args(self, created_at_value) -> dict:
        return {
            "layer": "episodic",
            "payload": {"summary": "ts-test", "scope": {
                "project": "p", "agent_class_visibility": "all",
            }},
            "provenance": {"source": "verified_agent", "created_at": created_at_value},
        }

    def test_z_suffix_iso_parses_without_crash(self) -> None:
        """G-CREATEDAT-TOLERANT: Z-suffixed ISO string is handled (replace + fromisoformat)."""
        from crystalium.trust import Tier

        result, _ = _call_handle_commit(self._commit_args("2026-01-01T00:00:00Z"), Tier.T1)

        assert result["status"] == "committed"
        # Z suffix is correctly normalised — not a fallback, so no advisory for created_at
        assert "provenance_coercion" not in result

    def test_epoch_int_parses_without_crash(self) -> None:
        """G-CREATEDAT-TOLERANT: epoch int is parsed via datetime.fromtimestamp."""
        from crystalium.trust import Tier

        result, _ = _call_handle_commit(self._commit_args(1_700_000_000), Tier.T1)

        assert result["status"] == "committed"
        # Successful epoch parse: no fallback, no advisory
        assert "provenance_coercion" not in result

    def test_garbage_timestamp_falls_back_no_crash(self) -> None:
        """G-CREATEDAT-TOLERANT: non-ISO garbage string falls back to server_now without crash."""
        from crystalium.trust import Tier

        result, _ = _call_handle_commit(self._commit_args("absolutely-not-a-timestamp"), Tier.T1)

        assert result["status"] == "committed"
        # Fallback fires → advisory attached for created_at
        assert "provenance_coercion" in result
        adv = result["provenance_coercion"]
        assert adv["field"] == "created_at"
        assert adv["from"] == "absolutely-not-a-timestamp"
        assert adv["to"] == "server_now"


class TestAdvisoryObservable:
    """G-ADVISORY-OBSERVABLE: advisory present only when coercion fires."""

    def test_advisory_present_on_source_coercion(self) -> None:
        """G-ADVISORY-OBSERVABLE: non-fatal advisory attached when source is coerced."""
        from crystalium.trust import Tier

        result, _ = _call_handle_commit(
            {
                "layer": "episodic",
                "payload": {"summary": "x", "scope": {
                    "project": "p", "agent_class_visibility": "all",
                }},
                "provenance": {"source": "custom-agent-session"},
            },
            caller_tier=Tier.T1,
        )

        assert "provenance_coercion" in result
        adv = result["provenance_coercion"]
        assert adv["field"] == "source"
        assert adv["from"] == "custom-agent-session"
        assert adv["to"] == "verified_agent"

    def test_advisory_absent_on_clean_path(self) -> None:
        """G-ADVISORY-OBSERVABLE: no advisory when source is valid AND created_at is valid."""
        from crystalium.trust import Tier

        result, _ = _call_handle_commit(
            {
                "layer": "episodic",
                "payload": {"summary": "x", "scope": {
                    "project": "p", "agent_class_visibility": "all",
                }},
                "provenance": {
                    "source": "environment",
                    "created_at": "2026-06-01T00:00:00+00:00",
                },
            },
            caller_tier=Tier.T1,
        )

        assert "provenance_coercion" not in result

    def test_advisory_is_list_when_both_source_and_created_at_coerce(self) -> None:
        """G-ADVISORY-OBSERVABLE: advisory is a list when both source and created_at fire."""
        from crystalium.trust import Tier

        result, _ = _call_handle_commit(
            {
                "layer": "episodic",
                "payload": {"summary": "x", "scope": {
                    "project": "p", "agent_class_visibility": "all",
                }},
                "provenance": {
                    "source": "bad-descriptive-source",
                    "created_at": "not-a-date-at-all",
                },
            },
            caller_tier=Tier.T1,
        )

        assert "provenance_coercion" in result
        adv = result["provenance_coercion"]
        assert isinstance(adv, list), "Both coercions fired → advisory must be a list"
        assert len(adv) == 2
        fields = {item["field"] for item in adv}
        assert fields == {"source", "created_at"}
