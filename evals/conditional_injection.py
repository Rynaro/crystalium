"""Deterministic policy fixture proxies; NOT a coding benchmark.

resolve_rate = memory-needed fixtures receiving their author-labeled useful id /
number of memory-needed fixtures. distraction_rate = injected records without an
author-labeled useful id / injected records (zero if none). No agent executes a
task: labels are synthetic stipulations, not measured task outcomes. The broad
always_on baseline uses k=10; gated uses the recommended k=3. Fixture retrieval
is a fixed candidate source, so this evaluates policy, not retrieval quality.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from crystalium.assembly import assemble_context
from crystalium.config import Config
from crystalium.schemas import AssembleContextRequest, CrystalSummary
from crystalium.trust import Tier


class FixtureRecall:
    """Fixed, already-safe summary candidates with deterministic word tokens."""

    composer = SimpleNamespace(tokenize=lambda text: len(text.split()))

    def __init__(self, records: list[CrystalSummary]) -> None:
        self.records = records

    def recall(self, **kwargs: Any) -> SimpleNamespace:
        """Return a bounded synthetic recall response."""
        return SimpleNamespace(records=self.records[: kwargs["k"]])


def run_comparison() -> dict[str, Any]:
    """Compare three policies on the exact same labeled fixture tasks."""
    records = [
        CrystalSummary(
            id=f"record-{i}",
            layer="semantic",
            summary=(
                "Fixed parser; tests passed."
                if i == 0
                else "Historical parser background information."
            ),
            trust_tier="T1",
            validation_state="validated",
            importance=0.5,
            score=1 - i * 0.03,
            last_access=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for i in range(10)
    ]
    fixtures = [
        {"name": "new_feature", "needs_memory": False, "prior_failure": False, "useful": set()},
        {
            "name": "known_parser",
            "needs_memory": True,
            "prior_failure": False,
            "useful": {"record-0"},
        },
        {
            "name": "failed_parser",
            "needs_memory": False,
            "prior_failure": True,
            "useful": {"record-0"},
        },
    ]
    report: dict[str, Any] = {
        "measurement": "synthetic author-labeled policy proxies; no coding benchmark executed",
        "tokenizer": "deterministic whitespace words (fixture-only)",
        "fixture_count": len(fixtures),
        "modes": {},
    }
    with tempfile.TemporaryDirectory(prefix="crystalium-injection-") as directory:
        for mode in ("gated", "always_on", "off"):
            config = Config(data_dir=Path(directory) / mode, memory_injection_mode=mode)
            token_count = injected = distractions = resolved = needed = 0
            k = 10 if mode == "always_on" else 3
            for fixture in fixtures:
                request = AssembleContextRequest(
                    scope={"project": "fixture"},
                    query="parser",
                    k=k,
                    needs_memory=fixture["needs_memory"],
                    prior_failure=fixture["prior_failure"],
                )
                result = assemble_context(request, FixtureRecall(records), config, Tier.T2)
                ids = {record.id for record in result.records}
                useful = fixture["useful"]
                needed += bool(useful)
                resolved += bool(ids & useful)
                injected += len(result.records)
                distractions += len(ids - useful)
                token_count += result.injected_token_count
            report["modes"][mode] = {
                "k": k,
                "injected_token_count": token_count,
                "resolve_rate": resolved / needed if needed else 0,
                "resolve_numerator": resolved,
                "resolve_denominator": needed,
                "distraction_rate": distractions / injected if injected else 0,
                "distraction_numerator": distractions,
                "distraction_denominator": injected,
            }
    return report


if __name__ == "__main__":
    print(json.dumps(run_comparison(), indent=2, sort_keys=True))
