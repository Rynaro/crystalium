"""W7 C11 — install.sh --hosts auto idempotent host MCP-config merge.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_host_wiring.py -v
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
    raise RuntimeError("install.sh not found")


pytestmark = pytest.mark.skipif(
    not (_repo_root() / "install.sh").exists(), reason="install.sh not mounted"
)


def _install(target: Path, hosts_dir: Path, root: Path):
    return subprocess.run(
        ["bash", str(root / "install.sh"), "--target", str(target),
         "--non-interactive", "--hosts", "auto", "--hosts-dir", str(hosts_dir)],
        cwd=str(root), capture_output=True, text=True,
    )


def test_hosts_auto_writes_all_four_configs(tmp_path: Path):
    root = _repo_root()
    hd = tmp_path / "hosts"
    r = _install(tmp_path / "t", hd, root)
    assert r.returncode == 0, r.stderr
    claude = json.loads((hd / ".mcp.json").read_text())
    cursor = json.loads((hd / ".cursor" / "mcp.json").read_text())
    copilot = json.loads((hd / ".vscode" / "mcp.json").read_text())
    opencode = json.loads((hd / ".config" / "opencode" / "config.json").read_text())
    assert "crystalium" in claude["mcpServers"]
    assert "crystalium" in cursor["mcpServers"]
    assert "crystalium" in copilot["servers"]        # VS Code uses "servers"
    assert "crystalium" in opencode["mcp"]
    # the command must include the serve subcommand
    assert "serve" in claude["mcpServers"]["crystalium"]["args"]


def test_hosts_merge_is_idempotent(tmp_path: Path):
    root = _repo_root()
    hd = tmp_path / "hosts"
    assert _install(tmp_path / "t", hd, root).returncode == 0
    before = (hd / ".mcp.json").read_text()
    assert _install(tmp_path / "t", hd, root).returncode == 0
    after = (hd / ".mcp.json").read_text()
    assert before == after                            # second run byte-identical


def test_hosts_merge_preserves_existing_servers(tmp_path: Path):
    root = _repo_root()
    hd = tmp_path / "hosts"
    hd.mkdir(parents=True)
    # a pre-existing host config with another server must survive the merge
    (hd / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    assert _install(tmp_path / "t", hd, root).returncode == 0
    cfg = json.loads((hd / ".mcp.json").read_text())
    assert "other" in cfg["mcpServers"]               # not clobbered
    assert "crystalium" in cfg["mcpServers"]           # upserted alongside
