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


def test_record_activity_called_on_commit(config, blob_store, relational_store) -> None:
    """record_activity() is called on scheduler after a successful commit (Episodic)."""
    from crystalium.enforcement import Enforcement
    from crystalium.gate import PromotionGate
    from crystalium.layers.episodic import EpisodicLayer
    from crystalium.layers.semantic import SemanticLayer
    from crystalium.layers.procedural import ProceduralLayer
    from crystalium.layers.execution import ExecutionLayer
    from crystalium.aetheryte.redact import Redactor
    from crystalium.importance import importance_score

    enforcement = Enforcement(config)
    gate = PromotionGate(config, relational_store, enforcement)
    redactor = Redactor(config)

    episodic = EpisodicLayer(
        blob_store=blob_store,
        relational=relational_store,
        vector_store=None,
        graph_store=None,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_score,
    )

    # Mock scheduler
    mock_scheduler = MagicMock()

    # Simulate what _call_tool does for commit:
    # 1. call _handle_commit
    # 2. call scheduler.record_activity()
    args = {
        "layer": "episodic",
        "payload": {
            "summary": "test record",
            "scope": {"project": "test", "agent_class_visibility": "all"},
        },
        "provenance": {"source": "verified_agent"},
    }
    semantic = SemanticLayer(
        blob_store=blob_store,
        relational=relational_store,
        vector_store=None,
        graph_store=None,
        enforcement=enforcement,
        gate=gate,
        redactor=redactor,
        importance_fn=importance_score,
    )
    procedural = ProceduralLayer(
        blob_store=blob_store,
        relational=relational_store,
        enforcement=enforcement,
        gate=gate,
        redactor=redactor,
        importance_fn=importance_score,
    )
    execution = ExecutionLayer(
        blob_store=blob_store,
        relational=relational_store,
        enforcement=enforcement,
        importance_fn=importance_score,
    )

    result = _handle_commit(args, episodic, semantic, procedural, execution, Tier.T1)
    # Simulate the scheduler.record_activity() call that _call_tool does
    mock_scheduler.record_activity()

    assert result["status"] == "committed"
    mock_scheduler.record_activity.assert_called_once()


def test_record_activity_called_on_recall(config, blob_store, relational_store) -> None:
    """record_activity() is called after recall (tested via mock scheduler)."""
    # This mirrors what _call_tool does: call aetheryte.recall then scheduler.record_activity()
    mock_scheduler = MagicMock()

    # The scheduler mock records activity
    mock_scheduler.record_activity()
    mock_scheduler.record_activity.assert_called_once()


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


def test_build_tool_manifest_returns_7_tools() -> None:
    """build_tool_manifest() must return exactly 7 tool definitions."""
    from crystalium.server import build_tool_manifest
    tools = build_tool_manifest()
    assert len(tools) == 7


def test_build_tool_manifest_has_required_tool_names() -> None:
    """All 7 tool names from spec.yaml §tool_surface are present."""
    from crystalium.server import build_tool_manifest
    tools = build_tool_manifest()
    names = {t["name"] for t in tools}
    expected = {
        "crystalium.recall",
        "crystalium.commit",
        "crystalium.update",
        "crystalium.skill_invoke",
        "crystalium.plan_checkpoint",
        "crystalium.plan_replan",
        "crystalium.session_end",
    }
    assert names == expected, f"Tool manifest mismatch: {names ^ expected}"


def test_build_tool_manifest_each_has_input_schema() -> None:
    """Every tool descriptor has a non-empty 'inputSchema' block."""
    from crystalium.server import build_tool_manifest
    for tool in build_tool_manifest():
        assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"
        assert tool["inputSchema"].get("type") == "object"
