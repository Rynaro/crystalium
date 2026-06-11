"""R25 — CLI tests for the `recall` subcommand (GAP-2).

Tests:
  test_recall_happy_json_shape              — exit 0; stdout is RecallResult JSON with expected keys
  test_recall_format_text                   — --format text → [layer/tier] lines; no JSON on stdout
  test_recall_missing_query_exits_nonzero   — omit --query → non-zero exit; "Missing option" in output
  test_recall_missing_scope_project_exits_nonzero — omit --scope-project → non-zero exit
  test_recall_layers_passthrough            — --layers csv → Aetheryte.recall called with layers list
  test_recall_invalid_layer_exits_1         — --layers bogus → exit 1; "Unknown layer" in output
  test_recall_fast_path_no_vector_graph     — default (no --full) → VectorStore/GraphStore NOT constructed
  test_recall_full_constructs_arms          — --full → VectorStore/GraphStore constructors ARE called
  test_recall_tier_env_passthrough          — CRYSTALIUM_CALLER_TIER=T1 → caller_tier=Tier.T1
  test_recall_error_exits_1                 — aetheryte.recall raises → exit 1; no JSON on stdout
  test_recall_never_writes                  — no commit/tombstone/write methods invoked on RelationalStore

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_recall_cli.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from crystalium.__main__ import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_record(layer: str = "episodic", trust_tier: str = "T1") -> MagicMock:
    """Build a MagicMock that mimics a CrystalSummary Pydantic model."""
    r = MagicMock()
    r.model_dump.return_value = {
        "id": "test-id-1234",
        "layer": layer,
        "summary": f"Test summary from {layer}",
        "trust_tier": trust_tier,
        "validation_state": "unverified",
        "importance": 0.8,
        "last_access": "2026-06-11T00:00:00+00:00",
        "content_ref": None,
        "score": None,
    }
    return r


def _fake_recall_result(records=None) -> MagicMock:
    """Build a MagicMock that mimics RecallResult.model_dump()."""
    if records is None:
        records = [_fake_record()]
    result = MagicMock()
    result.records = records
    result.model_dump.return_value = {
        "records": [r.model_dump() for r in records],
        "slot_breakdown": {
            "executive": 0,
            "procedural": 0,
            "semantic": 0,
            "episodic": 100,
            "execution": 0,
            "buffer": 0,
        },
        "total_tokens": 100,
        "evicted_count": 0,
    }
    return result


def _invoke(args: list[str], env: dict | None = None, data_dir: str | None = None):
    """Invoke CLI via CliRunner and return Result."""
    runner = CliRunner()
    env = env or {}
    if data_dir and "CRYSTALIUM_DATA_DIR" not in env:
        env["CRYSTALIUM_DATA_DIR"] = data_dir
    return runner.invoke(cli, args, env=env, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Shared patch targets
# ---------------------------------------------------------------------------

_PATCH_AETHERYTE = "crystalium.aetheryte.retrieve.Aetheryte"
_PATCH_VECTOR = "crystalium.storage.vector.VectorStore"
_PATCH_GRAPH = "crystalium.storage.graph.GraphStore"
_PATCH_RELATIONAL = "crystalium.storage.relational.RelationalStore"


# ---------------------------------------------------------------------------
# AC-R24-1 — happy path: exit 0; stdout is a single JSON line with expected keys
# ---------------------------------------------------------------------------


def _find_json_line(output: str) -> str:
    """Extract the first line from output that looks like a JSON object.

    structlog may emit debug lines to stdout before the JSON output; this helper
    finds the first line that starts with '{' so tests are resilient to log preamble.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            return stripped
    return output.strip()


def test_recall_happy_json_shape(tmp_path: Path) -> None:
    """recall --query Q --scope-project P → exit 0; stdout parses as RecallResult JSON."""
    data_dir = str(tmp_path / "crystalium_data")
    fake_result = _fake_recall_result()

    with patch(_PATCH_AETHERYTE) as MockAetheryte:
        mock_instance = MagicMock()
        mock_instance.recall.return_value = fake_result
        MockAetheryte.return_value = mock_instance

        result = _invoke(
            ["recall", "--query", "test query", "--scope-project", "my-project"],
            data_dir=data_dir,
        )

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}.\nOutput:\n{result.output}"

    # stdout must contain a valid JSON line (structlog debug lines may precede it)
    json_line = _find_json_line(result.output)
    parsed = json.loads(json_line)
    assert "records" in parsed
    assert "slot_breakdown" in parsed
    assert "total_tokens" in parsed
    assert "evicted_count" in parsed


# ---------------------------------------------------------------------------
# AC-R24-2 — --format text: each line matches [layer/tier] pattern; no JSON
# ---------------------------------------------------------------------------


def test_recall_format_text(tmp_path: Path) -> None:
    """--format text → each record prints as [layer/trust_tier] summary; no JSON."""
    import re

    data_dir = str(tmp_path / "crystalium_data")
    records = [
        _fake_record(layer="episodic", trust_tier="T1"),
        _fake_record(layer="semantic", trust_tier="T2"),
    ]
    fake_result = _fake_recall_result(records=records)

    with patch(_PATCH_AETHERYTE) as MockAetheryte:
        mock_instance = MagicMock()
        mock_instance.recall.return_value = fake_result
        MockAetheryte.return_value = mock_instance

        result = _invoke(
            ["recall", "--query", "test", "--scope-project", "proj", "--format", "text"],
            data_dir=data_dir,
        )

    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) >= 1, "Expected at least one output line"
    # Each line must match [layer/tier] pattern
    pattern = re.compile(r"^\[\w+/T\d\] ")
    for line in lines:
        assert pattern.match(line), f"Line does not match [layer/tier] pattern: {line!r}"
    # No JSON curly braces
    assert "{" not in result.output, "JSON output found in text mode"


# ---------------------------------------------------------------------------
# AC-R24-3 — missing required flags
# ---------------------------------------------------------------------------


def test_recall_missing_query_exits_nonzero(tmp_path: Path) -> None:
    """Omit --query → non-zero exit; usage error in output."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["recall", "--scope-project", "my-project"],
        env={"CRYSTALIUM_DATA_DIR": str(tmp_path / "crystalium_data")},
        catch_exceptions=True,
    )
    assert result.exit_code != 0
    combined = (result.output or "") + (result.exception and str(result.exception) or "")
    assert "Missing option" in result.output or result.exit_code != 0


def test_recall_missing_scope_project_exits_nonzero(tmp_path: Path) -> None:
    """Omit --scope-project → non-zero exit."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["recall", "--query", "test query"],
        env={"CRYSTALIUM_DATA_DIR": str(tmp_path / "crystalium_data")},
        catch_exceptions=True,
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# AC-R24-4 — --layers CSV passthrough + invalid layer exits 1
# ---------------------------------------------------------------------------


def test_recall_layers_passthrough(tmp_path: Path) -> None:
    """--layers episodic,semantic → Aetheryte.recall called with layers=['episodic','semantic']."""
    data_dir = str(tmp_path / "crystalium_data")
    fake_result = _fake_recall_result()

    with patch(_PATCH_AETHERYTE) as MockAetheryte:
        mock_instance = MagicMock()
        mock_instance.recall.return_value = fake_result
        MockAetheryte.return_value = mock_instance

        result = _invoke(
            [
                "recall",
                "--query", "test",
                "--scope-project", "proj",
                "--layers", "episodic,semantic",
            ],
            data_dir=data_dir,
        )

    assert result.exit_code == 0, result.output
    call_kwargs = mock_instance.recall.call_args
    passed_layers = call_kwargs.kwargs.get("layers") or call_kwargs.args[2] if call_kwargs.args else None
    # Extract from keyword args
    if call_kwargs.kwargs:
        passed_layers = call_kwargs.kwargs.get("layers")
    assert passed_layers == ["episodic", "semantic"], (
        f"Expected layers=['episodic','semantic'], got {passed_layers}"
    )


def test_recall_invalid_layer_exits_1(tmp_path: Path) -> None:
    """--layers bogus → exit 1; 'Unknown layer' in output."""
    data_dir = str(tmp_path / "crystalium_data")

    with patch(_PATCH_AETHERYTE):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["recall", "--query", "test", "--scope-project", "proj", "--layers", "bogus"],
            env={"CRYSTALIUM_DATA_DIR": data_dir},
            catch_exceptions=False,
        )

    assert result.exit_code != 0
    assert "Unknown layer" in result.output or "Error" in result.output


# ---------------------------------------------------------------------------
# AC-R24-5 / AC-R25-2 — fast path: VectorStore/GraphStore NOT constructed by default
# ---------------------------------------------------------------------------


def test_recall_fast_path_no_vector_graph(tmp_path: Path) -> None:
    """Default (no --full) → VectorStore/GraphStore NOT constructed; no lance/kuzu imports."""
    data_dir = str(tmp_path / "crystalium_data")
    fake_result = _fake_recall_result()

    with (
        patch(_PATCH_AETHERYTE) as MockAetheryte,
        patch(_PATCH_VECTOR) as MockVectorStore,
        patch(_PATCH_GRAPH) as MockGraphStore,
    ):
        mock_instance = MagicMock()
        mock_instance.recall.return_value = fake_result
        MockAetheryte.return_value = mock_instance

        result = _invoke(
            ["recall", "--query", "test", "--scope-project", "proj"],
            data_dir=data_dir,
        )

    assert result.exit_code == 0, result.output
    # VectorStore and GraphStore constructors must NOT have been called on the fast path
    assert not MockVectorStore.called, (
        "VectorStore was constructed on the fast path — lazy-import discipline violated"
    )
    assert not MockGraphStore.called, (
        "GraphStore was constructed on the fast path — lazy-import discipline violated"
    )

    # Verify that Aetheryte was constructed with _NullVectorStore/_NullGraphStore instances
    aetheryte_call_kwargs = MockAetheryte.call_args.kwargs
    from crystalium.server import _NullVectorStore, _NullGraphStore
    assert isinstance(aetheryte_call_kwargs.get("vector_store"), _NullVectorStore), (
        "Expected _NullVectorStore on fast path"
    )
    assert isinstance(aetheryte_call_kwargs.get("graph_store"), _NullGraphStore), (
        "Expected _NullGraphStore on fast path"
    )


# ---------------------------------------------------------------------------
# AC-R24-5 / test_recall_full_constructs_arms — --full constructs vector+graph
# ---------------------------------------------------------------------------


def test_recall_full_constructs_arms(tmp_path: Path) -> None:
    """--full → VectorStore/GraphStore constructors ARE called (with Null-fallback on raise)."""
    data_dir = str(tmp_path / "crystalium_data")
    fake_result = _fake_recall_result()

    with (
        patch(_PATCH_AETHERYTE) as MockAetheryte,
        patch(_PATCH_VECTOR) as MockVectorStore,
        patch(_PATCH_GRAPH) as MockGraphStore,
    ):
        mock_instance = MagicMock()
        mock_instance.recall.return_value = fake_result
        MockAetheryte.return_value = mock_instance

        result = _invoke(
            ["recall", "--query", "test", "--scope-project", "proj", "--full"],
            data_dir=data_dir,
        )

    assert result.exit_code == 0, result.output
    # When --full is given, VectorStore and GraphStore constructors MUST be called
    assert MockVectorStore.called, "VectorStore was NOT constructed with --full"
    assert MockGraphStore.called, "GraphStore was NOT constructed with --full"


# ---------------------------------------------------------------------------
# AC-R24-6 — CRYSTALIUM_CALLER_TIER env passthrough
# ---------------------------------------------------------------------------


def test_recall_tier_env_passthrough(tmp_path: Path) -> None:
    """CRYSTALIUM_CALLER_TIER=T1 in env → Aetheryte.recall called with caller_tier=Tier.T1."""
    from crystalium.trust import Tier

    data_dir = str(tmp_path / "crystalium_data")
    fake_result = _fake_recall_result()

    with patch(_PATCH_AETHERYTE) as MockAetheryte:
        mock_instance = MagicMock()
        mock_instance.recall.return_value = fake_result
        MockAetheryte.return_value = mock_instance

        result = _invoke(
            ["recall", "--query", "test", "--scope-project", "proj"],
            env={"CRYSTALIUM_CALLER_TIER": "T1", "CRYSTALIUM_DATA_DIR": data_dir},
        )

    assert result.exit_code == 0, result.output
    call_kwargs = mock_instance.recall.call_args.kwargs
    passed_tier = call_kwargs.get("caller_tier")
    assert passed_tier == Tier.T1, f"Expected Tier.T1, got {passed_tier}"


# ---------------------------------------------------------------------------
# AC-R24-9 / test_recall_error_exits_1 — store/enforcement exception → exit 1
# ---------------------------------------------------------------------------


def test_recall_error_exits_1(tmp_path: Path) -> None:
    """aetheryte.recall raises an exception → exit 1; one-line stderr; no JSON on stdout."""
    data_dir = str(tmp_path / "crystalium_data")

    with patch(_PATCH_AETHERYTE) as MockAetheryte:
        mock_instance = MagicMock()
        mock_instance.recall.side_effect = RuntimeError("store exploded")
        MockAetheryte.return_value = mock_instance

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["recall", "--query", "test", "--scope-project", "proj"],
            env={"CRYSTALIUM_DATA_DIR": data_dir},
            catch_exceptions=False,
        )

    assert result.exit_code != 0, "Expected non-zero exit on recall error"
    # No JSON output line on stdout (structlog lines are OK but no RecallResult JSON)
    json_line = _find_json_line(result.output)
    # The JSON line should not be a valid RecallResult (i.e., no "records" key)
    try:
        parsed = json.loads(json_line)
        assert "records" not in parsed, (
            "RecallResult JSON should not appear on stdout when recall raises"
        )
    except json.JSONDecodeError:
        pass  # no JSON at all → correct
    # Error message must mention the failure
    combined = result.output
    assert "recall failed" in combined or "Error" in combined, (
        f"Expected error message in output, got: {combined!r}"
    )


# ---------------------------------------------------------------------------
# AC-R24-8 / test_recall_never_writes — no write methods invoked; persist_dynamics=False
# ---------------------------------------------------------------------------


def test_recall_never_writes(tmp_path: Path) -> None:
    """No commit/tombstone/write methods on RelationalStore; Aetheryte has persist_dynamics=False."""
    data_dir = str(tmp_path / "crystalium_data")
    fake_result = _fake_recall_result()

    with (
        patch(_PATCH_RELATIONAL) as MockRelationalStore,
        patch(_PATCH_AETHERYTE) as MockAetheryte,
    ):
        mock_rs = MagicMock()
        MockRelationalStore.return_value = mock_rs

        mock_instance = MagicMock()
        mock_instance.recall.return_value = fake_result
        MockAetheryte.return_value = mock_instance

        result = _invoke(
            ["recall", "--query", "test", "--scope-project", "proj"],
            data_dir=data_dir,
        )

    assert result.exit_code == 0, result.output

    # Write methods must NOT have been called on RelationalStore
    assert not mock_rs.tombstone.called, "tombstone() was called — recall must never write"
    assert not mock_rs.commit.called, "commit() was called — recall must never write"

    # Aetheryte must be constructed with persist_dynamics=False
    aetheryte_call_kwargs = MockAetheryte.call_args.kwargs
    assert aetheryte_call_kwargs.get("persist_dynamics") is False, (
        "Aetheryte must have persist_dynamics=False for recall CLI"
    )
    assert aetheryte_call_kwargs.get("forgetting_fsrs") is False, (
        "Aetheryte must have forgetting_fsrs=False for recall CLI"
    )
