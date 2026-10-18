"""Acceptance anchors for task-conditional injection (issue 71)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from crystalium.assembly import assemble_context, select_records
from crystalium.config import Config
from crystalium.schemas import AssembleContextRequest, CrystalSummary
from crystalium.trust import Tier


def record(name: str, score: float, summary: str = "background context") -> CrystalSummary:
    return CrystalSummary(
        id=name,
        score=score,
        summary=summary,
        layer="semantic",
        trust_tier="T1",
        validation_state="validated",
        importance=0.5,
        last_access=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "mode,needs,failure,enabled",
    [
        ("gated", False, False, False),
        ("gated", True, False, True),
        ("gated", False, True, True),
        ("gated", True, True, True),
        ("off", True, True, False),
        ("always_on", False, False, True),
    ],
)
def test_gate_precedes_retrieval(tmp_path, mode, needs, failure, enabled):
    config = Config(data_dir=tmp_path, memory_injection_mode=mode)
    retrieval = MagicMock()
    retrieval.recall.return_value = SimpleNamespace(records=[record("a", 1)])
    retrieval.composer.tokenize.side_effect = lambda text: len(text.split())
    request = AssembleContextRequest(
        scope={"project": "p"},
        query="query",
        needs_memory=needs,
        prior_failure=failure,
    )
    result = assemble_context(request, retrieval, config, Tier.T2)
    assert result.gate_open is enabled
    assert result.injected_token_count == (2 if enabled else 0)
    assert len(result.records) == int(enabled)
    assert retrieval.recall.call_count == int(enabled)
    if not enabled:
        retrieval.composer.assert_not_called()
        retrieval.composer.tokenize.assert_not_called()


@pytest.mark.parametrize("k", [1, 2, 3, 10])
def test_final_cap_and_bounded_overfetch(tmp_path, k):
    retrieval = MagicMock()
    retrieval.recall.return_value = SimpleNamespace(records=[record(str(i), 1) for i in range(20)])
    retrieval.composer.tokenize.return_value = 2
    result = assemble_context(
        AssembleContextRequest(scope={"project": "p"}, query="q", k=k, needs_memory=True),
        retrieval,
        Config(data_dir=tmp_path, recall_relevance_primary=False),
        Tier.T3,
    )
    assert len(result.records) == k
    assert result.injected_token_count == k * 2
    kwargs = retrieval.recall.call_args.kwargs
    assert k <= kwargs["k"] <= 100
    assert kwargs["caller_tier"] == Tier.T3
    assert kwargs["scope"].project == "p"


def test_anchored_bands_preserve_stronger_relevance():
    records = [
        record("strong", 1),
        record("fix", 0.89, "Fixed parser; tests pass."),
        record("near", 0.9, "Resolved failure."),
        record("chain", 0.81, "Fix applied."),
    ]
    selected = select_records(records)
    assert [r.id for r in selected] == ["near", "strong", "chain", "fix"]
    assert select_records(records) == selected


def test_zero_score_and_exact_ties():
    assert [
        r.id
        for r in select_records(
            [
                record("a", 0),
                record("b", 0, "Fixed."),
                record("c", 0.01),
            ]
        )
    ] == ["c", "b", "a"]


@pytest.mark.parametrize(
    "updates",
    [
        {"k": 0},
        {"k": 101},
        {"needs_memory": "false"},
        {"prior_failure": 1},
        {"mode": "off"},
        {"scope": {"project": " "}},
        {"layers": []},
        {"layers": ["semantic", "semantic"]},
    ],
)
def test_request_rejects_invalid_input(updates):
    with pytest.raises(ValidationError):
        AssembleContextRequest.model_validate({"scope": {"project": "p"}, "query": "q", **updates})


def test_config_modes_and_default(tmp_path, monkeypatch):
    assert Config(data_dir=tmp_path).memory_injection_mode == "gated"
    for mode in ("gated", "off", "always_on"):
        assert (
            Config._from_dict(
                {"data_dir": str(tmp_path), "memory_injection_mode": mode}
            ).memory_injection_mode
            == mode
        )
    with pytest.raises(ValueError, match="memory_injection_mode"):
        Config(data_dir=tmp_path, memory_injection_mode="sometimes")
    monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRYSTALIUM_MEMORY_INJECTION_MODE", "off")
    assert Config.from_env().memory_injection_mode == "off"


@pytest.mark.parametrize("relevance_primary", [True, False])
def test_protected_recall_project_redaction_and_cap(tmp_path, relevance_primary):
    from test_recall_starvation import _build_aetheryte, _crystal_dict

    from crystalium.storage.relational import RelationalStore

    store = RelationalStore(db_path=tmp_path / "memory.sqlite")
    for i in range(15):
        crystal = _crystal_dict(id=f"local-{i}", project="p", summary="parser Fixed failure")
        crystal["scope"]["sensitivity_tag"] = "secrets"
        crystal["summary"] += " password=private-credential"
        store.insert_crystal(crystal)
    store.insert_crystal(_crystal_dict(id="foreign", project="other", summary="parser Fixed"))
    retrieval = _build_aetheryte(tmp_path, store, recall_relevance_primary=relevance_primary)
    tier_guard = retrieval.enforcement.assert_tier_allowed
    retrieval.enforcement.assert_tier_allowed = MagicMock(wraps=tier_guard)
    result = assemble_context(
        AssembleContextRequest(scope={"project": "p"}, query="parser", needs_memory=True, k=2),
        retrieval,
        Config(data_dir=tmp_path / "config"),
        Tier.T3,
    )
    assert len(result.records) == 2
    assert all(r.id.startswith("local-") for r in result.records)
    assert all(
        "private-credential" not in r.summary and "REDACTED" in r.summary for r in result.records
    )
    assert result.injected_token_count == sum(
        retrieval.composer.tokenize(r.summary) for r in result.records
    )
    retrieval.enforcement.assert_tier_allowed.assert_called_once_with(
        "crystalium.recall", "episodic", Tier.T3, "recall"
    )
    assert all(r.trust_tier == "T1" for r in result.records)


def test_registered_mcp_dispatch_and_schema(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator
    from test_server import _drive_call_tool

    from crystalium.server import _build_server, build_tool_manifest

    monkeypatch.setenv("CRYSTALIUM_SKIP_SLOW", "1")
    config = Config(data_dir=tmp_path)
    tool = next(t for t in build_tool_manifest(config) if t["name"] == "assemble_context")
    assert tool["inputSchema"]["properties"]["k"]["default"] == 3
    request = AssembleContextRequest(scope={"project": "p"}, query="q")
    schema_root = Path("schemas")
    for stem, value in [("request", request.model_dump(mode="json", exclude_none=True))]:
        Draft202012Validator(
            json.loads((schema_root / f"assemble-context-{stem}.v1.json").read_text())
        ).validate(value)
    server, _ = _build_server(config)
    response = _drive_call_tool(
        server, "assemble_context", request.model_dump(mode="json", exclude_none=True)
    )
    assert not response.is_error
    payload = json.loads(response.content[0].text)
    assert payload["records"] == []
    assert payload["injected_token_count"] == 0
    Draft202012Validator(
        json.loads((schema_root / "assemble-context-result.v1.json").read_text())
    ).validate(payload)
    response = _drive_call_tool(
        server,
        "assemble_context",
        {"scope": {"project": "p"}, "query": "q", "needs_memory": "false"},
    )
    assert response.is_error


def test_fixture_comparison_is_deterministic_and_labeled():
    from evals.conditional_injection import run_comparison

    first = run_comparison()
    assert first == run_comparison()
    assert "synthetic" in first["measurement"]
    modes = first["modes"]
    assert modes["always_on"]["k"] == 10
    assert modes["gated"]["k"] == 3
    assert modes["off"]["injected_token_count"] == 0
    assert modes["off"]["resolve_rate"] == 0
    assert modes["gated"]["resolve_rate"] == modes["always_on"]["resolve_rate"] == 1
    assert modes["gated"]["injected_token_count"] < modes["always_on"]["injected_token_count"]
    assert modes["gated"]["distraction_rate"] < modes["always_on"]["distraction_rate"]
    assert modes["gated"]["resolve_denominator"] == 2


def test_short_fix_preference_uses_overfetch_at_k_one(tmp_path):
    retrieval = MagicMock()
    retrieval.composer.tokenize.side_effect = lambda text: len(text.split())
    retrieval.recall.return_value = SimpleNamespace(
        records=[
            record("background", 1, "Background."),
            record("long", 0.98, "Fixed " + "details " * 129),
            record("fix", 0.95, "Fixed parser."),
        ]
    )
    result = assemble_context(
        AssembleContextRequest(scope={"project": "p"}, query="parser", k=1, prior_failure=True),
        retrieval,
        Config(data_dir=tmp_path),
        Tier.T2,
    )
    assert [r.id for r in result.records] == ["fix"]
    assert retrieval.recall.call_args.kwargs["k"] == 10
    assert result.injected_token_count == 2
