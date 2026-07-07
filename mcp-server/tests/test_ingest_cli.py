"""v1.8 — CLI tests for the `ingest` subcommand.

`ingest` is the third verb of the GAP-2 out-of-MCP-session pairing: `recall`
reads, `commit` writes a caller-typed summary, `ingest` writes an inbound ECL
roster handoff envelope + artifact payload. Tests use REAL (tmp-dir) stores —
BlobStore + RelationalStore, no mocking — so several tests double as an
in-process round-trip through `recall`'s BM25 fast path. Modeled directly on
test_commit_cli.py's fixture/helper style; envelope fixtures mirror
test_ingest_handler.py's `_envelope()` builder.

Tests (AC-101..AC-116 of spec-crystalium-1.8.criteria.md):
  test_ingest_happy_path_single_json_doc                        — AC-101
  test_session_handoff_roundtrip_recall_by_kind_and_token        — AC-102
  test_tool_origin_envelope_lands_t3_quarantined                 — AC-103
  test_quarantined_session_handoff_still_surfaces_on_recall      — AC-104
  test_payload_hash_mismatch_exits_1                             — AC-105
  test_missing_envelope_field_exits_1                            — AC-106
  test_unsupported_envelope_version_exits_1                      — AC-107
  test_envelope_parse_error_exits_1                               — AC-108
  test_nexus_envelope_shape_extra_fields_tolerated                — AC-109
  test_scope_normalized_to_canonical_key                          — AC-110
  test_thread_id_preserved_in_project_raw                         — AC-111
  test_format_text_prints_only_id                                 — AC-112
  test_no_summary_gate_on_ingest_path                             — AC-113
  test_ingest_help_no_heavy_imports                               — AC-114
  test_mcp_tool_surface_unchanged_9_tools                         — AC-115
  test_caller_tier_env_ignored_tier_is_envelope_derived           — AC-116

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_ingest_cli.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from click.testing import CliRunner

from crystalium.__main__ import cli
from crystalium.ecl import compute_sha256


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(args: list[str], env: Optional[dict] = None, data_dir: Optional[str] = None):
    """Invoke CLI via CliRunner and return Result."""
    runner = CliRunner()
    env = dict(env or {})
    if data_dir and "CRYSTALIUM_DATA_DIR" not in env:
        env["CRYSTALIUM_DATA_DIR"] = data_dir
    return runner.invoke(cli, args, env=env, catch_exceptions=False)


def _find_json_line(output: str) -> str:
    """Extract the first line from output that looks like a JSON object.

    Mirrors test_commit_cli.py's helper: structlog is routed to the REAL
    stderr (sys.__stderr__), so it should never appear in `output` at all,
    but this keeps the assertion resilient regardless.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            return stripped
    return output.strip()


def _envelope(
    payload_str: str,
    *,
    eidolon: str = "atlas",
    tier: str = "T1",
    kind: str = "scout-report",
    version: str = "1.0",
    thread: str = "thread-7",
    objective: str = "map auth subsystem",
    envelope_version_override: Optional[str] = None,
    drop_fields: tuple = (),
    extra_top_level: Optional[dict] = None,
) -> dict:
    """Mirrors test_ingest_handler.py's `_envelope()` fixture builder (:30-44)."""
    sha = compute_sha256(payload_str.encode())
    env = {
        "envelope_version": version,
        "message_id": "m1",
        "thread_id": thread,
        "parent_id": None,
        "from": {"eidolon": eidolon, "version": "1.0.0"},
        "to": {"eidolon": "spectra", "version": "1.0.0"},
        "performative": "INFORM",
        "objective": objective,
        "artifact": {
            "kind": kind, "schema_version": "1.0", "path": "stdin",
            "sha256": sha, "size_bytes": len(payload_str),
        },
        "integrity": {"method": "sha256", "value": sha},
        "trace": {"ts": "2026-01-01T00:00:00Z", "host": "h", "model": "m", "tier": tier},
    }
    if envelope_version_override is not None:
        env["envelope_version"] = envelope_version_override
    for field in drop_fields:
        env.pop(field, None)
    if extra_top_level:
        env.update(extra_top_level)
    return env


# A distinctive token embedded in the envelope `objective` so BM25 recall can
# find it without ambiguity against anything else in a fresh tmp store.
DISTINCTIVE_TOKEN = "gronkulator"


def _session_handoff_kind_envelope(payload_str: str, token: str = DISTINCTIVE_TOKEN, **kw) -> dict:
    """AC-102's GIVEN: artifact.kind IS the literal string 'session_handoff' —
    the server-composed summary becomes 'session_handoff: <objective>' verbatim
    (ingest_adapter.py:164), with the distinctive token inside the objective."""
    objective = f"regression probe distinctive token {token}"
    kw.setdefault("eidolon", "atlas")
    kw.setdefault("tier", "T1")
    return _envelope(
        payload_str, kind="session_handoff", thread="handoff-thread-1",
        objective=objective, **kw,
    )


def _tool_origin_session_handoff_envelope(payload_str: str, token: str = DISTINCTIVE_TOKEN) -> dict:
    """AC-103's envelope shape: from.eidolon unknown to _ROSTER_EIDOLONS (resolves
    T3 by identity) + trace.tier 'standard' (a non-tier token, falls back to
    identity — still T3). Also carries artifact.kind 'session_handoff' + the
    distinctive objective token, so AC-104 can reuse it verbatim as "the
    quarantined session_handoff crystal produced by AC-103's envelope shape"."""
    objective = f"regression probe distinctive token {token}"
    return _envelope(
        payload_str, eidolon="eidolons-context-kernel", tier="standard",
        kind="session_handoff", thread="handoff-quarantine-thread", objective=objective,
    )


def _nexus_handoff_envelope(payload_str: str, token: str = DISTINCTIVE_TOKEN) -> dict:
    """AC-109's fixture: the nexus composer's real envelope shape — kind
    'ecm/handoff-brief@0.1' plus extra top-level fields topic_key
    'session_handoff' and contains_tool_origin true."""
    objective = f"Session handoff brief for context-lifecycle succession token {token}."
    env = _envelope(
        payload_str, eidolon="eidolons-context-kernel", tier="standard",
        kind="ecm/handoff-brief@0.1", thread="ecm-thread-1", objective=objective,
    )
    env["topic_key"] = "session_handoff"
    env["contains_tool_origin"] = True
    return env


# ---------------------------------------------------------------------------
# AC-101 — happy path, single JSON doc on stdout
# ---------------------------------------------------------------------------


def test_ingest_happy_path_single_json_doc(tmp_path: Path) -> None:
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"findings": [{"id": "F1"}]})
    env = _envelope(payload, eidolon="atlas", tier="T1", kind="scout-report")

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json", "--format", "json"],
        data_dir=data_dir,
    )

    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}.\nOutput:\n{result.output}"
    )
    parsed = json.loads(result.output)
    assert parsed["status"] == "ingested"
    assert parsed["layer"] == "episodic"
    assert "id" in parsed and parsed["id"]


# ---------------------------------------------------------------------------
# AC-102 — session_handoff recallability round-trip (GAP-002 probe)
# ---------------------------------------------------------------------------


def test_session_handoff_roundtrip_recall_by_kind_and_token(tmp_path: Path) -> None:
    """GAP-002 empirical probe (recorded per S-2): FTS5's unicode61 tokenizer
    splits on `_` at BOTH index time (storing 'session_handoff: ...' as the
    two tokens 'session'/'handoff' + the rest) and query time (the CLI's
    `\\w+`-tokenized, per-term-quoted MATCH query re-tokenizes the quoted
    literal "session_handoff" into the same two-token sequence). A quoted
    phrase term therefore phrase-matches the adjacent 'session handoff' token
    pair regardless of the underscore in the raw query string. Confirmed
    empirically by this test passing: querying "session_handoff <token>"
    finds a crystal whose summary is literally "session_handoff: <objective
    containing token>".
    """
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"brief": "handoff body"})
    env = _session_handoff_kind_envelope(payload, token=DISTINCTIVE_TOKEN)

    ingest_result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=data_dir,
    )
    assert ingest_result.exit_code == 0, ingest_result.output
    parsed = json.loads(ingest_result.output)
    assert parsed["status"] == "ingested"

    canonical = Path(data_dir).name  # canonical_project_key = basename(data_dir)
    recall_result = _invoke(
        ["recall", "--query", f"session_handoff {DISTINCTIVE_TOKEN}",
         "--scope-project", canonical],
        data_dir=data_dir,
    )
    assert recall_result.exit_code == 0, recall_result.output
    recall_parsed = json.loads(recall_result.output)

    ids = [r["id"] for r in recall_parsed["records"]]
    assert parsed["id"] in ids, (
        f"session_handoff crystal {parsed['id']!r} not recallable via "
        f"'session_handoff {DISTINCTIVE_TOKEN}'. Recall records: {recall_parsed['records']}"
    )


# ---------------------------------------------------------------------------
# AC-103 / AC-104 — tool-origin quarantine + the quarantine-surfaces-on-recall lock
# ---------------------------------------------------------------------------


def test_tool_origin_envelope_lands_t3_quarantined(tmp_path: Path) -> None:
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"brief": "quarantine leg"})
    env = _tool_origin_session_handoff_envelope(payload)

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=data_dir,
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["trust_tier"] == "T3"
    assert parsed["validation_state"] == "quarantined"


def test_quarantined_session_handoff_still_surfaces_on_recall(tmp_path: Path) -> None:
    """The quarantined crystal produced by AC-103's envelope shape MUST still
    appear in default one-shot recall — quarantine is a triage flag, never a
    recall filter (FINDING-103; no `validation_state` filter exists anywhere
    in the recall waterfall). Both real-world canary legs land quarantined
    (from.eidolon 'eidolons-context-kernel' is not in _ROSTER_EIDOLONS), so
    this regression lock is doubly load-bearing.
    """
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"brief": "quarantine leg recall"})
    env = _tool_origin_session_handoff_envelope(payload, token=DISTINCTIVE_TOKEN)

    ingest_result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=data_dir,
    )
    assert ingest_result.exit_code == 0, ingest_result.output
    parsed = json.loads(ingest_result.output)
    assert parsed["validation_state"] == "quarantined"  # AC-103 precondition re-checked

    canonical = Path(data_dir).name
    recall_result = _invoke(
        ["recall", "--query", f"session_handoff {DISTINCTIVE_TOKEN}",
         "--scope-project", canonical],
        data_dir=data_dir,
    )
    assert recall_result.exit_code == 0, recall_result.output
    recall_parsed = json.loads(recall_result.output)

    ids = [r["id"] for r in recall_parsed["records"]]
    assert parsed["id"] in ids, (
        "quarantine MUST NOT exclude a crystal from default one-shot recall "
        f"(FINDING-103 regression) — recall records: {recall_parsed['records']}"
    )


# ---------------------------------------------------------------------------
# AC-105 — G7 payload-hash mismatch
# ---------------------------------------------------------------------------


def test_payload_hash_mismatch_exits_1(tmp_path: Path) -> None:
    """The envelope is internally consistent (integrity.value == artifact.sha256)
    but the ACTUAL --payload bytes differ from the declared hash — must be
    rejected (G7 binding), even though envelope self-consistency alone passes."""
    data_dir = str(tmp_path / "crystalium_data")
    honest_payload = json.dumps({"findings": [{"id": "F1"}]})
    env = _envelope(honest_payload, eidolon="atlas", tier="T1")  # sha256 over honest payload
    tampered_payload = json.dumps({"findings": [{"id": "EVIL"}]})

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", tampered_payload,
         "--payload-encoding", "json"],
        data_dir=data_dir,
    )
    assert result.exit_code != 0, "Expected non-zero exit for a payload/hash mismatch"

    json_line = _find_json_line(result.output)
    try:
        parsed = json.loads(json_line)
        assert "id" not in parsed, "No crystal id should appear on stdout when ingest rejects"
    except json.JSONDecodeError:
        pass  # no JSON at all -> correct


# ---------------------------------------------------------------------------
# AC-106 / AC-107 / AC-108 — envelope structural/version/parse rejections
# ---------------------------------------------------------------------------


def test_missing_envelope_field_exits_1(tmp_path: Path) -> None:
    """An envelope missing a required ECL field (here: 'integrity') exits 1
    with no JSON document on stdout."""
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"findings": []})
    env = _envelope(payload, drop_fields=("integrity",))

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=data_dir,
    )
    assert result.exit_code != 0

    json_line = _find_json_line(result.output)
    try:
        parsed = json.loads(json_line)
        assert "id" not in parsed
    except json.JSONDecodeError:
        pass


def test_unsupported_envelope_version_exits_1(tmp_path: Path) -> None:
    """An envelope declaring envelope_version '3.0' (outside the 1.x/2.x
    supported line) exits 1 with no JSON document on stdout."""
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"findings": []})
    env = _envelope(payload, envelope_version_override="3.0")

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=data_dir,
    )
    assert result.exit_code != 0

    json_line = _find_json_line(result.output)
    try:
        parsed = json.loads(json_line)
        assert "id" not in parsed
    except json.JSONDecodeError:
        pass


def test_envelope_parse_error_exits_1(tmp_path: Path) -> None:
    """An --envelope argument that is not parseable JSON exits 1 with a clear
    stderr message, never a Python traceback."""
    data_dir = str(tmp_path / "crystalium_data")

    result = _invoke(
        ["ingest", "--envelope", "{not valid json", "--payload", "x"],
        data_dir=data_dir,
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "Error" in result.output or "not valid JSON" in result.output


# ---------------------------------------------------------------------------
# AC-109 — nexus composer envelope shape: extra top-level fields tolerated
# ---------------------------------------------------------------------------


def test_nexus_envelope_shape_extra_fields_tolerated(tmp_path: Path) -> None:
    """An envelope mirroring the nexus composer exactly — kind
    'ecm/handoff-brief@0.1' plus extra top-level fields topic_key
    'session_handoff' and contains_tool_origin true — exits 0 (required-
    fields-only validation tolerates unknown extra top-level fields)."""
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"brief": "nexus composer body"})
    env = _nexus_handoff_envelope(payload)
    assert "topic_key" in env and "contains_tool_origin" in env  # sanity: fixture shape

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=data_dir,
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["status"] == "ingested"


# ---------------------------------------------------------------------------
# AC-110 / AC-111 — scope canonicalization (v1.6) + project_raw preservation
# ---------------------------------------------------------------------------


def test_scope_normalized_to_canonical_key(tmp_path: Path) -> None:
    """scope.project is rewritten to canonical_project_key(CRYSTALIUM_DATA_DIR);
    the result JSON carries scope_normalized: true. NOTE FINDING-107: this is
    an intentional asymmetry vs the `commit` CLI verb, which stores
    --scope-project verbatim (no normalization)."""
    from crystalium.storage.relational import RelationalStore

    data_dir = tmp_path / "crystalium_data"
    payload = json.dumps({"findings": []})
    env = _envelope(payload, thread="thread-X")

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=str(data_dir),
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed.get("scope_normalized") is True

    relational = RelationalStore(db_path=data_dir / "index.sqlite")
    crystal = relational.get_crystal(parsed["id"])
    assert crystal is not None
    assert crystal["scope"]["project"] == data_dir.name  # canonical key = basename(data_dir)


def test_thread_id_preserved_in_project_raw(tmp_path: Path) -> None:
    """The same envelope's thread_id survives verbatim in scope.project_raw."""
    from crystalium.storage.relational import RelationalStore

    data_dir = tmp_path / "crystalium_data"
    payload = json.dumps({"findings": []})
    env = _envelope(payload, thread="thread-X")

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=str(data_dir),
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)

    relational = RelationalStore(db_path=data_dir / "index.sqlite")
    crystal = relational.get_crystal(parsed["id"])
    assert crystal is not None
    assert crystal["scope"]["project_raw"] == "thread-X"


# ---------------------------------------------------------------------------
# AC-112 — --format text prints only the id
# ---------------------------------------------------------------------------


def test_format_text_prints_only_id(tmp_path: Path) -> None:
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"findings": []})
    env = _envelope(payload)

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json", "--format", "text"],
        data_dir=data_dir,
    )
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 1, f"Expected exactly one line of output, got: {lines!r}"
    assert "{" not in lines[0], "JSON output found in text mode"

    import uuid as _uuid

    _uuid.UUID(lines[0])  # raises ValueError if not a valid UUID


# ---------------------------------------------------------------------------
# AC-113 — no summary-quality gate on the ingest path (MCP ingest parity)
# ---------------------------------------------------------------------------


def test_no_summary_gate_on_ingest_path(tmp_path: Path) -> None:
    """An empty objective degenerates the server-composed summary to the bare
    artifact.kind ('scout-report' — 13 chars, 2 alpha words) — this would FAIL
    the v1.6 commit-CLI's hard mechanical quality gate (>= 24 chars, >= 3 alpha
    words) were it applied here. Ingest has NO such gate (D-3): exit 0."""
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"findings": []})
    env = _envelope(payload, objective="")

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        data_dir=data_dir,
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["status"] == "ingested"


# ---------------------------------------------------------------------------
# AC-114 — `ingest --help` — fast path, no heavy imports, no server import
# ---------------------------------------------------------------------------


def test_ingest_help_no_heavy_imports() -> None:
    """`ingest --help` documents the flags and never pulls torch/lance/kuzu —
    nor even `crystalium.server` (the `_handle_ingest` reuse point), which is
    imported lazily INSIDE the command body, never at module scope.

    Compares sys.modules before/after (rather than asserting absolute absence)
    because other tests in the same pytest process may have already imported
    these heavy deps — the invariant under test is that `--help` itself does
    not trigger a NEW import of any of them.
    """
    heavy_modules = ("torch", "sentence_transformers", "lancedb", "kuzu")
    before = set(sys.modules)

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", "--help"], catch_exceptions=False)

    after = set(sys.modules)
    newly_imported = after - before

    assert result.exit_code == 0, result.output
    assert "--envelope" in result.output
    assert "--payload" in result.output
    assert "--payload-encoding" in result.output

    heavy_newly_imported = [m for m in heavy_modules if m in newly_imported]
    assert not heavy_newly_imported, (
        f"{heavy_newly_imported} newly imported merely by `ingest --help` — "
        "lazy-import discipline (D-G2b) violated"
    )
    assert "crystalium.server" not in newly_imported, (
        "`ingest --help` must not import crystalium.server; _handle_ingest is "
        "imported lazily inside the command body, only on actual invocation"
    )


# ---------------------------------------------------------------------------
# AC-115 — MCP tool surface stays at exactly 9 tools (ingest ships CLI-side only)
# ---------------------------------------------------------------------------


def test_mcp_tool_surface_unchanged_9_tools() -> None:
    from crystalium.server import build_tool_manifest

    tools = build_tool_manifest()
    names = [t.get("name") for t in tools] if tools and isinstance(tools[0], dict) else tools
    assert len(tools) == 9, (
        f"Expected exactly 9 MCP tools (ingest ships CLI-side only at 1.8.0); "
        f"got {len(tools)}: {names}"
    )


# ---------------------------------------------------------------------------
# AC-116 — CRYSTALIUM_CALLER_TIER is NEVER read; tier is envelope-derived
# ---------------------------------------------------------------------------


def test_caller_tier_env_ignored_tier_is_envelope_derived(tmp_path: Path) -> None:
    """An envelope resolving to T3 by identity (unknown from.eidolon), ingested
    with CRYSTALIUM_CALLER_TIER=T0 set in the environment, MUST still report
    trust_tier T3 — the env var is never read on the ingest path (unlike
    commit's CRYSTALIUM_CALLER_TIER default T0 / recall's default T1)."""
    data_dir = str(tmp_path / "crystalium_data")
    payload = json.dumps({"findings": []})
    env = _envelope(payload, eidolon="totally-unknown-source", tier="standard")

    result = _invoke(
        ["ingest", "--envelope", json.dumps(env), "--payload", payload,
         "--payload-encoding", "json"],
        env={"CRYSTALIUM_CALLER_TIER": "T0"},
        data_dir=data_dir,
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["trust_tier"] == "T3", (
        "CRYSTALIUM_CALLER_TIER must NEVER be read on the ingest path — tier is "
        "envelope-derived MIN-trust (resolve_caller_tier), unlike commit/recall's "
        "env-var defaults"
    )
