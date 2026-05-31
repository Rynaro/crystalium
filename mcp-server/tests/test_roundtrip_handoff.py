"""W7 C13 — DoD round-trip gate: ATLAS→SPECTRA→APIVR-Δ→IDG through CRYSTALIUM.

Drives the REAL vendored roster fixtures (ECL v1.0) through crystalium.ingest, then
asserts every handoff recalls with provenance + MIN trust tier intact and the native
artifact preserved verbatim. The available fixtures are from atlas/spectra/forge/
vigil — the four pipeline hops; assertions key on each fixture's actual from.eidolon.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_roundtrip_handoff.py -v
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from crystalium.config import Config
from crystalium.schemas import Scope
from crystalium.server import _build_components, _handle_ingest
from crystalium.trust import Tier


def _fixtures_dir() -> Path:
    for base in (Path("/app"), *Path(__file__).resolve().parents):
        cand = base / ".eidolons" / "apivr" / "templates" / "inbound"
        if cand.exists():
            return cand
    return Path("/__no_fixtures__")   # non-existent -> skipif handles it (no collection error)


pytestmark = pytest.mark.skipif(
    not _fixtures_dir().exists(), reason="roster fixtures not mounted"
)

# pipeline order; each maps to a real vendored envelope fixture
_PIPELINE = ["scout-report", "spec", "reasoning-report", "root-cause-report"]


def _load(name: str) -> dict:
    return json.loads((_fixtures_dir() / f"{name}.envelope.fixture.json").read_text())


def _keywords(text: str, n: int = 4) -> str:
    # A downstream Eidolon recalls with clean keywords. (bm25_search passes the query
    # straight to FTS5 MATCH, which treats ':'/'-' etc. as syntax — [GAP] recall does
    # not sanitize; callers supply clean queries.)
    return " ".join(re.findall(r"[A-Za-z0-9]+", text)[:n]) or "artifact"


def _components(tmp_path: Path):
    cfg = Config(data_dir=tmp_path / "rt", rate_limit_per_minute=1_000_000)
    return _build_components(cfg)


def test_pipeline_round_trips_with_provenance_and_min_tier(tmp_path: Path) -> None:
    comps = _components(tmp_path)
    (_e, aetheryte, ep, se, pr, ex, _g, _s, relational) = comps

    for name in _PIPELINE:
        env = _load(name)
        eidolon = env["from"]["eidolon"]
        # synthesized artifact body (fixtures are content-addressed; integrity is
        # envelope-internal so validation passes regardless of this payload)
        artifact = {"kind": env["artifact"]["kind"], "objective": env["objective"],
                    "from": eidolon}
        receipt = _handle_ingest(
            {"envelope": env, "payload": artifact, "payload_encoding": "json"},
            ep, se, pr, ex,
        )

        # 1. ingest landed (episodic; roster default tier T1 -> not quarantined)
        assert receipt["status"] == "ingested"
        assert receipt["layer"] == "episodic"
        assert receipt["trust_tier"] == "T1", f"{name}: roster artifact must be T1"
        assert receipt["validation_state"] == "unverified"
        assert receipt["source_eidolon"] == eidolon
        assert receipt["envelope_version"].startswith("1.")   # v1.0 fixture ingested

        # 2. verbatim native artifact preserved
        crystal = relational.get_crystal(receipt["id"])
        assert crystal["encoding_context"]["native_artifact"] == artifact
        assert crystal["encoding_context"]["artifact_kind"] == env["artifact"]["kind"]

        # 3. downstream recall (a consuming Eidolon) finds it with provenance + tier intact
        project = env["thread_id"]
        out = aetheryte.recall(Scope(project=project), _keywords(env["objective"]), 10, None, Tier.T1)
        rec = next((r for r in out.records if r.id == receipt["id"]), None)
        assert rec is not None, f"{name}: not recallable downstream"
        assert rec.trust_tier == "T1"                          # MIN-tier intact (no laundering)
        assert crystal["provenance"]["author_agent"] == eidolon
        assert crystal["provenance"]["task_id"] == env["thread_id"]


def test_no_tier_laundering_across_pipeline(tmp_path: Path) -> None:
    # Every ingested roster artifact stays at T1 — none is laundered up to T0.
    comps = _components(tmp_path)
    (_e, _a, ep, se, pr, ex, _g, _s, relational) = comps
    ids = []
    for name in _PIPELINE:
        env = _load(name)
        r = _handle_ingest({"envelope": env, "payload": {"k": name},
                            "payload_encoding": "json"}, ep, se, pr, ex)
        ids.append(r["id"])
    for cid in ids:
        c = relational.get_crystal(cid)
        assert c["trust_tier"] == "T1"
        assert c["validation_state"] in ("unverified", "quarantined")
        assert c["trust_tier"] != "T0"                         # never laundered to host trust
