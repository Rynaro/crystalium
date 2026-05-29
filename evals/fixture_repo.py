"""Deterministic seeded fixture repo for the ablation bench (W1 Objective 4).

The committed seed lives in evals/fixtures/fixture_repo.json. load_fixture_repo()
returns it in a stable order (sorted by id) so two loads are byte-identical, and
seed_fixture_repo() commits every record through a CanaryEnv so the live A/B arm
runs against a known baseline instead of ad-hoc inline commits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "fixture_repo.json"


def fixture_path() -> Path:
    return _FIXTURE_PATH


def load_fixture_repo() -> dict[str, Any]:
    """Load the fixture repo, with crystals sorted by id (deterministic order)."""
    data = json.loads(_FIXTURE_PATH.read_text())
    data["crystals"] = sorted(data["crystals"], key=lambda c: c["id"])
    return data


def fixture_project() -> str:
    return str(load_fixture_repo()["project"])


def seed_fixture_repo(env: Any) -> list[dict[str, Any]]:
    """Commit every fixture crystal through *env* (a CanaryEnv). Deterministic order.

    Returns the list of commit results. On the memory-off / null arm this is a
    no-op-equivalent (env.commit returns stub ids), so both arms stay symmetric.
    """
    repo = load_fixture_repo()
    results: list[dict[str, Any]] = []
    for c in repo["crystals"]:
        results.append(
            env.commit(
                layer=c["layer"],
                summary=c["summary"],
                content=c["content"],
                trust_tier=c.get("trust_tier", "T1"),
            )
        )
    return results
