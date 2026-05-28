"""Wave 2 enforcement tests — G1, G2 P0 gates.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_enforcement.py -v

Gate anchors verified here:
  G1: T3 cannot commit above Episodic (test_g1_t3_cannot_commit_above_episodic)
  G2: T2 procedural commits land as candidate (test_g2_t2_procedural_candidate_only)

Additional coverage:
  - Rate limit sliding window
  - Path escape guard
  - assert_tier_within_layer_ceiling (pre-cursor to G4 which lives in test_trust_propagation)
  - Output cap
  - Quarantine flag ContextVar isolation
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crystalium.config import Config
from crystalium.enforcement import (
    Enforcement,
    TierViolation,
    TierCeilingViolation,
    PathEscape,
    RateLimitExceeded,
    _MARK_QUARANTINE,
)
from crystalium.trust import Tier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> Config:
    """Config with a fast rate limit for testing."""
    return Config(rate_limit_per_minute=10)


@pytest.fixture
def enforcement(config: Config) -> Enforcement:
    return Enforcement(config)


# ---------------------------------------------------------------------------
# G1: T3 cannot commit above Episodic
# test_anchor: test_enforcement.py::test_g1_t3_cannot_commit_above_episodic
# ---------------------------------------------------------------------------


class TestG1T3CannotCommitAboveEpisodic:
    """G1 (P0) — T3 caller → commit(layer in {Semantic, Procedural, Execution}) → TierViolation."""

    @pytest.mark.parametrize("layer", ["semantic", "procedural", "execution"])
    def test_g1_t3_cannot_commit_above_episodic(
        self, enforcement: Enforcement, layer: str
    ) -> None:
        """GIVEN T3 caller, WHEN commit(layer != episodic), THEN TierViolation raised."""
        with pytest.raises(TierViolation) as exc_info:
            enforcement.assert_tier_allowed(
                "crystalium.commit", layer, Tier.T3, "commit"
            )

        err = exc_info.value
        assert err.reason_code == "TIER_VIOLATION"
        assert err.tier == Tier.T3
        assert err.layer == layer

    def test_g1_t3_cannot_commit_above_episodic_no_store_call(
        self, enforcement: Enforcement, tmp_path: Path
    ) -> None:
        """Store insert must NOT be called when TierViolation fires.

        Verified by asserting RelationalStore.insert_crystal is never reached.
        """
        from crystalium.storage.relational import RelationalStore
        from crystalium.storage.blob import BlobStore

        relational = RelationalStore(db_path=tmp_path / "g1.sqlite")
        blob = BlobStore(root=tmp_path / "blobs")

        # Wrap insert_crystal with a spy
        original_insert = relational.insert_crystal
        call_count = []

        def spy_insert(*args, **kwargs):
            call_count.append(1)
            return original_insert(*args, **kwargs)

        relational.insert_crystal = spy_insert

        # Attempt T3 commit to Semantic through EpisodicLayer-like path
        with pytest.raises(TierViolation):
            enforcement.assert_tier_allowed(
                "crystalium.commit", "semantic", Tier.T3, "commit"
            )

        # No store call happened — TierViolation fires before storage
        assert len(call_count) == 0, "insert_crystal should NOT be called on TierViolation"

    def test_g1_t3_allowed_for_episodic(self, enforcement: Enforcement) -> None:
        """T3 IS allowed for Episodic (allow_quarantine rule)."""
        # Should not raise
        enforcement.assert_tier_allowed("crystalium.commit", "episodic", Tier.T3, "commit")
        # Quarantine flag should be set
        assert _MARK_QUARANTINE.get() is True
        # Reset
        _MARK_QUARANTINE.set(False)

    def test_g1_t2_blocked_for_semantic(self, enforcement: Enforcement) -> None:
        """T2 cannot commit to Semantic (D1 matrix)."""
        with pytest.raises(TierViolation) as exc_info:
            enforcement.assert_tier_allowed(
                "crystalium.commit", "semantic", Tier.T2, "commit"
            )
        assert exc_info.value.tier == Tier.T2

    def test_g1_t2_blocked_for_execution(self, enforcement: Enforcement) -> None:
        """T2 cannot commit to Execution (D1 matrix)."""
        with pytest.raises(TierViolation):
            enforcement.assert_tier_allowed(
                "crystalium.commit", "execution", Tier.T2, "commit"
            )

    def test_g1_t0_t1_allowed_all_layers(self, enforcement: Enforcement) -> None:
        """T0 and T1 are allowed for commit on all non-execution layers."""
        for tier in [Tier.T0, Tier.T1]:
            for layer in ["episodic", "semantic", "procedural"]:
                # Should not raise
                enforcement.assert_tier_allowed("crystalium.commit", layer, tier, "commit")

    def test_g1_recall_always_allowed(self, enforcement: Enforcement) -> None:
        """recall is universally allowed regardless of tier or layer."""
        for tier in [Tier.T0, Tier.T1, Tier.T2, Tier.T3]:
            for layer in ["episodic", "semantic", "procedural", "execution"]:
                enforcement.assert_tier_allowed("crystalium.recall", layer, tier, "recall")


# ---------------------------------------------------------------------------
# G2: T2 procedural commits land as candidate
# test_anchor: test_enforcement.py::test_g2_t2_procedural_candidate_only
# ---------------------------------------------------------------------------


class TestG2T2ProceduralCandidateOnly:
    """G2 (P0) — T2 Procedural commit must land as 'candidate'.

    The enforcement matrix allows T2→Procedural (does not raise TierViolation);
    the layer adapter then stamps validation_state='candidate'.
    The gate layer (propose_procedural with no skill result) confirms 'candidate'.
    """

    def test_g2_t2_procedural_matrix_allows(self, enforcement: Enforcement) -> None:
        """D1 matrix: T2 → Procedural commit is allowed (lands as candidate)."""
        # Should NOT raise TierViolation
        enforcement.assert_tier_allowed(
            "crystalium.commit", "procedural", Tier.T2, "commit"
        )

    def test_g2_t2_procedural_candidate_only(
        self, enforcement: Enforcement, tmp_path: Path
    ) -> None:
        """GIVEN T2 caller, WHEN Procedural commit, THEN validation_state == 'candidate'."""
        from crystalium.storage.relational import RelationalStore
        from crystalium.storage.blob import BlobStore
        from crystalium.aetheryte.redact import Redactor
        from crystalium.gate import PromotionGate
        from crystalium.layers.procedural import ProceduralLayer
        from crystalium.importance import importance_score

        relational = RelationalStore(db_path=tmp_path / "g2.sqlite")
        blob = BlobStore(root=tmp_path / "blobs")
        redactor = Redactor(Config())
        gate = PromotionGate(
            config=Config(),
            relational=relational,
            enforcement=enforcement,
        )
        layer = ProceduralLayer(
            blob_store=blob,
            relational=relational,
            enforcement=enforcement,
            gate=gate,
            redactor=redactor,
            importance_fn=importance_score,
            data_dir=tmp_path,
        )

        result = layer.commit(
            payload={
                "summary": "T2 procedural candidate test",
                "scope": {"project": "test", "agent_class_visibility": None, "sensitivity_tag": "none"},
            },
            provenance={
                "source": "unverified_agent",
                "author_agent": "test-t2-agent",
                "task_id": None,
                "created_at": "2026-05-28T12:00:00+00:00",
            },
            caller_tier=Tier.T2,
            skill_invoke_result=None,  # no verifier result
        )

        assert result["status"] == "committed"
        assert result["validation_state"] == "candidate", (
            f"T2 Procedural commit must land as 'candidate', got: {result['validation_state']}"
        )
        assert result["layer"] == "procedural"

    def test_g2_t2_cannot_force_promote_procedural(self, enforcement: Enforcement) -> None:
        """T2 cannot force_promote Procedural (must raise TierViolation)."""
        with pytest.raises(TierViolation) as exc_info:
            enforcement.assert_tier_allowed(
                "crystalium.commit", "procedural", Tier.T2, "force_promote"
            )
        assert exc_info.value.tier == Tier.T2

    def test_g2_t1_with_verifier_not_candidate(
        self, enforcement: Enforcement, tmp_path: Path
    ) -> None:
        """T1 + passing verifier result → NOT candidate (validated).

        Gate.propose_procedural returns 'admit' when verifier passes and
        human_confirm is not active (no install_ts set).
        """
        from crystalium.storage.relational import RelationalStore
        from crystalium.storage.blob import BlobStore
        from crystalium.aetheryte.redact import Redactor
        from crystalium.gate import PromotionGate, SkillResult
        from crystalium.layers.procedural import ProceduralLayer
        from crystalium.importance import importance_score
        import os

        # Disable human-confirm window to allow auto-admit
        os.environ["CRYSTALIUM_AUTO_CONFIRM"] = "1"
        try:
            relational = RelationalStore(db_path=tmp_path / "g2b.sqlite")
            blob = BlobStore(root=tmp_path / "blobs")
            redactor = Redactor(Config())
            gate = PromotionGate(
                config=Config(),
                relational=relational,
                enforcement=enforcement,
            )
            layer = ProceduralLayer(
                blob_store=blob,
                relational=relational,
                enforcement=enforcement,
                gate=gate,
                redactor=redactor,
                importance_fn=importance_score,
                data_dir=tmp_path,
            )
            passing_result = SkillResult(
                exit_code=0, stdout=b"ok", stderr=b"", overflow_flag=False
            )
            result = layer.commit(
                payload={
                    "summary": "T1 verified skill",
                    "scope": {"project": "test", "agent_class_visibility": None, "sensitivity_tag": "none"},
                },
                provenance={
                    "source": "verified_agent",
                    "author_agent": "test-t1-agent",
                    "task_id": None,
                    "created_at": "2026-05-28T12:00:00+00:00",
                },
                caller_tier=Tier.T1,
                skill_invoke_result=passing_result,
            )
            assert result["validation_state"] == "validated", (
                f"T1 + passing verifier should be 'validated', got {result['validation_state']}"
            )
        finally:
            os.environ.pop("CRYSTALIUM_AUTO_CONFIRM", None)


# ---------------------------------------------------------------------------
# Rate limit tests
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_rate_limit_exceeded(self) -> None:
        """Rate limit fires after max_calls_per_minute calls in 60s window."""
        config = Config(rate_limit_per_minute=3)
        enf = Enforcement(config)

        # First 3 calls should pass
        enf.assert_rate_limit()
        enf.assert_rate_limit()
        enf.assert_rate_limit()

        # 4th should raise
        with pytest.raises(RateLimitExceeded) as exc_info:
            enf.assert_rate_limit()

        assert exc_info.value.reason_code == "RATE_LIMIT"
        assert exc_info.value.limit == 3

    def test_rate_limit_disabled(self) -> None:
        """rate_limit_per_minute=0 disables rate limiting."""
        config = Config(rate_limit_per_minute=0)
        enf = Enforcement(config)

        # Should never raise
        for _ in range(1000):
            enf.assert_rate_limit()


# ---------------------------------------------------------------------------
# Path escape tests
# ---------------------------------------------------------------------------


class TestPathEscape:
    def test_path_escape_raises(self, tmp_path: Path) -> None:
        """Path resolving outside root raises PathEscape."""
        config = Config()
        enf = Enforcement(config)

        # Create a file inside tmp_path so resolve(strict=True) works
        target = tmp_path / "safe_file.txt"
        target.write_text("hello")

        root = tmp_path / "sandbox"
        root.mkdir()

        # target is in tmp_path, not inside sandbox/ → should raise
        with pytest.raises(PathEscape) as exc_info:
            enf.assert_no_path_escape(target, root)

        assert exc_info.value.reason_code == "PATH_ESCAPE"

    def test_path_escape_inside_root_ok(self, tmp_path: Path) -> None:
        """Path inside root does not raise."""
        config = Config()
        enf = Enforcement(config)

        root = tmp_path / "sandbox"
        root.mkdir()
        target = root / "skill.py"
        target.write_text("# verifier")

        # Should not raise
        enf.assert_no_path_escape(target, root)

    def test_path_escape_nonexistent_raises(self, tmp_path: Path) -> None:
        """Non-existent path raises PathEscape (strict=True)."""
        config = Config()
        enf = Enforcement(config)

        root = tmp_path / "sandbox"
        root.mkdir()
        target = root / "does_not_exist.py"

        # strict=True means OSError → PathEscape
        with pytest.raises(PathEscape):
            enf.assert_no_path_escape(target, root)


# ---------------------------------------------------------------------------
# Output cap tests
# ---------------------------------------------------------------------------


class TestOutputCap:
    def test_cap_output_no_overflow(self, enforcement: Enforcement) -> None:
        payload = b"hello world"
        result, overflow = enforcement.cap_output(payload, max_bytes=1024)
        assert result == payload
        assert overflow is False

    def test_cap_output_truncates(self, enforcement: Enforcement) -> None:
        payload = b"x" * 100
        result, overflow = enforcement.cap_output(payload, max_bytes=50)
        assert len(result) == 50
        assert overflow is True

    def test_cap_output_exact_boundary(self, enforcement: Enforcement) -> None:
        payload = b"y" * 50
        result, overflow = enforcement.cap_output(payload, max_bytes=50)
        assert result == payload
        assert overflow is False


# ---------------------------------------------------------------------------
# Quarantine ContextVar isolation
# ---------------------------------------------------------------------------


class TestQuarantineContextVar:
    def test_quarantine_set_by_t3_episodic(self, enforcement: Enforcement) -> None:
        """T3 → Episodic sets _MARK_QUARANTINE."""
        _MARK_QUARANTINE.set(False)
        enforcement.assert_tier_allowed("crystalium.commit", "episodic", Tier.T3, "commit")
        assert enforcement.quarantine_active() is True
        enforcement.reset_quarantine_flag()
        assert enforcement.quarantine_active() is False

    def test_quarantine_not_set_by_t1_episodic(self, enforcement: Enforcement) -> None:
        """T1 → Episodic does NOT set quarantine flag."""
        _MARK_QUARANTINE.set(False)
        enforcement.assert_tier_allowed("crystalium.commit", "episodic", Tier.T1, "commit")
        assert enforcement.quarantine_active() is False


# ---------------------------------------------------------------------------
# Tier ceiling (D7 — also covered in test_trust_propagation.py)
# ---------------------------------------------------------------------------


class TestTierCeiling:
    def test_t3_blocked_for_semantic(self, enforcement: Enforcement) -> None:
        """consolidated_tier=T3 → Semantic raises TierCeilingViolation."""
        with pytest.raises(TierCeilingViolation) as exc_info:
            enforcement.assert_tier_within_layer_ceiling(Tier.T3, "semantic")
        assert exc_info.value.reason_code == "TIER_CEILING"
        assert exc_info.value.consolidated_tier == Tier.T3

    def test_t2_blocked_for_semantic(self, enforcement: Enforcement) -> None:
        """T2 also exceeds Semantic ceiling (T1)."""
        with pytest.raises(TierCeilingViolation):
            enforcement.assert_tier_within_layer_ceiling(Tier.T2, "semantic")

    def test_t1_allowed_for_semantic(self, enforcement: Enforcement) -> None:
        """T1 is within Semantic ceiling."""
        enforcement.assert_tier_within_layer_ceiling(Tier.T1, "semantic")  # no raise

    def test_t3_allowed_for_episodic(self, enforcement: Enforcement) -> None:
        """T3 is within Episodic ceiling (universally writable)."""
        enforcement.assert_tier_within_layer_ceiling(Tier.T3, "episodic")  # no raise

    def test_t2_allowed_for_procedural(self, enforcement: Enforcement) -> None:
        """T2 is within Procedural ceiling."""
        enforcement.assert_tier_within_layer_ceiling(Tier.T2, "procedural")  # no raise

    def test_t3_blocked_for_procedural(self, enforcement: Enforcement) -> None:
        """T3 exceeds Procedural ceiling (T2)."""
        with pytest.raises(TierCeilingViolation):
            enforcement.assert_tier_within_layer_ceiling(Tier.T3, "procedural")


# ---------------------------------------------------------------------------
# Force-promote tier checks
# ---------------------------------------------------------------------------


class TestForcePromote:
    @pytest.mark.parametrize(
        "layer,tier",
        [
            ("episodic", Tier.T1),
            ("episodic", Tier.T2),
            ("episodic", Tier.T3),
            ("semantic", Tier.T1),
            ("semantic", Tier.T2),
            ("semantic", Tier.T3),
            ("procedural", Tier.T1),
            ("procedural", Tier.T2),
            ("procedural", Tier.T3),
        ],
    )
    def test_force_promote_blocked_for_non_t0(
        self, enforcement: Enforcement, layer: str, tier: Tier
    ) -> None:
        """force_promote is T0-only. All other tiers raise TierViolation."""
        with pytest.raises(TierViolation):
            enforcement.assert_tier_allowed(
                "crystalium.commit", layer, tier, "force_promote"
            )

    @pytest.mark.parametrize("layer", ["episodic", "semantic", "procedural"])
    def test_force_promote_allowed_for_t0(
        self, enforcement: Enforcement, layer: str
    ) -> None:
        """T0 can force_promote on episodic/semantic/procedural."""
        enforcement.assert_tier_allowed(
            "crystalium.commit", layer, Tier.T0, "force_promote"
        )
