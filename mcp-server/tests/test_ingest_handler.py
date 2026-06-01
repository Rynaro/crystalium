"""W7 C4 — crystalium.ingest handler (commits through the chokepoint).

Integration via live components (episodic commit uses BM25 path; runs under
CRYSTALIUM_SKIP_SLOW).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_ingest_handler.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crystalium.config import Config
from crystalium.ecl import compute_sha256
from crystalium.enforcement import CrystaliumEnforcementError
from crystalium.schemas import Scope
from crystalium.server import _build_components, _handle_ingest
from crystalium.trust import Tier


def _components(tmp_path: Path):
    cfg = Config(data_dir=tmp_path / "ing", rate_limit_per_minute=1_000_000)
    return _build_components(cfg)


def _envelope(payload_str: str, *, eidolon="atlas", tier="T1", kind="scout-report",
              version="1.0", thread="thread-7", objective="map auth subsystem") -> dict:
    sha = compute_sha256(payload_str.encode())
    return {
        "envelope_version": version, "message_id": "m1", "thread_id": thread,
        "parent_id": None,
        "from": {"eidolon": eidolon, "version": "1.0.0"},
        "to": {"eidolon": "spectra", "version": "1.0.0"},
        "performative": "INFORM", "objective": objective,
        "artifact": {"kind": kind, "schema_version": "1.0", "path": "stdin",
                     "sha256": sha, "size_bytes": len(payload_str)},
        "integrity": {"method": "sha256", "value": sha},
        "trace": {"ts": "2026-01-01T00:00:00Z", "host": "h", "model": "m", "tier": tier},
    }


def _ingest(comps, envelope, payload):
    (_e, _a, ep, se, pr, ex, _g, _s, _r) = comps
    return _handle_ingest({"envelope": envelope, "payload": payload,
                           "payload_encoding": "json"}, ep, se, pr, ex)


def test_ingest_rejects_payload_not_matching_declared_hash(tmp_path: Path) -> None:
    """G7 binding (battle-test): the envelope is internally consistent (integrity.value
    == artifact.sha256) but the ACTUAL payload bytes differ from the declared hash.
    This must be rejected — the old validation only checked envelope self-consistency,
    so tampered/mismatched payloads passed through unbound."""
    comps = _components(tmp_path)
    honest_payload = json.dumps({"findings": [{"id": "F1"}]})
    env = _envelope(honest_payload, eidolon="atlas", tier="T1")  # sha256 over honest payload
    tampered_payload = json.dumps({"findings": [{"id": "EVIL"}]})  # different bytes
    with pytest.raises(CrystaliumEnforcementError) as ei:
        _ingest(comps, env, tampered_payload)
    assert ei.value.reason_code == "INGEST_PAYLOAD_HASH_MISMATCH"


def test_t1_roster_artifact_lands_episodic_unverified(tmp_path: Path) -> None:
    comps = _components(tmp_path)
    artifact = {"findings": [{"id": "F1"}], "gaps": ["G1"]}
    payload = json.dumps(artifact)
    res = _ingest(comps, _envelope(payload, eidolon="atlas", tier="T1"), payload)
    assert res["status"] == "ingested"
    assert res["layer"] == "episodic"
    assert res["trust_tier"] == "T1"
    assert res["validation_state"] == "unverified"   # T1 -> not quarantined
    assert res["source_eidolon"] == "atlas"
    assert res["artifact_kind"] == "scout-report"


def test_recall_preserves_provenance_and_tier(tmp_path: Path) -> None:
    comps = _components(tmp_path)
    (_e, aetheryte, _ep, _se, _pr, _ex, _g, _s, _r) = comps
    artifact = {"findings": ["auth uses argon2"]}
    payload = json.dumps(artifact)
    res = _ingest(comps, _envelope(payload, eidolon="atlas", tier="T1",
                                   thread="proj-x", objective="auth subsystem map"), payload)
    out = aetheryte.recall(Scope(project="proj-x"), "auth subsystem map", 10, None, Tier.T1)
    rec = next((r for r in out.records if r.id == res["id"]), None)
    assert rec is not None
    assert rec.trust_tier == "T1"                    # MIN-trust intact, not laundered


def test_t3_tool_artifact_quarantined(tmp_path: Path) -> None:
    comps = _components(tmp_path)
    payload = json.dumps({"raw": "tool output"})
    # unknown source + non-tier trace.tier -> T3 -> episodic-quarantine
    env = _envelope(payload, eidolon="some-tool", tier="standard")
    res = _ingest(comps, env, payload)
    assert res["layer"] == "episodic"
    assert res["trust_tier"] == "T3"
    assert res["validation_state"] == "quarantined"


def test_native_artifact_preserved_through_ingest(tmp_path: Path) -> None:
    comps = _components(tmp_path)
    (_e, _a, _ep, _se, _pr, _ex, _g, _s, relational) = comps
    artifact = {"nested": {"a": [1, 2, 3]}, "k": "v"}
    payload = json.dumps(artifact)
    res = _ingest(comps, _envelope(payload), payload)
    crystal = relational.get_crystal(res["id"])
    assert crystal["encoding_context"]["native_artifact"] == artifact


def test_unsupported_version_rejected(tmp_path: Path) -> None:
    comps = _components(tmp_path)
    payload = json.dumps({})
    env = _envelope(payload, version="3.0")
    with pytest.raises(CrystaliumEnforcementError) as exc:
        _ingest(comps, env, payload)
    assert exc.value.reason_code == "UNSUPPORTED_ENVELOPE_VERSION"


def test_integrity_mismatch_rejected(tmp_path: Path) -> None:
    comps = _components(tmp_path)
    payload = json.dumps({})
    env = _envelope(payload)
    env["integrity"]["value"] = "0" * 64           # break G7
    with pytest.raises(CrystaliumEnforcementError) as exc:
        _ingest(comps, env, payload)
    assert exc.value.reason_code == "INGEST_BAD_ENVELOPE"


def test_missing_envelope_rejected(tmp_path: Path) -> None:
    (_e, _a, ep, se, pr, ex, _g, _s, _r) = _components(tmp_path)
    with pytest.raises(CrystaliumEnforcementError) as exc:
        _handle_ingest({"payload": "{}"}, ep, se, pr, ex)
    assert exc.value.reason_code == "INGEST_BAD_ENVELOPE"
