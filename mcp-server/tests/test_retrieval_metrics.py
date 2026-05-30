"""W5 Objective 7 — pure retrieval-gate metric math (no live store).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_retrieval_metrics.py -v
"""

from __future__ import annotations

import pytest

from evals.metrics import (
    attack_success_rate,
    cache_hit_rate,
    precision_recall_f1,
    write_amplification,
)


def test_precision_recall_f1_perfect():
    m = precision_recall_f1(["a", "b"], ["a", "b"])
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0
    assert m["f1"] == 1.0


def test_precision_recall_f1_partial():
    # retrieved {a,b,c}, relevant {a,b,d}: hit=2 -> p=2/3, r=2/3, f1=2/3
    m = precision_recall_f1(["a", "b", "c"], ["a", "b", "d"])
    assert m["precision"] == pytest.approx(2 / 3)
    assert m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3)


def test_precision_recall_f1_no_overlap_f1_none():
    m = precision_recall_f1(["x"], ["y"])
    assert m["precision"] == 0.0
    assert m["recall"] == 0.0
    assert m["f1"] is None              # 0+0 -> undefined, not 0.0


def test_precision_recall_f1_empty_denominators():
    assert precision_recall_f1([], ["a"])["precision"] is None
    assert precision_recall_f1(["a"], [])["recall"] is None
    assert precision_recall_f1([], [])["f1"] is None


def test_precision_recall_f1_dedupes_inputs():
    # duplicate ids in retrieved must not inflate precision
    m = precision_recall_f1(["a", "a", "a"], ["a"])
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0


def test_write_amplification():
    assert write_amplification(10, 10) == 1.0     # no dedup
    assert write_amplification(6, 10) == 0.6      # merges collapsed 4 writes
    assert write_amplification(5, 0) is None       # undefined


def test_cache_hit_rate():
    assert cache_hit_rate(3, 1) == 0.75
    assert cache_hit_rate(0, 5) == 0.0
    assert cache_hit_rate(0, 0) is None            # never queried -> undefined


def test_attack_success_rate():
    assert attack_success_rate([False, False, False, False]) == 0.0   # all blocked
    assert attack_success_rate([True, False, False, False]) == 0.25
    assert attack_success_rate([True, True]) == 1.0
    assert attack_success_rate([]) is None         # no attacks -> undefined
