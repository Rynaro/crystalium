"""W7 C14 — standalone + partial-team (2-member) deployment modes.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_deploy_modes.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from crystalium.config import Config
from crystalium.ecl import compute_sha256
from crystalium.schemas import Scope
from crystalium.server import _build_components, _handle_ingest
from crystalium.trust import Tier


def _repo_root() -> Path:
    for base in (Path("/app"), *Path(__file__).resolve().parents):
        if (base / "install.sh").exists():
            return base
    return Path("/__no_repo__")   # non-existent -> skipif handles it (no collection error)


def _envelope(payload: str, *, eidolon: str, kind: str, thread: str, objective: str) -> dict:
    sha = compute_sha256(payload.encode())
    return {
        "envelope_version": "2.0", "message_id": "m", "thread_id": thread, "parent_id": None,
        "from": {"eidolon": eidolon, "version": "1.0.0"},
        "to": {"eidolon": "crystalium", "version": "0.8.0"},
        "performative": "INFORM", "objective": objective,
        "artifact": {"kind": kind, "schema_version": "1.0", "path": "stdin",
                     "sha256": sha, "size_bytes": len(payload)},
        "integrity": {"method": "sha256", "value": sha},
        "trace": {"ts": "2026-01-01T00:00:00Z", "host": "h", "model": "m", "tier": "T1"},
    }


# ---- runtime: standalone works (zero teammates; other layers unused) -------

def test_standalone_ingest_recall(tmp_path: Path) -> None:
    from crystalium.scope import canonical_project_key

    cfg = Config(data_dir=tmp_path / "solo", rate_limit_per_minute=1_000_000)
    (_e, aetheryte, ep, se, pr, ex, _g, _s, relational) = _build_components(cfg)
    payload = json.dumps({"note": "self-authored standalone fact about caching"})
    env = _envelope(payload, eidolon="crystalium", kind="note",
                    thread="solo-proj", objective="caching policy notes")
    receipt = _handle_ingest({"envelope": env, "payload": payload,
                              "payload_encoding": "json"}, ep, se, pr, ex, cfg)
    assert receipt["status"] == "ingested" and receipt["layer"] == "episodic"
    # v1.6: scope.project is normalized to the canonical (data-dir-derived) key;
    # thread_id="solo-proj" is preserved verbatim in scope.project_raw instead.
    assert receipt["scope_normalized"] is True
    canonical = canonical_project_key(cfg.data_dir)
    out = aetheryte.recall(Scope(project=canonical), "caching policy notes", 10, None, Tier.T1)
    assert any(r.id == receipt["id"] for r in out.records)
    # the other three layers are simply unused — no rows, no error
    layers = {c["layer"] for c in relational.list_crystals_with_dynamics()}
    assert layers == {"episodic"}


# ---- install: standalone vs 2-member roster recorded in manifest -----------

pytestmark_install = pytest.mark.skipif(
    not (_repo_root() / "install.sh").exists(), reason="install.sh not mounted"
)


def _install(target: Path, root: Path, *extra):
    return subprocess.run(
        ["bash", str(root / "install.sh"), "--target", str(target), "--non-interactive", *extra],
        cwd=str(root), capture_output=True, text=True,
    )


@pytestmark_install
def test_install_standalone_empty_roster(tmp_path: Path) -> None:
    root = _repo_root()
    target = tmp_path / "t"
    assert _install(target, root).returncode == 0
    manifest = json.loads((target / "install.manifest.json").read_text())
    assert manifest.get("roster", []) == []


@pytestmark_install
def test_install_two_member_atlas_crystalium(tmp_path: Path) -> None:
    root = _repo_root()
    target = tmp_path / "t"
    assert _install(target, root, "--members", "atlas,crystalium").returncode == 0
    manifest = json.loads((target / "install.manifest.json").read_text())
    assert manifest["roster"] == ["atlas", "crystalium"]
