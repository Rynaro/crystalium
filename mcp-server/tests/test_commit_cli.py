"""v1.7 — CLI tests for the `commit` subcommand.

`commit` is the one-shot WRITE counterpart to `recall` (GAP-2 out-of-MCP-session
pairing): `recall` reads, `commit` writes. Tests use REAL (tmp-dir) stores —
BlobStore + RelationalStore, no mocking — so the happy-path test doubles as an
in-process round-trip through `recall`'s BM25 fast path.

Tests:
  test_commit_happy_path_then_recall_roundtrip — exit 0; stdout is one JSON doc
                                                  with an id; recall finds it via BM25
  test_commit_poor_summary_rejected_exit_1     — summary fails the v1.6 quality
                                                  gate → exit 1, no stdout JSON
  test_commit_missing_scope_project_exits_nonzero — omit --scope-project → non-zero exit
  test_commit_format_text_prints_only_id       — --format text → stdout is just the id
  test_commit_source_default_is_environment    — no --source → stored provenance.source == "environment"
  test_commit_source_override_is_stored        — --source/--author-agent/--task-id override is persisted
  test_commit_help_no_heavy_imports            — `commit --help` never pulls torch/lance/kuzu

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_commit_cli.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

from crystalium.__main__ import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(args: list[str], env: dict | None = None, data_dir: str | None = None):
    """Invoke CLI via CliRunner and return Result."""
    runner = CliRunner()
    env = dict(env or {})
    if data_dir and "CRYSTALIUM_DATA_DIR" not in env:
        env["CRYSTALIUM_DATA_DIR"] = data_dir
    return runner.invoke(cli, args, env=env, catch_exceptions=False)


def _find_json_line(output: str) -> str:
    """Extract the first line from output that looks like a JSON object.

    Mirrors test_recall_cli.py's helper: structlog is routed to the REAL stderr
    (sys.__stderr__), so it should never appear in `output` at all, but this
    keeps the assertion resilient regardless.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            return stripped
    return output.strip()


# A summary that clears the v1.6 quality gate (>= 24 chars, >= 3 alpha words,
# not a bare machine label). Includes a distinctive token so BM25 recall can
# find it without ambiguity against anything else in a fresh tmp store.
GOOD_SUMMARY = "Round trip canary check for wobblefrog project rollout"
DISTINCTIVE_TOKEN = "wobblefrog"


# ---------------------------------------------------------------------------
# Happy path + in-process recall round-trip
# ---------------------------------------------------------------------------


def test_commit_happy_path_then_recall_roundtrip(tmp_path: Path) -> None:
    """commit --summary ... --scope-project ... → exit 0; stdout is one JSON
    document with an id; a subsequent `recall --query <token> --scope-project
    <same>` (real BM25 fast path, no mocks) finds the committed crystal."""
    data_dir = str(tmp_path / "crystalium_data")
    project = "commit-roundtrip-project"

    commit_result = _invoke(
        ["commit", "--summary", GOOD_SUMMARY, "--scope-project", project],
        data_dir=data_dir,
    )

    assert commit_result.exit_code == 0, (
        f"Expected exit 0, got {commit_result.exit_code}.\nOutput:\n{commit_result.output}"
    )

    # STRICT: stdout must be exactly one JSON document — structlog must be
    # routed to stderr (sys.__stderr__), mirroring recall's regression lock.
    parsed = json.loads(commit_result.output)
    assert parsed["status"] == "committed"
    assert parsed["layer"] == "episodic"
    assert "id" in parsed and parsed["id"]

    recall_result = _invoke(
        ["recall", "--query", DISTINCTIVE_TOKEN, "--scope-project", project],
        data_dir=data_dir,
    )
    assert recall_result.exit_code == 0, recall_result.output
    recall_parsed = json.loads(recall_result.output)

    ids = [r["id"] for r in recall_parsed["records"]]
    assert parsed["id"] in ids, (
        f"Committed crystal {parsed['id']!r} not found via recall BM25 fast path. "
        f"Recall records: {recall_parsed['records']}"
    )


# ---------------------------------------------------------------------------
# Summary-quality gate — hard rejection (unlike the MCP tool's soft advisory)
# ---------------------------------------------------------------------------


def test_commit_poor_summary_rejected_exit_1(tmp_path: Path) -> None:
    """A summary failing the v1.6 mechanical quality gate → exit 1; no crystal
    is written and no commit-result JSON appears on stdout."""
    data_dir = str(tmp_path / "crystalium_data")

    # Fails MIN_SUMMARY_LENGTH (24) and MIN_ALPHA_WORDS (3).
    result = _invoke(
        ["commit", "--summary", "too short", "--scope-project", "gate-test-project"],
        data_dir=data_dir,
    )

    assert result.exit_code != 0, "Expected non-zero exit for a gate-failing summary"

    json_line = _find_json_line(result.output)
    try:
        parsed = json.loads(json_line)
        assert "id" not in parsed, "Commit result JSON should not appear on stdout when the gate rejects"
    except json.JSONDecodeError:
        pass  # no JSON at all → correct

    assert "quality gate" in result.output or "Error" in result.output, (
        f"Expected a quality-gate error message in output, got: {result.output!r}"
    )


def test_commit_machine_label_summary_rejected_exit_1(tmp_path: Path) -> None:
    """A bare machine-label-shaped summary (the MOTIVATING INCIDENT shape) is
    also rejected, even though it is long enough."""
    data_dir = str(tmp_path / "crystalium_data")

    result = _invoke(
        ["commit", "--summary", "plan_checkpoint:08234787", "--scope-project", "gate-test-project"],
        data_dir=data_dir,
    )

    assert result.exit_code != 0
    assert "quality gate" in result.output or "Error" in result.output


# ---------------------------------------------------------------------------
# Missing required flags
# ---------------------------------------------------------------------------


def test_commit_missing_scope_project_exits_nonzero(tmp_path: Path) -> None:
    """Omit --scope-project → non-zero exit (Click required option)."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["commit", "--summary", GOOD_SUMMARY],
        env={"CRYSTALIUM_DATA_DIR": str(tmp_path / "crystalium_data")},
        catch_exceptions=True,
    )
    assert result.exit_code != 0


def test_commit_missing_summary_exits_nonzero(tmp_path: Path) -> None:
    """Omit --summary → non-zero exit (Click required option)."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["commit", "--scope-project", "proj"],
        env={"CRYSTALIUM_DATA_DIR": str(tmp_path / "crystalium_data")},
        catch_exceptions=True,
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# --format text — stdout is just the id
# ---------------------------------------------------------------------------


def test_commit_format_text_prints_only_id(tmp_path: Path) -> None:
    """--format text → stdout is exactly the new crystal id, nothing else."""
    data_dir = str(tmp_path / "crystalium_data")

    result = _invoke(
        [
            "commit",
            "--summary", GOOD_SUMMARY,
            "--scope-project", "text-format-project",
            "--format", "text",
        ],
        data_dir=data_dir,
    )

    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 1, f"Expected exactly one line of output, got: {lines!r}"
    assert "{" not in lines[0], "JSON output found in text mode"
    # A UUID4 string (uuid.uuid4() per episodic.commit) — sanity-check shape.
    import uuid as _uuid

    _uuid.UUID(lines[0])  # raises ValueError if not a valid UUID


# ---------------------------------------------------------------------------
# --source default + override (stored provenance, verified via RelationalStore)
# ---------------------------------------------------------------------------


def test_commit_source_default_is_environment(tmp_path: Path) -> None:
    """No --source given → stored provenance.source == 'environment'."""
    from crystalium.storage.relational import RelationalStore

    data_dir = tmp_path / "crystalium_data"

    result = _invoke(
        ["commit", "--summary", GOOD_SUMMARY, "--scope-project", "source-default-project"],
        data_dir=str(data_dir),
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)

    relational = RelationalStore(db_path=data_dir / "index.sqlite")
    crystal = relational.get_crystal(parsed["id"])
    assert crystal is not None
    assert crystal["provenance"]["source"] == "environment"
    assert crystal["provenance"]["author_agent"] == "crystalium-cli"


def test_commit_source_override_is_stored(tmp_path: Path) -> None:
    """--source/--author-agent/--task-id overrides are persisted on the crystal."""
    from crystalium.storage.relational import RelationalStore

    data_dir = tmp_path / "crystalium_data"

    result = _invoke(
        [
            "commit",
            "--summary", GOOD_SUMMARY,
            "--scope-project", "source-override-project",
            "--source", "verified_agent",
            "--author-agent", "spectra",
            "--task-id", "T-42",
        ],
        data_dir=str(data_dir),
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)

    relational = RelationalStore(db_path=data_dir / "index.sqlite")
    crystal = relational.get_crystal(parsed["id"])
    assert crystal is not None
    assert crystal["provenance"]["source"] == "verified_agent"
    assert crystal["provenance"]["author_agent"] == "spectra"
    assert crystal["provenance"]["task_id"] == "T-42"


# ---------------------------------------------------------------------------
# --content default (mirrors summary when omitted)
# ---------------------------------------------------------------------------


def test_commit_content_defaults_to_summary(tmp_path: Path) -> None:
    """--content omitted → the persisted blob content defaults to --summary."""
    from crystalium.storage.blob import BlobStore
    from crystalium.storage.relational import RelationalStore

    data_dir = tmp_path / "crystalium_data"

    result = _invoke(
        ["commit", "--summary", GOOD_SUMMARY, "--scope-project", "content-default-project"],
        data_dir=str(data_dir),
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)

    relational = RelationalStore(db_path=data_dir / "index.sqlite")
    crystal = relational.get_crystal(parsed["id"])
    blob_store = BlobStore(root=data_dir / "blobs")
    raw = blob_store.get(crystal["content_ref"])
    payload = json.loads(raw)
    assert payload["content"] == GOOD_SUMMARY


# ---------------------------------------------------------------------------
# `commit --help` — fast path, no heavy imports
# ---------------------------------------------------------------------------


def test_commit_help_no_heavy_imports() -> None:
    """`commit --help` documents the flags and never pulls torch/lance/kuzu.

    Compares sys.modules before/after (rather than asserting absolute absence)
    because other tests in the same pytest process may have already imported
    these heavy deps — the invariant under test is that `--help` itself does
    not trigger a NEW import of any of them.
    """
    heavy_modules = ("torch", "sentence_transformers", "lancedb", "kuzu")
    before = set(sys.modules)

    runner = CliRunner()
    result = runner.invoke(cli, ["commit", "--help"], catch_exceptions=False)

    after = set(sys.modules)
    newly_imported = after - before

    assert result.exit_code == 0, result.output
    assert "--summary" in result.output
    assert "--scope-project" in result.output
    # The exact mechanical gate rules must be documented in --summary's help.
    assert "24 chars" in result.output or "quality gate" in result.output

    heavy_newly_imported = [m for m in heavy_modules if m in newly_imported]
    assert not heavy_newly_imported, (
        f"{heavy_newly_imported} newly imported merely by `commit --help` — "
        "lazy-import discipline (D-G2b) violated"
    )
