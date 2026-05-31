"""W7 C8/C9 — install.sh flags: --version, --manifest-only, --non-interactive, roster.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_install_flags.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    if (Path("/app") / "install.sh").exists():
        return Path("/app")
    for parent in Path(__file__).resolve().parents:
        if (parent / "install.sh").exists():
            return parent
    return Path("/__no_repo__")   # non-existent -> skipif handles it (no collection error)


pytestmark = pytest.mark.skipif(
    not (_repo_root() / "install.sh").exists(), reason="install.sh not mounted"
)


def _run(args, cwd):
    return subprocess.run(["bash", str(cwd / "install.sh"), *args],
                          cwd=str(cwd), capture_output=True, text=True)


def test_version_flag_prints_and_exits():
    root = _repo_root()
    res = _run(["--version"], root)
    assert res.returncode == 0
    assert res.stdout.strip() == "1.0.0"


def test_manifest_only_does_not_change_files(tmp_path: Path):
    root = _repo_root()
    target = tmp_path / "t"
    # full install first
    assert _run(["--target", str(target), "--non-interactive"], root).returncode == 0
    snap1 = sorted(p.name for p in target.rglob("*") if p.is_file())
    manifest1 = (target / "install.manifest.json").read_text()
    # manifest-only re-run: no file added/removed; manifest still valid
    r2 = _run(["--target", str(target), "--manifest-only", "--non-interactive"], root)
    assert r2.returncode == 0, r2.stderr
    snap2 = sorted(p.name for p in target.rglob("*") if p.is_file())
    assert snap1 == snap2                                   # no staging/sweep changes
    assert json.loads((target / "install.manifest.json").read_text())["version"] == "1.0.0"
    assert manifest1  # sanity


def test_non_interactive_no_prompt_on_existing_target(tmp_path: Path):
    root = _repo_root()
    target = tmp_path / "t"
    assert _run(["--target", str(target), "--non-interactive"], root).returncode == 0
    # second non-interactive run over the populated target must not hang/prompt
    r2 = _run(["--target", str(target), "--non-interactive"], root)
    assert r2.returncode == 0


def test_members_scope_recorded_in_manifest(tmp_path: Path):
    root = _repo_root()
    target = tmp_path / "t"
    res = _run(["--target", str(target), "--non-interactive",
                "--members", "atlas,crystalium", "--scope", "demo"], root)
    assert res.returncode == 0, res.stderr
    manifest = json.loads((target / "install.manifest.json").read_text())
    assert manifest.get("roster") == ["atlas", "crystalium"]
    assert manifest.get("scope") == "demo"


def test_standalone_empty_roster(tmp_path: Path):
    root = _repo_root()
    target = tmp_path / "t"
    assert _run(["--target", str(target), "--non-interactive"], root).returncode == 0
    manifest = json.loads((target / "install.manifest.json").read_text())
    assert manifest.get("roster", []) == []                 # standalone: no members
