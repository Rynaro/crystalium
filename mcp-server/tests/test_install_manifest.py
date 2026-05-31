"""W7 C7 — the generated install.manifest.json validates against its schema.

Runs install.sh into a temp target, then validates the emitted manifest against
schemas/install.manifest.v1.json. Guards the generator↔schema convergence (W7).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_install_manifest.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

# Repo root inside the container overlay: /app (src) but install.sh + schemas are
# mounted at the workspace root. Resolve relative to this test file's mount.
_REPO = Path("/app")  # tests run with PYTHONPATH=/app/src:/app; schemas at /app/schemas


def _repo_root() -> Path:
    # The overlay mounts schemas at /app/schemas and install.sh at /app/install.sh
    # in CI/`make`, but the fast-loop mounts only src+tests+schemas+evals. Fall back
    # to walking up from this file when install.sh isn't at /app.
    if (Path("/app") / "schemas" / "install.manifest.v1.json").exists() and (
        Path("/app") / "install.sh"
    ).exists():
        return Path("/app")
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "install.sh").exists() and (parent / "schemas").exists():
            return parent
    return Path("/__no_repo__")   # non-existent -> skipif handles it (no collection error)


@pytest.mark.skipif(
    not (_repo_root() / "install.sh").exists(),
    reason="install.sh not mounted in this loop",
)
def test_generated_manifest_validates(tmp_path: Path) -> None:
    root = _repo_root()
    target = tmp_path / "target"
    subprocess.run(
        ["bash", str(root / "install.sh"), "--target", str(target), "--non-interactive"],
        check=True, cwd=str(root), capture_output=True, text=True,
    )
    manifest = json.loads((target / "install.manifest.json").read_text())
    schema = json.loads((root / "schemas" / "install.manifest.v1.json").read_text())
    jsonschema.validate(instance=manifest, schema=schema)   # raises on any divergence

    # spot-check the W7 fixes
    assert manifest["ecl_version"] == "2.0"
    assert manifest["version"] == "0.8.0"
    roles = {f["role"] for f in manifest["files_written"]}
    assert "schema" in roles and "other" not in roles
