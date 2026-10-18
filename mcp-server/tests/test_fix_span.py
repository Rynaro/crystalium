"""Issue 73 acceptance anchors: bounded repairs and enforcement before persistence."""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from crystalium.aetheryte.redact import Redactor
from crystalium.aetheryte.retrieve import Aetheryte
from crystalium.composer import Composer
from crystalium.config import Config
from crystalium.enforcement import CrystaliumEnforcementError, Enforcement
from crystalium.fix_span import FixSpan, render_fix_span
from crystalium.layers.episodic import EpisodicLayer
from crystalium.layers.execution import ExecutionLayer
from crystalium.layers.procedural import ProceduralLayer
from crystalium.layers.semantic import SemanticLayer
from crystalium.schemas import Scope
from crystalium.scope import canonical_project_key
from crystalium.server import _handle_ingest, _handle_update
from crystalium.storage.relational import RelationalStore
from crystalium.trust import Tier


def repair() -> dict:
    """Complete repair with a historical file anchor."""
    return {
        "kind": "fix_span",
        "fix_span": {
            "schema_version": "1.0",
            "symptom": "Recall loses a repair",
            "root_cause": "Approximate dedup drops fields",
            "fix_pattern": "Preserve structured facts",
            "anchors": [{"kind": "file", "value": "missing/repair.py"}],
            "constraints": [],
            "locator": {"kind": "handoff", "value": "issue-73"},
        },
    }


def test_fix_span_flags_default_off() -> None:
    """AC-006/010: default deployments do not enable typed writes/extraction."""
    config = Config()
    assert config.fix_span_enabled is False
    assert config.fix_span_extractor_enabled is False


@pytest.mark.parametrize("value", ["../../etc/passwd", "/etc/passwd", "C:\\a", "\\\\host\\a"])
def test_fix_span_anchor_escape(tmp_path: Path, value: str) -> None:
    """AC-004: metadata confinement rejects escape and foreign path spellings."""
    guard = Enforcement(Config(repo_root=tmp_path, fix_span_enabled=True))
    payload = repair()
    payload["fix_span"]["anchors"][0]["value"] = value
    with pytest.raises(CrystaliumEnforcementError) as exc:
        guard.validate_structured_payload(payload, Tier.T1, "episodic")
    assert exc.value.reason_code == "path"


def test_fix_span_missing_anchor(tmp_path: Path) -> None:
    """AC-002: an explicit empty anchor set rejects before model fallback."""
    guard = Enforcement(Config(repo_root=tmp_path, fix_span_enabled=True))
    payload = repair()
    payload["fix_span"]["anchors"] = []
    with pytest.raises(CrystaliumEnforcementError) as exc:
        guard.validate_structured_payload(payload, Tier.T1, "episodic")
    assert exc.value.reason_code == "missing_anchor"


@pytest.fixture
def components(tmp_path: Path, tmp_blob_store: Any, tmp_relational_store: Any) -> dict:
    """Real persistence and guards; admission stub isolates typed write behavior."""
    config = Config(
        repo_root=tmp_path,
        data_dir=tmp_path / "project",
        fix_span_enabled=True,
        fix_span_extractor_enabled=True,
        rate_limit_per_minute=10000,
    )
    enforcement = Enforcement(config)
    redactor = Redactor(config)
    gate = MagicMock()
    gate.propose_semantic.return_value = MagicMock(decision="admit")
    gate.propose_procedural.return_value = MagicMock(decision="admit")

    def score(record: Any, now: datetime | None = None) -> float:
        return 0.1

    common = dict(
        blob_store=tmp_blob_store,
        relational=tmp_relational_store,
        enforcement=enforcement,
        importance_fn=score,
    )
    layers = {
        "episodic": EpisodicLayer(**common, vector_store=None, graph_store=None, redactor=redactor),
        "semantic": SemanticLayer(
            **common, vector_store=None, graph_store=None, redactor=redactor, gate=gate
        ),
        "procedural": ProceduralLayer(**common, gate=gate, redactor=redactor),
        "execution": ExecutionLayer(**common),
    }
    return dict(
        config=config,
        enforcement=enforcement,
        store=tmp_relational_store,
        blob=tmp_blob_store,
        gate=gate,
        layers=layers,
        score=score,
        redactor=redactor,
    )


def write(
    components: dict, payload: dict | None = None, tier: Tier = Tier.T1, layer: str = "episodic"
) -> dict:
    """Exercise real layer entry points with authoritative caller identity."""
    payload = deepcopy(payload if payload is not None else repair())
    payload.setdefault("scope", {"project": canonical_project_key(components["config"].data_dir)})
    adapter = components["layers"][layer]
    if layer == "execution":
        return adapter.checkpoint(payload, tier)
    provenance = {"source": "verified_agent", "created_at": datetime.now(UTC).isoformat()}
    return adapter.commit(payload, provenance, tier)


def ingest(components: dict, payload: dict, tier: str = "T1") -> dict:
    """Use the established ECL envelope fixture with bound JSON payload hash."""
    import json

    from test_ingest_handler import _envelope

    raw = json.dumps(payload)
    layers = components["layers"]
    return _handle_ingest(
        {"envelope": _envelope(raw, tier=tier), "payload": raw, "payload_encoding": "json"},
        layers["episodic"],
        layers["semantic"],
        layers["procedural"],
        layers["execution"],
        components["config"],
    )


def update(components: dict, cid: str, patch: dict, tier: Tier = Tier.T1) -> dict:
    """Exercise generic and semantic update dispatch."""
    layers = components["layers"]
    return _handle_update(
        {"id": cid, "patch": patch},
        layers["episodic"],
        layers["semantic"],
        layers["procedural"],
        layers["execution"],
        components["enforcement"],
        components["store"],
        tier,
    )


@pytest.mark.parametrize("layer", ["episodic", "semantic", "procedural", "execution"])
def test_fix_span_valid_t1_roundtrip(components: dict, layer: str) -> None:
    """AC-001: all layers retain complete typed facts with T1 authoritative tier."""
    result = write(components, layer=layer)
    row = components["store"].get_crystal(result["id"])
    assert row["trust_tier"] == "T1"
    assert row["fix_span"] == repair()["fix_span"]
    assert row["kind"] == "fix_span"


@pytest.mark.parametrize("layer", ["episodic", "semantic", "procedural", "execution"])
@pytest.mark.parametrize("failure", ["disabled", "anchor", "cap", "path", "extra"])
def test_fix_span_entrypoints(components: dict, layer: str, failure: str) -> None:
    """AC-015: rejected direct writes do not touch blobs, rows, or admissions."""
    payload = repair()
    expected = {
        "disabled": "fix_span_disabled",
        "anchor": "missing_anchor",
        "cap": "cap",
        "path": "path",
        "extra": "malformed",
    }[failure]
    if failure == "disabled":
        components["config"].fix_span_enabled = False
    elif failure == "anchor":
        payload["fix_span"]["anchors"] = []
    elif failure == "cap":
        payload["fix_span"]["symptom"] = "x" * 201
    elif failure == "path":
        payload["fix_span"]["anchors"][0]["value"] = "../../etc/passwd"
    else:
        payload["fix_span"]["trust_tier"] = "T0"
    with pytest.raises(CrystaliumEnforcementError) as exc:
        write(components, payload, layer=layer)
    assert exc.value.reason_code == expected
    with components["store"]._connect() as conn:
        assert conn.execute("SELECT count(*) FROM crystals").fetchone()[0] == 0
    components["gate"].propose_semantic.assert_not_called()
    components["gate"].propose_procedural.assert_not_called()
    assert not components["blob"].root.exists()


@pytest.mark.parametrize("field,cap", [("symptom", 200), ("root_cause", 300), ("fix_pattern", 400)])
def test_fix_span_caps(components: dict, field: str, cap: int) -> None:
    """AC-003: exact limits accepted; one character overflow rejected as cap."""
    payload = repair()
    payload["fix_span"][field] = "x" * cap
    write(components, payload)
    payload["fix_span"][field] += "x"
    with pytest.raises(CrystaliumEnforcementError) as exc:
        write(components, payload)
    assert exc.value.reason_code == "cap"


def test_fix_span_historical_and_symbol_anchors(components: dict, tmp_path: Path) -> None:
    """AC-005: normalize confined history; symbols preserve qualified spelling."""
    payload = repair()
    payload["fix_span"]["anchors"] = [
        {"kind": "file", "value": "./gone//old.py"},
        {"kind": "symbol", "value": "Store::update"},
    ]
    result = write(components, payload)
    assert components["store"].get_crystal(result["id"])["fix_span"]["anchors"] == [
        {"kind": "file", "value": "gone/old.py"},
        {"kind": "symbol", "value": "Store::update"},
    ]
    (tmp_path / "escape").symlink_to("/etc", target_is_directory=True)
    payload["fix_span"]["anchors"] = [{"kind": "file", "value": "escape/nonexistent"}]
    with pytest.raises(CrystaliumEnforcementError) as exc:
        write(components, payload)
    assert exc.value.reason_code == "path"
    components["config"].repo_root = None
    with pytest.raises(CrystaliumEnforcementError) as exc:
        write(components)
    assert exc.value.reason_code == "path"


@pytest.mark.parametrize("layer", ["episodic", "semantic", "procedural", "execution"])
@pytest.mark.parametrize(
    "patch",
    [
        {"fix_span": None},
        {"kind": None},
        {"kind": "opaque"},
        {"fix_span": {**repair()["fix_span"], "anchors": []}},
        {"fix_span": {**repair()["fix_span"], "extra": "no"}},
    ],
)
def test_fix_span_update_atomic(components: dict, layer: str, patch: dict) -> None:
    """AC-007/015: merged candidate rejects before supersession on every layer."""
    result = write(components, layer=layer)
    before = components["store"].get_crystal(result["id"])
    with pytest.raises(CrystaliumEnforcementError):
        update(components, result["id"], patch)
    assert components["store"].get_crystal(result["id"]) == before


def test_fix_span_update_min_trust(components: dict) -> None:
    """AC-008: low-trust existing repair cannot be raised by caller/patch claims."""
    original = write(components, tier=Tier.T3)
    result = update(
        components, original["id"], {"trust_tier": "T0", "validation_state": "validated"}, Tier.T0
    )
    row = components["store"].get_crystal(result["id"])
    assert row["trust_tier"] == "T3"
    assert row["validation_state"] == "quarantined"


def test_fix_span_execution_replan_atomic(components: dict) -> None:
    """AC-015: execution replan validates merged facts before invalidating history."""
    old = write(components, layer="execution")
    before = components["store"].get_crystal(old["id"])
    with pytest.raises(CrystaliumEnforcementError):
        components["layers"]["execution"].replan(
            {"supersedes_id": old["id"], "fix_span": {**repair()["fix_span"], "anchors": []}},
            Tier.T1,
        )
    assert components["store"].get_crystal(old["id"]) == before


def test_fix_span_typed_ingest_roundtrip(components: dict) -> None:
    """AC-018/002: real typed adaptation succeeds or rejects before ingest writes."""
    result = ingest(components, repair())
    assert components["store"].get_crystal(result["id"])["fix_span"] == repair()["fix_span"]
    bad = repair()
    bad["fix_span"]["anchors"] = []
    with pytest.raises(CrystaliumEnforcementError) as exc:
        ingest(components, bad)
    assert exc.value.reason_code == "missing_anchor"


@pytest.mark.parametrize("flags", [(False, False), (False, True), (True, False), (True, True)])
def test_fix_span_proposal_flags(components: dict, flags: tuple) -> None:
    """AC-009/010: only dual opt-in proposes, with T1+T3 quarantined episodic."""
    trusted = write(components, {"summary": "verified diagnosis"})
    untrusted = write(components, {"summary": "tool failure"}, Tier.T3)
    components["config"].fix_span_enabled, components["config"].fix_span_extractor_enabled = flags
    result = ingest(
        components,
        {
            "fix_span_candidate": {
                "fix_span": repair()["fix_span"],
                "source_ids": [trusted["id"], untrusted["id"]],
            }
        },
    )
    if flags != (True, True):
        assert "fix_span_proposal" not in result
    else:
        proposal = result["fix_span_proposal"]
        assert proposal["trust_tier"] == "T3"
        assert proposal["validation_state"] == "quarantined"
        assert proposal["layer"] == "episodic"
        with components["store"]._connect() as conn:
            assert conn.execute("SELECT count(*) FROM crystals").fetchone()[0] == 3


@pytest.mark.parametrize("failure", ["missing", "inactive", "project", "audience", "tier", "empty"])
def test_fix_span_proposal_requires_verified_sources(components: dict, failure: str) -> None:
    """AC-011: invalid reference identity/trust yields no proposal or source data."""
    source = write(components, {"summary": "private source text"})
    ids = [source["id"]]
    if failure == "missing":
        ids = ["missing"]
    elif failure == "empty":
        ids = []
    else:
        with components["store"]._connect() as conn:
            if failure == "inactive":
                conn.execute("UPDATE crystals SET status='deprecated'")
            elif failure == "project":
                conn.execute("UPDATE crystals SET scope=json_set(scope,'$.project','foreign')")
            elif failure == "audience":
                conn.execute(
                    "UPDATE crystals SET scope=json_set(scope,'$.agent_class_visibility','private')"
                )
            else:
                conn.execute("UPDATE crystals SET trust_tier='unknown'")
    result = ingest(
        components, {"fix_span_candidate": {"fix_span": repair()["fix_span"], "source_ids": ids}}
    )
    assert "fix_span_proposal" not in result
    assert "private source text" not in str(result)


def test_fix_span_proposal_recommit(components: dict) -> None:
    """AC-020: forged display tier cannot establish authority; stale refs reject."""
    source = write(components, {"summary": "verified diagnosis"})
    result = ingest(
        components,
        {
            "fix_span_candidate": {
                "fix_span": repair()["fix_span"],
                "source_ids": [source["id"]],
            }
        },
        tier="T3",
    )
    proposal = result["fix_span_proposal"]
    proposal["trust_tier"] = "T0"
    committed = write(components, proposal, Tier.T0)
    row = components["store"].get_crystal(committed["id"])
    assert row["trust_tier"] == "T3"
    assert row["validation_state"] == "quarantined"
    components["store"].mark_superseded(source["id"], "next", datetime.now(UTC))
    with pytest.raises(CrystaliumEnforcementError):
        write(components, proposal, Tier.T0)


@pytest.mark.parametrize("incoming", [True, False])
@pytest.mark.parametrize("layer", ["episodic", "semantic"])
def test_fix_span_dedup_survival(components: dict, incoming: bool, layer: str) -> None:
    """AC-012: neither incoming nor target structured facts are summary-deduped."""
    target = write(components, {"summary": "same summary"} if incoming else repair(), layer=layer)
    vector = MagicMock()
    vector.embed.return_value = [1.0]
    vector.dense_search.return_value = [{"id": target["id"], "_distance": 0.0}]
    adapter = components["layers"][layer]
    adapter.vector_store = vector
    adapter.dedup_merge = True
    result = write(components, repair() if incoming else {"summary": "same summary"}, layer=layer)
    assert result["status"] == "committed"
    assert result["id"] != target["id"]


def test_fix_span_dream_preserves_structured(components: dict) -> None:
    """AC-013: repeated Dream consolidate/prune never admits or erases typed facts."""
    from crystalium.dream.worker import DreamWorker

    result = write(components)
    record = components["store"].get_crystal(result["id"])
    worker = DreamWorker(
        components["store"],
        None,
        None,
        components["enforcement"],
        components["gate"],
        components["score"],
        prune_thresholds={"episodic": 1.0},
    )
    for _ in range(2):
        assert worker._consolidate([record], datetime.now(UTC)) is None
        worker._prune(datetime.now(UTC))
    assert components["store"].get_crystal(result["id"])["status"] == "active"
    components["gate"].propose_semantic.assert_not_called()


def test_fix_span_render_when_disabled(components: dict) -> None:
    """AC-014/019: stored facts render with flags OFF; returned text equals budget text."""
    payload = repair()
    payload["fix_span"]["constraints"] = ["never erase", "retain trust"]
    payload["scope"] = {
        "project": canonical_project_key(components["config"].data_dir),
        "sensitivity_tag": "secrets",
    }
    payload["fix_span"]["root_cause"] += " password=secret123"
    result = write(components, payload)
    components["config"].fix_span_enabled = False
    components["config"].fix_span_extractor_enabled = False
    vector = MagicMock()
    vector.embed.return_value = []
    graph = MagicMock()
    graph.neighbor_expand.return_value = []
    composer = Composer(components["config"], tokenizer=lambda text: len(text.split()))
    aetheryte = Aetheryte(
        components["store"],
        vector,
        graph,
        components["enforcement"],
        components["redactor"],
        components["score"],
        composer,
        recall_active_only=True,
    )
    out = aetheryte.recall(
        Scope(project=canonical_project_key(components["config"].data_dir)),
        "Recall",
        10,
        None,
        Tier.T1,
    )
    record = next(row for row in out.records if row.id == result["id"])
    expected = components["redactor"].redact(render_fix_span(payload), "secrets")
    assert record.summary == expected
    assert "secret123" not in record.summary
    assert "never erase; retain trust" in record.summary
    assert "handoff:issue-73" in record.summary
    assert out.total_tokens == len(record.summary.split())

    from crystalium.assembly import assemble_context
    from crystalium.schemas import AssembleContextRequest

    request = AssembleContextRequest(
        scope={"project": canonical_project_key(components["config"].data_dir)}, query="Recall"
    )
    closed = assemble_context(request, aetheryte, components["config"], Tier.T1)
    assert closed.records == [] and closed.injected_token_count == 0
    opened = assemble_context(
        request.model_copy(update={"needs_memory": True}),
        aetheryte,
        components["config"],
        Tier.T1,
    )
    assert opened.records[0].summary == record.summary
    assert opened.injected_token_count == len(record.summary.split())


def test_fix_span_migration_legacy(tmp_path: Path, sample_crystal: Any) -> None:
    """AC-016: a pre-feature table migrates twice without losing legacy content."""
    import sqlite3

    from crystalium.storage.relational import _DDL

    db = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            _DDL.replace("    kind             TEXT,\n", "").replace(
                "    fix_span         TEXT,\n", ""
            )
        )
    store = RelationalStore(db)
    legacy = sample_crystal(layer="episodic")
    store.insert_crystal(legacy)
    RelationalStore(db)
    row = store.get_crystal(legacy["id"])
    assert row["summary"] == legacy["summary"]
    assert "kind" not in row and "fix_span" not in row


def test_fix_span_telemetry(components: dict) -> None:
    """AC-017: finally outcomes carry fixed reasons, never repair content labels."""
    records = []
    components["enforcement"].record = lambda *args, **kwargs: records.append((args, kwargs))
    write(components)
    payload = repair()
    payload["fix_span"]["anchors"] = []
    with pytest.raises(CrystaliumEnforcementError):
        write(components, payload)
    old = write(components, tier=Tier.T3)
    update(components, old["id"], {}, Tier.T0)
    assert {record[0][4] for record in records} >= {"accepted", "rejected", "downgraded"}
    assert "missing_anchor" in str(records)
    assert "Recall loses a repair" not in str(records)
    assert "missing/repair.py" not in str(records)


def test_fix_span_legacy_opaque_kind(components: dict) -> None:
    """Ordinary old artifact metadata is unaffected when the new flags are OFF."""
    components["config"].fix_span_enabled = False
    result = write(components, {"kind": "scout-report", "summary": "opaque report"})
    assert components["store"].get_crystal(result["id"])["summary"] == "opaque report"


def test_fix_span_schema_model_parity() -> None:
    """Nested facts enforce the same required shape in both schema and model."""
    import json

    from jsonschema import Draft202012Validator

    schema = json.loads(Path("schemas/fix-span.v1.json").read_text())
    validator = Draft202012Validator(schema)
    valid = repair()["fix_span"]
    validator.validate(valid)
    assert FixSpan.model_validate(valid).model_dump() == valid
    for field in ("constraints", "locator", "anchors"):
        malformed = {key: value for key, value in valid.items() if key != field}
        assert list(validator.iter_errors(malformed))
        with pytest.raises(ValidationError):
            FixSpan.model_validate(malformed)


@pytest.mark.parametrize("old_tier,new_tier", [(Tier.T1, Tier.T3), (Tier.T3, Tier.T1)])
def test_fix_span_update_unions_sources(components: dict, old_tier: Tier, new_tier: Tier) -> None:
    """AC-008/020: neither incoming nor original low-trust source can be dropped."""
    old_source = write(components, {"summary": "old evidence"}, old_tier)
    new_source = write(components, {"summary": "new evidence"}, new_tier)
    payload = repair()
    payload["encoding_context"] = {"fix_span_sources": [old_source["id"]]}
    original = write(components, payload, Tier.T0)
    revised = update(
        components,
        original["id"],
        {
            "encoding_context": {"fix_span_sources": [new_source["id"]]},
        },
        Tier.T0,
    )
    row = components["store"].get_crystal(revised["id"])
    assert row["trust_tier"] == "T3"
    assert row["validation_state"] == "quarantined"
    assert set(row["encoding_context"]["fix_span_sources"]) == {old_source["id"], new_source["id"]}
    for _ in range(3):
        repeated = update(components, row["id"], {}, Tier.T0)
        row = components["store"].get_crystal(repeated["id"])
        assert row["encoding_context"]["fix_span_sources"] == [old_source["id"], new_source["id"]]


def test_fix_span_update_source_ceiling_atomic(components: dict) -> None:
    """AC-008/015: new T3 evidence cannot revise a semantic span or supersede it."""
    original = write(components, layer="semantic")
    source = write(components, {"summary": "untrusted new evidence"}, Tier.T3)
    before = components["store"].get_crystal(original["id"])
    with pytest.raises(CrystaliumEnforcementError):
        components["layers"]["semantic"].update(
            original["id"],
            {
                "encoding_context": {"fix_span_sources": [source["id"]]},
            },
            "add evidence",
            Tier.T0,
        )
    assert components["store"].get_crystal(original["id"]) == before


def test_fix_span_execution_preserves_sources_and_quarantine(components: dict) -> None:
    """Execution typed checkpoint/replan persist validated refs and conservative state."""
    source = write(components, {"summary": "verified evidence"})
    payload = repair()
    payload["encoding_context"] = {"fix_span_sources": [source["id"]]}
    result = write(components, payload, layer="execution")
    row = components["store"].get_crystal(result["id"])
    assert row["encoding_context"]["fix_span_sources"] == [source["id"]]
    with components["store"]._connect() as conn:
        conn.execute(
            "UPDATE crystals SET validation_state='quarantined' WHERE id=?", (result["id"],)
        )
    revised = components["layers"]["execution"].replan({"supersedes_id": result["id"]}, Tier.T1)
    row = components["store"].get_crystal(revised["id"])
    assert row["validation_state"] == "quarantined"
    assert row["encoding_context"]["fix_span_sources"]


def test_fix_span_flags_env_yaml(tmp_path: Path, monkeypatch: Any) -> None:
    """Both switches are explicit independent env/YAML settings, with OFF defaults."""
    monkeypatch.setenv("CRYSTALIUM_FIX_SPAN_ENABLED", "true")
    monkeypatch.setenv("CRYSTALIUM_FIX_SPAN_EXTRACTOR_ENABLED", "false")
    assert Config.from_env().fix_span_enabled is True
    assert Config.from_env().fix_span_extractor_enabled is False
    yaml = tmp_path / "config.yaml"
    yaml.write_text("fix_span_enabled: true\nfix_span_extractor_enabled: true\n")
    configured = Config.from_yaml(yaml)
    assert configured.fix_span_enabled is True
    assert configured.fix_span_extractor_enabled is True


def test_fix_span_flags_off_no_source_reads(components: dict, monkeypatch: Any) -> None:
    """AC-010: disabled extraction performs zero additional provenance reads."""
    components["config"].fix_span_extractor_enabled = False
    source_read = MagicMock(side_effect=AssertionError("unexpected source read"))
    monkeypatch.setattr(components["store"], "get_active_scoped_crystal", source_read)
    result = ingest(
        components,
        {
            "fix_span_candidate": {
                "fix_span": repair()["fix_span"],
                "source_ids": ["not-read"],
            }
        },
    )
    assert "fix_span_proposal" not in result
    source_read.assert_not_called()


def test_fix_span_admission_gates_preserved(components: dict) -> None:
    """Typed validation does not admit semantic/procedural records by itself."""
    from crystalium.gate import PromotionGate

    gate = PromotionGate(
        config=components["config"],
        relational=components["store"],
        enforcement=components["enforcement"],
    )
    components["layers"]["semantic"].gate = gate
    components["layers"]["procedural"].gate = gate
    semantic = write(components, layer="semantic")
    procedural = write(components, layer="procedural")
    assert semantic["status"] != "committed"
    row = components["store"].get_crystal(procedural["id"])
    assert row["status"] == "candidate"
    assert row["validation_state"] == "candidate"


def test_fix_span_mismatched_or_null_rejected(components: dict) -> None:
    """Explicit malformed structured intent cannot silently degrade to opaque data."""
    for payload in (
        {"fix_span": None},
        {"fix_span": repair()["fix_span"]},
        {"kind": "opaque", "fix_span": repair()["fix_span"]},
    ):
        with pytest.raises(CrystaliumEnforcementError) as exc:
            write(components, payload)
        assert exc.value.reason_code == "malformed"
