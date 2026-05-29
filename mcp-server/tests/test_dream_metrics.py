"""W3 Objective 6 — pure Dream-gate metric math (no live store).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_dream_metrics.py -v
"""

from __future__ import annotations

import pytest

from evals.metrics import (
    compression_ratio,
    consolidation_gain,
    memory_size_plateau,
    semantic_drift,
    useful_context_retention,
)


def test_memory_size_plateau():
    # linear growth -> latter-half slope ~1
    assert memory_size_plateau([0, 1, 2, 3, 4, 5, 6]) == pytest.approx(1.0)
    # plateau -> latter-half slope ~0
    assert memory_size_plateau([0, 2, 4, 6, 6, 6, 6]) == pytest.approx(0.0)
    assert memory_size_plateau([5]) is None


def test_consolidation_gain():
    assert consolidation_gain(3, 7) == 4
    assert consolidation_gain(5, 5) == 0
    assert consolidation_gain(5, 4) == -1


def test_semantic_drift_basic():
    cons = [
        {"summary": "auth uses argon2id hashing", "expected_tokens": ["argon2"]},   # ok
        {"summary": "deploy is blue-green on k8s", "expected_tokens": ["blue-green"]},  # ok
        {"summary": "rate limit unknown", "expected_tokens": ["200", "per minute"]},  # drift
    ]
    assert semantic_drift(cons) == pytest.approx(1 / 3)


def test_semantic_drift_none_when_empty():
    assert semantic_drift([]) is None


def test_semantic_drift_zero_when_all_faithful():
    cons = [{"summary": "postgres 16 primary store", "expected_tokens": ["postgres", "16"]}]
    assert semantic_drift(cons) == 0.0


def test_useful_context_retention():
    crystals = [
        {"id": "k1", "status": "active"},
        {"id": "k2", "status": "deprecated"},
        {"id": "other", "status": "active"},
    ]
    assert useful_context_retention(crystals, {"k1", "k2"}) == pytest.approx(0.5)
    assert useful_context_retention(crystals, set()) is None


def test_compression_ratio():
    assert compression_ratio([100, 100], 50) == pytest.approx(0.25)
    assert compression_ratio([], 10) is None
    assert compression_ratio([10, 10], 20) == pytest.approx(1.0)  # no generalization
