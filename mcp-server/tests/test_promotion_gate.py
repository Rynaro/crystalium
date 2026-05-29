"""Wave 2 promotion gate tests — G5 P0 gate + k-corroboration + force_promote.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_promotion_gate.py -v

Gate anchor verified here:
  G5: human-confirm default window routes to pending_promotions
      (test_g5_human_confirm_default_window)

Coverage:
  - k=3 independent witnesses → auto-admit (no human-confirm window)
  - k < 3 → pending
  - force_promote requires T0; T1/T2/T3 raises TierViolation
  - Procedural admission without verifier stays candidate
  - CRYSTALIUM_AUTO_CONFIRM=1 bypasses window with WARN log
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crystalium.config import Config
from crystalium.enforcement import Enforcement, TierViolation
from crystalium.gate import PromotionGate, SkillResult, CrystalRef
from crystalium.trust import Tier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_relational(tmp_path: Path):
    from crystalium.storage.relational import RelationalStore

    return RelationalStore(db_path=tmp_path / "gate_test.sqlite")


@pytest.fixture
def config_no_window() -> Config:
    """Config without a human-confirm window (install_ts = None)."""
    return Config(
        k_corroboration=3,
        human_confirm_default_window_days=30,
        install_ts=None,  # no install.ts → window inactive
        rate_limit_per_minute=200,
    )


@pytest.fixture
def config_in_window() -> Config:
    """Config with install_ts within the 30-day window."""
    return Config(
        k_corroboration=3,
        human_confirm_default_window_days=30,
        install_ts=datetime.now(timezone.utc) - timedelta(days=5),  # 5 days ago
        rate_limit_per_minute=200,
    )


@pytest.fixture
def enforcement_no_window(config_no_window: Config) -> Enforcement:
    return Enforcement(config_no_window)


@pytest.fixture
def enforcement_in_window(config_in_window: Config) -> Enforcement:
    return Enforcement(config_in_window)


@pytest.fixture
def gate_no_window(config_no_window: Config, tmp_relational, enforcement_no_window: Enforcement) -> PromotionGate:
    return PromotionGate(
        config=config_no_window,
        relational=tmp_relational,
        enforcement=enforcement_no_window,
    )


@pytest.fixture
def gate_in_window(config_in_window: Config, tmp_relational, enforcement_in_window: Enforcement) -> PromotionGate:
    return PromotionGate(
        config=config_in_window,
        relational=tmp_relational,
        enforcement=enforcement_in_window,
    )


def _stub_crystal(crystal_id: str = "crystal-001") -> dict:
    return {"id": crystal_id, "summary": "test crystal", "layer": "semantic"}


def _make_witnesses(n: int, same_agent: bool = False) -> list[CrystalRef]:
    """Create n independent witnesses (distinct author_agent + source)."""
    return [
        CrystalRef(
            id=f"witness-{i}",
            author_agent=f"agent-{i}" if not same_agent else "same-agent",
            source="verified_agent",
            trust_tier=Tier.T1,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# G5: human-confirm default window routes to pending
# test_anchor: test_promotion_gate.py::test_g5_human_confirm_default_window
# ---------------------------------------------------------------------------


class TestG5HumanConfirmDefaultWindow:
    """G5 (P0) — install_ts < 30d → proposals land in pending_promotions."""

    def test_g5_human_confirm_default_window(
        self, gate_in_window: PromotionGate, tmp_relational
    ) -> None:
        """GIVEN install_ts within 30 days, WHEN propose_semantic, THEN decision == 'pending'."""
        crystal = _stub_crystal()
        # Even with enough witnesses, human-confirm window forces pending
        witnesses = _make_witnesses(5)

        result = gate_in_window.propose_semantic(
            crystal=crystal,
            witnesses=witnesses,
            caller_tier=Tier.T1,
        )

        assert result.decision == "pending", (
            f"Expected 'pending' during human-confirm window, got {result.decision!r}"
        )
        assert result.promotion_id is not None, "promotion_id must be set for pending result"

        # Verify it landed in pending_promotions table
        pending = tmp_relational.list_pending_promotions()
        assert len(pending) == 1
        assert pending[0]["crystal_id"] == crystal["id"]
        assert pending[0]["target_layer"] == "semantic"
        assert pending[0]["status"] == "pending"

    def test_g5_auto_confirm_env_bypasses_window(
        self, gate_in_window: PromotionGate
    ) -> None:
        """CRYSTALIUM_AUTO_CONFIRM=1 bypasses human-confirm (test-only)."""
        os.environ["CRYSTALIUM_AUTO_CONFIRM"] = "1"
        try:
            crystal = _stub_crystal()
            witnesses = _make_witnesses(5)  # enough for k=3

            result = gate_in_window.propose_semantic(
                crystal=crystal,
                witnesses=witnesses,
                caller_tier=Tier.T1,
            )

            # With CRYSTALIUM_AUTO_CONFIRM=1 AND k>=3, should admit
            assert result.decision == "admit", (
                f"AUTO_CONFIRM=1 should bypass window and admit, got {result.decision!r}"
            )
        finally:
            os.environ.pop("CRYSTALIUM_AUTO_CONFIRM", None)

    def test_g5_no_window_k_sufficient_admits(
        self, gate_no_window: PromotionGate
    ) -> None:
        """Outside human-confirm window, k>=3 independent witnesses → admit."""
        crystal = _stub_crystal()
        witnesses = _make_witnesses(3)  # exactly k=3

        result = gate_no_window.propose_semantic(
            crystal=crystal,
            witnesses=witnesses,
            caller_tier=Tier.T1,
        )

        assert result.decision == "admit"
        assert result.record_id == crystal["id"]

    def test_g5_no_window_k_insufficient_pending(
        self, gate_no_window: PromotionGate, tmp_relational
    ) -> None:
        """Outside window but k<3 → pending (not enough witnesses)."""
        crystal = _stub_crystal()
        witnesses = _make_witnesses(2)  # only 2, need 3

        result = gate_no_window.propose_semantic(
            crystal=crystal,
            witnesses=witnesses,
            caller_tier=Tier.T1,
        )

        assert result.decision == "pending"
        assert "k=" in result.reason

        # Confirm in table
        pending = tmp_relational.list_pending_promotions()
        assert len(pending) == 1


# ---------------------------------------------------------------------------
# Independent witness counting
# ---------------------------------------------------------------------------


class TestIndependentWitnessCount:
    def test_k3_independent_witnesses(self, gate_no_window: PromotionGate) -> None:
        """k=3 with 3 distinct (author_agent, source) pairs → admit."""
        crystal = _stub_crystal()
        witnesses = [
            CrystalRef(id="w1", author_agent="agent-A", source="verified_agent", trust_tier=Tier.T1),
            CrystalRef(id="w2", author_agent="agent-B", source="verified_agent", trust_tier=Tier.T1),
            CrystalRef(id="w3", author_agent="agent-C", source="verified_agent", trust_tier=Tier.T1),
        ]

        result = gate_no_window.propose_semantic(
            crystal=crystal,
            witnesses=witnesses,
            caller_tier=Tier.T1,
        )

        assert result.decision == "admit"

    def test_k2_same_agent_not_independent(
        self, gate_no_window: PromotionGate, tmp_relational
    ) -> None:
        """Same agent_agent corroborating 5x counts as 1 witness (not independent)."""
        crystal = _stub_crystal()
        witnesses = [
            CrystalRef(id=f"w{i}", author_agent="same-agent", source="verified_agent", trust_tier=Tier.T1)
            for i in range(5)
        ]

        result = gate_no_window.propose_semantic(
            crystal=crystal,
            witnesses=witnesses,
            caller_tier=Tier.T1,
        )

        # 5 refs but only 1 independent (same author_agent, same source)
        assert result.decision == "pending"

    def test_k3_different_sources_count_independently(
        self, gate_no_window: PromotionGate
    ) -> None:
        """Different (author_agent, source) pairs all count as independent witnesses."""
        crystal = _stub_crystal()
        witnesses = [
            CrystalRef(id="w1", author_agent="agent-A", source="verified_agent", trust_tier=Tier.T1),
            CrystalRef(id="w2", author_agent="agent-A", source="human", trust_tier=Tier.T0),
            CrystalRef(id="w3", author_agent="agent-B", source="verified_agent", trust_tier=Tier.T1),
        ]
        # All 3 have distinct (author_agent, source) tuples → k=3 → admit
        result = gate_no_window.propose_semantic(
            crystal=crystal,
            witnesses=witnesses,
            caller_tier=Tier.T1,
        )
        assert result.decision == "admit"


# ---------------------------------------------------------------------------
# force_promote tier check
# ---------------------------------------------------------------------------


class TestForcePromoteTierCheck:
    def test_force_promote_requires_t0(
        self, gate_no_window: PromotionGate
    ) -> None:
        """force=True requires caller_tier == T0."""
        crystal = _stub_crystal()

        # T0 should be allowed
        result = gate_no_window.propose_semantic(
            crystal=crystal, witnesses=[], caller_tier=Tier.T0, force=True
        )
        assert result.decision == "admit"

    @pytest.mark.parametrize("tier", [Tier.T1, Tier.T2, Tier.T3])
    def test_force_promote_raises_for_non_t0(
        self, gate_no_window: PromotionGate, tier: Tier
    ) -> None:
        """force=True raises TierViolation for T1, T2, T3."""
        crystal = _stub_crystal()

        with pytest.raises(TierViolation) as exc_info:
            gate_no_window.propose_semantic(
                crystal=crystal, witnesses=[], caller_tier=tier, force=True
            )

        assert exc_info.value.tier == tier
        assert exc_info.value.op == "force_promote"


# ---------------------------------------------------------------------------
# Procedural admission gate
# ---------------------------------------------------------------------------


class TestProceduralAdmissionGate:
    def test_procedural_without_verifier_stays_candidate(
        self, gate_no_window: PromotionGate
    ) -> None:
        """Procedural commit without verifier result stays 'pending' (candidate state)."""
        crystal = _stub_crystal()

        result = gate_no_window.propose_procedural(
            crystal=crystal,
            skill_invoke_result=None,
            caller_tier=Tier.T1,
        )

        assert result.decision == "pending"
        assert "no verifier result" in result.reason

    def test_procedural_with_failing_verifier_stays_candidate(
        self, gate_no_window: PromotionGate
    ) -> None:
        """Procedural with failing verifier → pending (candidate unchanged)."""
        crystal = _stub_crystal()
        failing = SkillResult(exit_code=1, stdout=b"", stderr=b"error", overflow_flag=False)

        result = gate_no_window.propose_procedural(
            crystal=crystal,
            skill_invoke_result=failing,
            caller_tier=Tier.T1,
        )

        assert result.decision == "pending"
        assert "exit_code=1" in result.reason

    def test_procedural_with_overflow_stays_candidate(
        self, gate_no_window: PromotionGate
    ) -> None:
        """Procedural with output overflow → pending (overflow = failure)."""
        crystal = _stub_crystal()
        overflow = SkillResult(exit_code=0, stdout=b"x" * 9000, stderr=b"", overflow_flag=True)

        result = gate_no_window.propose_procedural(
            crystal=crystal,
            skill_invoke_result=overflow,
            caller_tier=Tier.T1,
        )

        assert result.decision == "pending"
        assert "overflow=True" in result.reason

    def test_procedural_with_passing_verifier_no_window_admits(
        self, gate_no_window: PromotionGate
    ) -> None:
        """T1 + passing verifier + no human-confirm window → admit."""
        crystal = _stub_crystal()
        passing = SkillResult(exit_code=0, stdout=b"ok", stderr=b"", overflow_flag=False)

        result = gate_no_window.propose_procedural(
            crystal=crystal,
            skill_invoke_result=passing,
            caller_tier=Tier.T1,
        )

        assert result.decision == "admit"
        assert result.record_id == crystal["id"]

    def test_procedural_with_passing_verifier_in_window_pending(
        self, gate_in_window: PromotionGate, tmp_relational
    ) -> None:
        """T1 + passing verifier + human-confirm window → pending."""
        crystal = _stub_crystal()
        passing = SkillResult(exit_code=0, stdout=b"ok", stderr=b"", overflow_flag=False)

        result = gate_in_window.propose_procedural(
            crystal=crystal,
            skill_invoke_result=passing,
            caller_tier=Tier.T1,
        )

        assert result.decision == "pending"
        assert result.promotion_id is not None
        pending = tmp_relational.list_pending_promotions()
        assert len(pending) == 1

    def test_skill_result_passed_property(self) -> None:
        """SkillResult.passed is True only when exit_code==0 AND no overflow."""
        assert SkillResult(exit_code=0, stdout=b"", stderr=b"", overflow_flag=False).passed
        assert not SkillResult(exit_code=1, stdout=b"", stderr=b"", overflow_flag=False).passed
        assert not SkillResult(exit_code=0, stdout=b"", stderr=b"", overflow_flag=True).passed
        assert not SkillResult(exit_code=1, stdout=b"", stderr=b"", overflow_flag=True).passed


# ---------------------------------------------------------------------------
# Process pending (accept/reject)
# ---------------------------------------------------------------------------


class TestProcessPending:
    def test_accept_pending_promotion(
        self, gate_no_window: PromotionGate, tmp_relational
    ) -> None:
        """process_pending('accept') updates status to 'accepted'."""
        # Create a pending promotion first
        crystal = _stub_crystal("accept-test-crystal")
        witnesses = _make_witnesses(2)  # < k=3 → pending

        result = gate_no_window.propose_semantic(
            crystal=crystal, witnesses=witnesses, caller_tier=Tier.T1
        )
        assert result.decision == "pending"
        promotion_id = result.promotion_id

        # Accept it
        gate_no_window.process_pending(promotion_id, "accept")

        with tmp_relational._connect() as conn:
            row = conn.execute(
                "SELECT status FROM pending_promotions WHERE id = ?",
                (promotion_id,),
            ).fetchone()
        assert row["status"] == "accepted"

    def test_reject_pending_promotion(
        self, gate_no_window: PromotionGate, tmp_relational
    ) -> None:
        """process_pending('reject') updates status to 'rejected'."""
        crystal = _stub_crystal("reject-test-crystal")
        witnesses = _make_witnesses(2)

        result = gate_no_window.propose_semantic(
            crystal=crystal, witnesses=witnesses, caller_tier=Tier.T1
        )
        promotion_id = result.promotion_id

        gate_no_window.process_pending(promotion_id, "reject")

        with tmp_relational._connect() as conn:
            row = conn.execute(
                "SELECT status FROM pending_promotions WHERE id = ?",
                (promotion_id,),
            ).fetchone()
        assert row["status"] == "rejected"


class TestKOverride:
    """W3 D4 — STC lowers the corroboration bar via k_override, without bypassing
    witness counting or the human-confirm window (chokepoint-preserving)."""

    def test_k_override_lowers_bar(self, gate_no_window: PromotionGate) -> None:
        # 2 witnesses < config k=3 → would be pending; k_override=2 → admit.
        result = gate_no_window.propose_semantic(
            crystal=_stub_crystal(), witnesses=_make_witnesses(2),
            caller_tier=Tier.T1, k_override=2,
        )
        assert result.decision == "admit"

    def test_k_override_still_counts_witnesses(self, gate_no_window: PromotionGate) -> None:
        # 1 witness < k_override=2 → still pending (override is a threshold, not a bypass).
        result = gate_no_window.propose_semantic(
            crystal=_stub_crystal(), witnesses=_make_witnesses(1),
            caller_tier=Tier.T1, k_override=2,
        )
        assert result.decision == "pending"

    def test_k_override_floored_at_one(self, gate_no_window: PromotionGate) -> None:
        # k_override=0 floors to 1 → 1 witness admits, 0 witnesses stays pending
        # (never bypasses witness counting → never a force).
        assert gate_no_window.propose_semantic(
            crystal=_stub_crystal("c-a"), witnesses=_make_witnesses(1),
            caller_tier=Tier.T1, k_override=0,
        ).decision == "admit"
        assert gate_no_window.propose_semantic(
            crystal=_stub_crystal("c-b"), witnesses=_make_witnesses(0),
            caller_tier=Tier.T1, k_override=0,
        ).decision == "pending"

    def test_k_override_none_is_baseline(self, gate_no_window: PromotionGate) -> None:
        # None → config k=3: 2 witnesses pending, 3 admit (byte-identical to W2).
        assert gate_no_window.propose_semantic(
            crystal=_stub_crystal("c-c"), witnesses=_make_witnesses(2),
            caller_tier=Tier.T1, k_override=None,
        ).decision == "pending"

    def test_k_override_respects_human_confirm_window(self, gate_in_window: PromotionGate) -> None:
        # Inside the human-confirm window, even a lowered k stays pending.
        result = gate_in_window.propose_semantic(
            crystal=_stub_crystal(), witnesses=_make_witnesses(3),
            caller_tier=Tier.T1, k_override=1,
        )
        assert result.decision == "pending"
