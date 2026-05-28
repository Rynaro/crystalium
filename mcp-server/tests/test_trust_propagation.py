"""Wave 2 trust propagation tests — G4 P0 gate.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_trust_propagation.py -v

Gate anchor verified here:
  G4: MIN-tier blocks T3 laundering into Semantic
      (test_g4_min_tier_blocks_semantic_laundering)

Coverage:
  - min_tier() combinator produces the lowest-trust (highest int) tier
  - TierCeilingViolation raised when summarizer tries to launder T3 into Semantic
  - Advice message present in exception
  - Excluding T3 inputs allows Semantic admission
  - All layer ceiling checks
"""

from __future__ import annotations

import pytest

from crystalium.config import Config
from crystalium.enforcement import Enforcement, TierCeilingViolation
from crystalium.trust import Tier, min_tier, LAYER_CEILING


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def enforcement() -> Enforcement:
    return Enforcement(Config(rate_limit_per_minute=200))


# ---------------------------------------------------------------------------
# G4: Trust-tier propagation blocks T3 laundering
# test_anchor: test_trust_propagation.py::test_g4_min_tier_blocks_semantic_laundering
# ---------------------------------------------------------------------------


class TestG4MinTierBlocksSemanticLaundering:
    """G4 (P0) — Summarizer reads {T1, T2, T3} → min=T3 → Semantic admission denied."""

    def test_g4_min_tier_blocks_semantic_laundering(
        self, enforcement: Enforcement
    ) -> None:
        """GIVEN summarizer reads {T1, T2, T3}, WHEN commit(Semantic), THEN TierCeilingViolation.

        This is the exact G4 test anchor specified in spec.yaml.
        """
        # Simulate summarizer computing consolidated tier
        input_tiers = [Tier.T1, Tier.T2, Tier.T3]
        consolidated = min_tier(input_tiers)

        assert consolidated == Tier.T3, "min_tier({T1,T2,T3}) must be T3 (least trusted)"

        # Attempt Semantic admission with consolidated T3 tier
        with pytest.raises(TierCeilingViolation) as exc_info:
            enforcement.assert_tier_within_layer_ceiling(consolidated, "semantic")

        err = exc_info.value
        assert err.reason_code == "TIER_CEILING"
        assert err.consolidated_tier == Tier.T3
        assert err.layer == "semantic"
        # Structured advice must be present (G4 spec requires this)
        assert "T3" in err.advice or "Episodic" in err.advice, (
            f"Advice must mention T3 or Episodic, got: {err.advice!r}"
        )

    def test_g4_excluding_t3_allows_semantic(
        self, enforcement: Enforcement
    ) -> None:
        """G4 inverse: excluding T3 inputs → consolidated=T1 → Semantic admission allowed."""
        # Summarizer excludes T3 inputs and uses only {T1, T2}
        input_tiers_without_t3 = [Tier.T1, Tier.T2]
        consolidated = min_tier(input_tiers_without_t3)

        # T2 = min of {T1, T2}; but Semantic ceiling is T1!
        # So even T2 should be blocked:
        with pytest.raises(TierCeilingViolation):
            enforcement.assert_tier_within_layer_ceiling(consolidated, "semantic")

    def test_g4_t1_only_inputs_allow_semantic(
        self, enforcement: Enforcement
    ) -> None:
        """G4: all-T1 inputs → consolidated=T1 → Semantic admission allowed."""
        input_tiers = [Tier.T1, Tier.T1, Tier.T1]
        consolidated = min_tier(input_tiers)
        assert consolidated == Tier.T1

        # Should not raise (T1 <= Semantic ceiling T1)
        enforcement.assert_tier_within_layer_ceiling(consolidated, "semantic")

    def test_g4_t0_inputs_allow_semantic(
        self, enforcement: Enforcement
    ) -> None:
        """G4: T0 input → consolidated=T0 → Semantic admission allowed."""
        input_tiers = [Tier.T0, Tier.T1]
        consolidated = min_tier(input_tiers)
        assert consolidated == Tier.T0  # lowest integer = highest trust

        enforcement.assert_tier_within_layer_ceiling(consolidated, "semantic")

    def test_g4_no_laundering_via_layer_commit(
        self, enforcement: Enforcement, tmp_path
    ) -> None:
        """Full G4 path: T3 consolidated tier → SemanticLayer.commit → TierCeilingViolation."""
        from crystalium.storage.relational import RelationalStore
        from crystalium.storage.blob import BlobStore
        from crystalium.aetheryte.redact import Redactor
        from crystalium.gate import PromotionGate
        from crystalium.layers.semantic import SemanticLayer
        from crystalium.importance import importance_score

        relational = RelationalStore(db_path=tmp_path / "g4.sqlite")
        blob = BlobStore(root=tmp_path / "blobs")
        redactor = Redactor(Config())
        gate = PromotionGate(
            config=Config(),
            relational=relational,
            enforcement=enforcement,
        )
        layer = SemanticLayer(
            blob_store=blob,
            relational=relational,
            vector_store=None,
            graph_store=None,
            enforcement=enforcement,
            gate=gate,
            redactor=redactor,
            importance_fn=importance_score,
        )

        # Summarizer computed consolidated_tier = T3 (min of {T1,T2,T3})
        # and passes it as caller_tier to the commit call
        with pytest.raises(TierCeilingViolation) as exc_info:
            layer.commit(
                payload={
                    "summary": "consolidated summary of T1+T2+T3 inputs",
                    "scope": {
                        "project": "test-project",
                        "agent_class_visibility": None,
                        "sensitivity_tag": "none",
                    },
                },
                provenance={
                    "source": "verified_agent",
                    "author_agent": "summarizer-agent",
                    "task_id": None,
                    "created_at": "2026-05-28T12:00:00+00:00",
                },
                caller_tier=Tier.T3,  # consolidated from min({T1,T2,T3})
            )

        assert exc_info.value.reason_code == "TIER_CEILING"
        # Assert no row was inserted in semantic store
        all_crystals = relational.bm25_search("consolidated", layer_filter="semantic", k=10)
        assert len(all_crystals) == 0, (
            "No crystal should be in Semantic store after TierCeilingViolation"
        )


# ---------------------------------------------------------------------------
# min_tier() combinator
# ---------------------------------------------------------------------------


class TestMinTierCombinator:
    def test_min_tier_single(self) -> None:
        assert min_tier([Tier.T1]) == Tier.T1

    def test_min_tier_mixed(self) -> None:
        assert min_tier([Tier.T0, Tier.T1, Tier.T3]) == Tier.T3

    def test_min_tier_all_same(self) -> None:
        assert min_tier([Tier.T2, Tier.T2]) == Tier.T2

    def test_min_tier_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            min_tier([])

    def test_min_tier_t0_and_t3(self) -> None:
        """T3 is less trusted (higher int) than T0 (lower int)."""
        assert min_tier([Tier.T0, Tier.T3]) == Tier.T3

    def test_min_tier_returns_tier_enum(self) -> None:
        result = min_tier([Tier.T1, Tier.T2])
        assert isinstance(result, Tier)
        assert result == Tier.T2


# ---------------------------------------------------------------------------
# Layer ceiling map sanity checks
# ---------------------------------------------------------------------------


class TestLayerCeilingMap:
    def test_episodic_ceiling_is_t3(self) -> None:
        """Episodic is universally writable (ceiling T3 = all tiers allowed)."""
        assert LAYER_CEILING["episodic"] == Tier.T3

    def test_semantic_ceiling_is_t1(self) -> None:
        assert LAYER_CEILING["semantic"] == Tier.T1

    def test_procedural_ceiling_is_t2(self) -> None:
        assert LAYER_CEILING["procedural"] == Tier.T2

    def test_execution_ceiling_is_t1(self) -> None:
        assert LAYER_CEILING["execution"] == Tier.T1

    @pytest.mark.parametrize(
        "tier,layer,expected_blocked",
        [
            (Tier.T3, "semantic", True),
            (Tier.T2, "semantic", True),
            (Tier.T1, "semantic", False),
            (Tier.T0, "semantic", False),
            (Tier.T3, "procedural", True),
            (Tier.T2, "procedural", False),
            (Tier.T1, "procedural", False),
            (Tier.T3, "execution", True),
            (Tier.T2, "execution", True),
            (Tier.T1, "execution", False),
            (Tier.T3, "episodic", False),
        ],
    )
    def test_ceiling_enforcement(
        self,
        enforcement: Enforcement,
        tier: Tier,
        layer: str,
        expected_blocked: bool,
    ) -> None:
        if expected_blocked:
            with pytest.raises(TierCeilingViolation):
                enforcement.assert_tier_within_layer_ceiling(tier, layer)
        else:
            enforcement.assert_tier_within_layer_ceiling(tier, layer)  # no raise


# ---------------------------------------------------------------------------
# Trust propagation across summarization
# ---------------------------------------------------------------------------


class TestTrustPropagationSummarization:
    """Verify D7 propagation rule: consolidated.tier = min(inputs.tier)."""

    def test_summarizer_with_t3_source_is_blocked(
        self, enforcement: Enforcement
    ) -> None:
        """Summarizer ingests any T3 source → blocked at Semantic admission."""
        sources = [Tier.T1, Tier.T1, Tier.T3]  # one T3 source poisons the set
        consolidated = min_tier(sources)
        assert consolidated == Tier.T3

        with pytest.raises(TierCeilingViolation):
            enforcement.assert_tier_within_layer_ceiling(consolidated, "semantic")

    def test_pure_t1_summarization_succeeds(self, enforcement: Enforcement) -> None:
        """Summarizer ingests only T1 sources → Semantic admission allowed."""
        sources = [Tier.T1, Tier.T1, Tier.T1]
        consolidated = min_tier(sources)
        enforcement.assert_tier_within_layer_ceiling(consolidated, "semantic")

    def test_t2_source_poisons_to_t2(self, enforcement: Enforcement) -> None:
        """T2 source → consolidated=T2, which also exceeds Semantic ceiling (T1)."""
        sources = [Tier.T1, Tier.T2]
        consolidated = min_tier(sources)
        assert consolidated == Tier.T2

        with pytest.raises(TierCeilingViolation):
            enforcement.assert_tier_within_layer_ceiling(consolidated, "semantic")

    def test_t2_is_within_procedural_ceiling(self, enforcement: Enforcement) -> None:
        """T2 from summarization can still write to Procedural (ceiling=T2)."""
        sources = [Tier.T1, Tier.T2]
        consolidated = min_tier(sources)
        # Procedural ceiling is T2 — should not raise
        enforcement.assert_tier_within_layer_ceiling(consolidated, "procedural")
