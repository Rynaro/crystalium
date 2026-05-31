"""W7 C12 — AGENTS.md YAML frontmatter + agent.md token budget (EIIS finalization).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_frontmatter.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    for base in (Path("/app"), *Path(__file__).resolve().parents):
        if (base / "AGENTS.md").exists() and (base / "agent.md").exists():
            return base
    raise RuntimeError("AGENTS.md/agent.md not found")


pytestmark = pytest.mark.skipif(
    not (_repo_root() / "AGENTS.md").exists(), reason="AGENTS.md not mounted"
)


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), "AGENTS.md must open with a YAML frontmatter block"
    end = text.index("\n---", 4)
    return yaml.safe_load(text[4:end])


def test_agents_frontmatter_required_keys():
    fm = _frontmatter(_repo_root() / "AGENTS.md")
    assert fm["name"] == "crystalium"
    assert fm["version"] == "0.8.0"                 # full semver
    assert fm["ecl_version"] == "2.0"
    assert fm["eiis_version"] == "1.4"


def test_agents_frontmatter_handoffs_split():
    fm = _frontmatter(_repo_root() / "AGENTS.md")
    handoffs = fm["handoffs"]
    assert isinstance(handoffs["upstream"], list) and handoffs["upstream"]
    assert isinstance(handoffs["downstream"], list) and handoffs["downstream"]
    assert "atlas" in handoffs["upstream"]
    assert "idg" in handoffs["downstream"]


def test_agent_md_under_1000_tokens():
    # The EIIS budget: agent.md must stay <= 1000 tokens (tiktoken cl100k_base).
    import tiktoken
    text = (_repo_root() / "agent.md").read_text()
    n = len(tiktoken.get_encoding("cl100k_base").encode(text))
    assert n <= 1000, f"agent.md is {n} tokens (> 1000)"
