"""Tests for the D6 importance function (signature stability + numerical correctness).

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_importance.py -v

Tests:
  - Signature stability (do NOT drift from FORGE D6 contract)
  - Known input → known output
  - Bounded output ([0, 1])
  - Access frequency / recency / outcome / novelty component behaviour
"""

from __future__ import annotations

import inspect
import math
from datetime import datetime, timedelta, timezone

import pytest

from crystalium.importance import (
    RECENCY_HALFLIFE_DAYS,
    WEIGHTS,
    MemoryRecord,
    importance_score,
)


# ---------------------------------------------------------------------------
# Test stub implementing MemoryRecord protocol
# ---------------------------------------------------------------------------


class FakeRecord:
    """Minimal MemoryRecord for testing."""

    def __init__(
        self,
        access_count: int = 0,
        last_access: datetime | None = None,
        outcome_success: float | None = None,
        novelty_at_write: float = 0.5,
    ):
        self.access_count = access_count
        self.last_access = last_access or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.outcome_success = outcome_success
        self.novelty_at_write = novelty_at_write


# ---------------------------------------------------------------------------
# D6 Signature stability tests
# ---------------------------------------------------------------------------


class TestD6SignatureStability:
    """Verify the importance_score() signature has not drifted from FORGE D6."""

    def test_function_exists(self) -> None:
        assert callable(importance_score)

    def test_signature_has_record_and_now_keyword(self) -> None:
        sig = inspect.signature(importance_score)
        params = list(sig.parameters.keys())
        assert "record" in params, "importance_score must have a 'record' parameter"
        assert "now" in params, "importance_score must have a 'now' keyword parameter"
        # now must be keyword-only (D6: "*, now: datetime")
        now_param = sig.parameters["now"]
        assert now_param.kind == inspect.Parameter.KEYWORD_ONLY, (
            "now must be keyword-only per D6 signature lock"
        )

    def test_weights_tuple_shape(self) -> None:
        assert isinstance(WEIGHTS, tuple), "WEIGHTS must be a tuple"
        assert len(WEIGHTS) == 4, "WEIGHTS must have 4 elements: (af, rc, os, nv)"
        assert WEIGHTS == (0.25, 0.30, 0.25, 0.20), (
            "WEIGHTS must equal (0.25, 0.30, 0.25, 0.20) per D6 signature lock. "
            "D11 is the only authorized change point."
        )

    def test_weights_sum_to_one(self) -> None:
        total = sum(WEIGHTS)
        assert abs(total - 1.0) < 1e-10, f"WEIGHTS must sum to 1.0, got {total}"

    def test_recency_halflife_constant(self) -> None:
        assert RECENCY_HALFLIFE_DAYS == 14.0, (
            "RECENCY_HALFLIFE_DAYS must be 14.0 per D6 (OQ-4 reviews operator-tuning)"
        )

    def test_returns_float(self, frozen_now: datetime) -> None:
        record = FakeRecord()
        result = importance_score(record, now=frozen_now)
        assert isinstance(result, float)

    def test_protocol_conformance(self, frozen_now: datetime) -> None:
        """FakeRecord satisfies MemoryRecord protocol."""
        record = FakeRecord(access_count=5, novelty_at_write=0.8, outcome_success=0.9)
        # Should not raise
        importance_score(record, now=frozen_now)


# ---------------------------------------------------------------------------
# Known input → known output tests
# ---------------------------------------------------------------------------


class TestKnownInputOutput:
    def test_brand_new_record_with_neutral_params(self, frozen_now: datetime) -> None:
        """Record accessed just now, 0 accesses, neutral outcome, 0.5 novelty."""
        record = FakeRecord(
            access_count=0,
            last_access=frozen_now,
            outcome_success=None,  # = 0.5
            novelty_at_write=0.5,
        )
        score = importance_score(record, now=frozen_now)
        # Expected:
        # af = log1p(0) / log1p(100) = 0 / log1p(100) = 0
        # rc = 0.5 ** (0 / 14) = 1.0
        # os = 0.5
        # nv = 0.5
        # raw = 0.25*0 + 0.30*1.0 + 0.25*0.5 + 0.20*0.5
        #     = 0 + 0.30 + 0.125 + 0.10 = 0.525
        expected = 0.525
        assert abs(score - expected) < 1e-6, f"Expected {expected}, got {score}"

    def test_highly_accessed_recent_high_quality_record(self, frozen_now: datetime) -> None:
        """Record with 100 accesses, just accessed, outcome=1.0, novelty=1.0."""
        record = FakeRecord(
            access_count=100,
            last_access=frozen_now,
            outcome_success=1.0,
            novelty_at_write=1.0,
        )
        score = importance_score(record, now=frozen_now)
        # af = log1p(100) / log1p(100) = 1.0
        # rc = 1.0
        # os = 1.0
        # nv = 1.0
        # raw = 0.25 + 0.30 + 0.25 + 0.20 = 1.0
        assert abs(score - 1.0) < 1e-6

    def test_old_record_recency_decay(self, frozen_now: datetime) -> None:
        """Record last accessed 14 days ago — recency should be 0.5."""
        old_access = frozen_now - timedelta(days=14)
        record = FakeRecord(
            access_count=0,
            last_access=old_access,
            outcome_success=None,
            novelty_at_write=0.0,
        )
        score = importance_score(record, now=frozen_now)
        # af = 0, rc = 0.5 ** (14/14) = 0.5, os = 0.5, nv = 0
        # raw = 0.25*0 + 0.30*0.5 + 0.25*0.5 + 0.20*0 = 0.15 + 0.125 = 0.275
        expected = 0.275
        assert abs(score - expected) < 1e-6, f"Expected {expected}, got {score}"

    def test_zero_novelty_zero_outcome_zero_access_old(self, frozen_now: datetime) -> None:
        """Minimum-signal record — should still have recency contribution."""
        old_access = frozen_now - timedelta(days=100)
        record = FakeRecord(
            access_count=0,
            last_access=old_access,
            outcome_success=0.0,
            novelty_at_write=0.0,
        )
        score = importance_score(record, now=frozen_now)
        # Should be very small but >= 0
        assert 0.0 <= score <= 0.1


# ---------------------------------------------------------------------------
# Bounded output tests
# ---------------------------------------------------------------------------


class TestBoundedOutput:
    @pytest.mark.parametrize(
        "access_count, days_old, outcome, novelty",
        [
            (0, 0, None, 0.0),
            (1000, 0, 1.0, 1.0),   # access_count above cap
            (0, 365, 0.0, 0.0),    # very old
            (50, 7, 0.5, 0.5),     # typical
            (100, 14, 1.0, 0.8),
        ],
    )
    def test_score_in_zero_one(
        self,
        frozen_now: datetime,
        access_count: int,
        days_old: int,
        outcome: float | None,
        novelty: float,
    ) -> None:
        record = FakeRecord(
            access_count=access_count,
            last_access=frozen_now - timedelta(days=days_old),
            outcome_success=outcome,
            novelty_at_write=novelty,
        )
        score = importance_score(record, now=frozen_now)
        assert 0.0 <= score <= 1.0, (
            f"Score {score} out of [0, 1] for input "
            f"(access={access_count}, days={days_old}, outcome={outcome}, novelty={novelty})"
        )

    def test_clamp_does_not_produce_nan(self, frozen_now: datetime) -> None:
        """Edge case: access_count very large; should not produce NaN."""
        record = FakeRecord(access_count=10_000_000, last_access=frozen_now)
        score = importance_score(record, now=frozen_now)
        assert not math.isnan(score)
        assert 0.0 <= score <= 1.0

    def test_naive_datetime_handled(self) -> None:
        """Naive datetime in record.last_access should not raise."""
        naive_now = datetime(2026, 5, 28, 12, 0, 0)  # no tzinfo
        naive_access = datetime(2026, 5, 21, 12, 0, 0)  # 7 days ago naive
        record = FakeRecord(last_access=naive_access, novelty_at_write=0.5)
        score = importance_score(record, now=naive_now)
        assert 0.0 <= score <= 1.0
