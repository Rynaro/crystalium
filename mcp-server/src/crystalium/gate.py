"""Promotion gate — k-corroboration Semantic + verifier-pass Procedural admission.

Implements FORGE D8 (k-corroboration + human-confirm window) and FORGE D5
(verifier-gated Procedural admission).

Key invariants:
  G5: install_ts < 30d → proposals land in pending_promotions (not target layer).
  D8: independent witnesses (distinct author_agent AND distinct source) count
      toward k_corroboration threshold. If k reached AND no human-confirm window,
      auto-admit. Otherwise, pending.
  D5: Procedural commit from T1/T2 requires skill_invoke verifier-pass. Without
      a passing SkillResult, the record stays 'candidate'.

Source: .forge/reasoning-report.md §D8, §D5.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import structlog

from crystalium.config import Config
from crystalium.enforcement import Enforcement, PromotionGated, TierViolation
from crystalium.trust import Tier

log = structlog.get_logger("crystalium.gate")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PromotionDecision = Literal["admit", "pending", "reject"]


@dataclass
class PromotionResult:
    """Detailed result from a promotion proposal."""

    decision: PromotionDecision
    reason: str
    promotion_id: str | None = None  # set when decision == "pending"
    record_id: str | None = None     # set when decision == "admit"


@dataclass
class SkillResult:
    """Result from a skill_invoke subprocess execution (D5)."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    overflow_flag: bool

    @property
    def passed(self) -> bool:
        """True if verifier passed: exit_code == 0 AND no output overflow."""
        return self.exit_code == 0 and not self.overflow_flag


@dataclass
class CrystalRef:
    """Lightweight reference to a crystal — carries identity fields for witness counting."""

    id: str
    author_agent: str | None  # from provenance.author_agent
    source: str               # from provenance.source ("human"|"verified_agent"|...)
    trust_tier: Tier


# ---------------------------------------------------------------------------
# PromotionGate
# ---------------------------------------------------------------------------


class PromotionGate:
    """Validates and routes promotion proposals for Semantic and Procedural layers.

    Args:
        config:      Server-wide config (k_corroboration, human_confirm window).
        relational:  RelationalStore for pending_promotions persistence.
        enforcement: Enforcement instance for tier checks.
    """

    def __init__(
        self,
        config: Config,
        relational: Any,  # RelationalStore — late-import to avoid circular deps
        enforcement: Enforcement,
    ) -> None:
        self.config = config
        self.relational = relational
        self.enforcement = enforcement

    # ------------------------------------------------------------------
    # Semantic promotion (D8)
    # ------------------------------------------------------------------

    def propose_semantic(
        self,
        crystal: Any,  # Crystal-like dict or Crystal model
        witnesses: list[CrystalRef],
        caller_tier: Tier,
        force: bool = False,
        k_override: int | None = None,
    ) -> PromotionResult:
        """Evaluate a Semantic promotion proposal.

        D8 rule:
          - force=True requires caller_tier == T0; raises TierViolation otherwise.
          - If not forced: count independent witnesses (distinct author_agent AND
            distinct source). If count >= k_corroboration AND human_confirm is
            NOT active → admit. Else → pending.
          - If human_confirm IS active: always pending (regardless of k).

        CRYSTALIUM_AUTO_CONFIRM=1 env var bypasses human-confirm (test-only);
        a WARN log is emitted on every bypass.

        Args:
            crystal:     The crystal being proposed for Semantic admission.
            witnesses:   Corroborating crystal references.
            caller_tier: Trust tier of the proposing agent.
            force:       If True, bypass k-gate (requires T0).

        Returns:
            PromotionResult with decision in {"admit", "pending", "reject"}.

        Raises:
            TierViolation: If force=True and caller_tier != T0.
        """
        crystal_id = crystal["id"] if isinstance(crystal, dict) else crystal.id

        # force_promote requires T0 (D1 matrix: force_promote/semantic T0=allow, T1+=deny)
        if force:
            if caller_tier != Tier.T0:
                raise TierViolation(
                    "crystalium.gate.force_promote",
                    "semantic",
                    caller_tier,
                    "force_promote",
                )
            log.info(
                "semantic_force_promote",
                crystal_id=crystal_id,
                caller_tier=str(caller_tier),
            )
            self.relational.record_promotion(
                crystal_id, "semantic", now=datetime.now(timezone.utc)
            )
            return PromotionResult(
                decision="admit",
                reason="force_promote by T0",
                record_id=crystal_id,
            )

        # Count independent witnesses (D8: distinct author_agent AND distinct source)
        independent_count = self._count_independent_witnesses(witnesses)

        # Check human-confirm window (G5 / D8)
        human_confirm = self._human_confirm_active()
        # W3 STC (D4): a salient nearby event may lower the corroboration bar via
        # k_override. This ONLY changes the threshold — witnesses are still counted,
        # the human-confirm window still applies, and record_promotion still fires.
        # Floored at 1 so it can never bypass witness counting (never 0/force).
        k = self.config.k_corroboration if k_override is None else max(1, int(k_override))

        if not human_confirm and independent_count >= k:
            log.info(
                "semantic_auto_admit",
                crystal_id=crystal_id,
                witnesses=independent_count,
                k=k,
            )
            self.relational.record_promotion(
                crystal_id, "semantic", now=datetime.now(timezone.utc)
            )
            return PromotionResult(
                decision="admit",
                reason=f"k={independent_count}>={k} independent witnesses; auto-admit",
                record_id=crystal_id,
            )

        # Route to pending_promotions
        promotion_id = str(uuid.uuid4())
        self.relational.insert_pending_promotion(
            promotion_id=promotion_id,
            crystal_id=crystal_id,
            target_layer="semantic",
            proposed_at=datetime.now(timezone.utc),
        )
        reason = (
            "human-confirm window active"
            if human_confirm
            else f"k={independent_count}<{k} independent witnesses"
        )
        log.info(
            "semantic_promotion_pending",
            crystal_id=crystal_id,
            promotion_id=promotion_id,
            reason=reason,
        )
        return PromotionResult(
            decision="pending",
            reason=reason,
            promotion_id=promotion_id,
        )

    # ------------------------------------------------------------------
    # Procedural promotion (D5)
    # ------------------------------------------------------------------

    def propose_procedural(
        self,
        crystal: Any,
        skill_invoke_result: SkillResult | None,
        caller_tier: Tier,
    ) -> PromotionResult:
        """Evaluate a Procedural admission proposal.

        D5 rule:
          - Without a passing SkillResult (verifier exit_code==0 + no overflow):
            record stays 'candidate'.
          - T0/T1 with a passing SkillResult: admit to 'shared' state.
          - T2 ALWAYS stays 'candidate' (G2): a passing verifier never admits a T2
            proposal to shared/validated.
          - T3 is denied at the enforcement layer before reaching here.

        Also routes through human-confirm window when active (G5).

        Args:
            crystal:             Crystal being proposed.
            skill_invoke_result: Result of crystalium.skill_invoke; None = not run yet.
            caller_tier:         Trust tier of the proposing agent.

        Returns:
            PromotionResult with decision in {"admit", "pending"}.
        """
        crystal_id = crystal["id"] if isinstance(crystal, dict) else crystal.id

        # Battle-test fix (G2 defense-in-depth): the only live caller (ProceduralLayer
        # .commit) routes T2 to candidate before reaching the gate, but this chokepoint
        # method must enforce G2 itself — a future caller trusting the (now-corrected)
        # contract must never be able to admit a T2 proposal to shared, regardless of a
        # passing verifier.
        if caller_tier > Tier.T1:
            return PromotionResult(
                decision="pending",
                reason="T2 candidate-only per G2",
                promotion_id=None,
            )

        # No verifier result → candidate stays candidate
        if skill_invoke_result is None or not skill_invoke_result.passed:
            reason = (
                "no verifier result"
                if skill_invoke_result is None
                else f"verifier failed (exit_code={skill_invoke_result.exit_code}, "
                     f"overflow={skill_invoke_result.overflow_flag})"
            )
            log.info(
                "procedural_candidate_unchanged",
                crystal_id=crystal_id,
                reason=reason,
            )
            return PromotionResult(
                decision="pending",
                reason=reason,
                promotion_id=None,
            )

        # Verifier passed — check human-confirm window
        if self._human_confirm_active():
            promotion_id = str(uuid.uuid4())
            self.relational.insert_pending_promotion(
                promotion_id=promotion_id,
                crystal_id=crystal_id,
                target_layer="procedural",
                proposed_at=datetime.now(timezone.utc),
            )
            log.info(
                "procedural_promotion_pending",
                crystal_id=crystal_id,
                promotion_id=promotion_id,
                reason="human-confirm window active",
            )
            return PromotionResult(
                decision="pending",
                reason="human-confirm window active",
                promotion_id=promotion_id,
            )

        # Verifier passed + no human-confirm window → admit
        log.info(
            "procedural_admit",
            crystal_id=crystal_id,
            caller_tier=str(caller_tier),
        )
        self.relational.record_promotion(
            crystal_id, "procedural", now=datetime.now(timezone.utc)
        )
        return PromotionResult(
            decision="admit",
            reason="verifier passed",
            record_id=crystal_id,
        )

    # ------------------------------------------------------------------
    # Pending review (CLI: crystalium promote review)
    # ------------------------------------------------------------------

    def process_pending(
        self, promotion_id: str, action: Literal["accept", "reject"]
    ) -> None:
        """Accept or reject a pending promotion (for the CLI promote review subcommand).

        This is the human-in-the-loop path (G5 resolution).
        Actual layer insertion for "accept" is done by the caller (layer adapters)
        after checking the promotion status.

        Args:
            promotion_id: UUID from pending_promotions table.
            action:       "accept" or "reject".
        """
        status_map: dict[str, str] = {"accept": "accepted", "reject": "rejected"}
        db_status = status_map[action]
        self.relational.update_promotion_status(promotion_id, db_status)
        log.info(
            "pending_promotion_processed",
            promotion_id=promotion_id,
            action=action,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _count_independent_witnesses(self, witnesses: list[CrystalRef]) -> int:
        """Count independent witnesses per D8 definition.

        Independent = distinct (author_agent, source) tuple. A single agent
        corroborating from the same source multiple times counts as one witness.
        """
        seen: set[tuple[str | None, str]] = set()
        for w in witnesses:
            key = (w.author_agent, w.source)
            seen.add(key)
        return len(seen)

    def _human_confirm_active(self) -> bool:
        """Return True if human-confirm window is active (G5 / D8).

        CRYSTALIUM_AUTO_CONFIRM=1 bypasses with a WARN log (test-only).
        """
        if os.environ.get("CRYSTALIUM_AUTO_CONFIRM") == "1":
            log.warning(
                "human_confirm_bypassed",
                message="CRYSTALIUM_AUTO_CONFIRM=1 — human-confirm window bypassed (test-only).",
            )
            return False

        return self.config.human_confirm_active()
