"""Enforcement chokepoint for CRYSTALIUM.

This module is THE single mechanical sink for all mutation paths. Every write,
promote, and skill-invoke call MUST pass through here before any storage layer
is touched. This is the security and correctness backbone of CRYSTALIUM.

Architecture citations (from .forge/reasoning-report.md):
  - D1 (.forge/reasoning-report.md §D1): Tier × Layer × Operation matrix. The
    _MATRIX frozen dict encodes the full 12-row table from D1. Raising TierViolation
    here short-circuits the handler before any store call.
  - D5 (.forge/reasoning-report.md §D5): skill_invoke sandbox contract — path guard
    + output cap feed into assert_no_path_escape + cap_output.
  - D7 (.forge/reasoning-report.md §D7): MIN-tier propagation blocks Semantic
    laundering via assert_tier_within_layer_ceiling. Error class TierCeilingViolation
    carries structured advice per the D7 emit.
  - D8 (.forge/reasoning-report.md §D8): Human-confirm window. The gate.py layer
    consumes the config predicate; enforcement records every call outcome via record().

P0 invariants guarded here (MISSION.md §2.2):
  P0-1: T3 Episodic-quarantine only. _MARK_QUARANTINE ContextVar is the bridge.
  P0-2: T3 content writes ONLY Episodic-quarantined. Matrix denies all other targets.
  P0-7: path-traversal guard + per-process rate limit + telemetry on every call.

Pattern mirrors atlas-aci/mcp-server/src/atlas_aci/enforcement.py (FINDING-001).
"""

from __future__ import annotations

import os
import time
from collections import deque
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import structlog

from crystalium.config import Config
from crystalium.telemetry import record_call
from crystalium.trust import Tier, LAYER_CEILING

log = structlog.get_logger("crystalium.enforcement")


# ---------------------------------------------------------------------------
# _MARK_QUARANTINE ContextVar (D1 allow_quarantine logic)
#
# Set to True by assert_tier_allowed when rule == "allow_quarantine" (T3 → Episodic).
# Consumed by the commit handler to stamp validation_state="quarantined".
# T3 callers CANNOT spoof this — the parameter doesn't appear in the public MCP
# tool surface. ContextVar isolation prevents concurrent request cross-contamination.
# ---------------------------------------------------------------------------

_MARK_QUARANTINE: ContextVar[bool] = ContextVar("_MARK_QUARANTINE", default=False)


# ---------------------------------------------------------------------------
# Exception hierarchy
#
# Each exception carries a reason_code matching the commit-result.v1.json enum.
# ---------------------------------------------------------------------------


class CrystaliumEnforcementError(Exception):
    """Base class for all enforcement exceptions."""

    reason_code: str = "UNKNOWN"

    def __init__(self, message: str, *, reason_code: str | None = None, advice: str = "") -> None:
        self.message = message
        self.advice = advice
        if reason_code is not None:
            self.reason_code = reason_code
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "error": self.reason_code,
            "message": self.message,
        }
        if self.advice:
            d["advice"] = self.advice
        return d


class TierViolation(CrystaliumEnforcementError):
    """Raised when a caller's trust tier is not permitted for the operation + layer.

    G1: T3 caller → commit(layer=Semantic|Procedural|Execution) → TierViolation.
    """

    reason_code = "TIER_VIOLATION"

    def __init__(
        self,
        tool: str,
        layer: str,
        tier: Tier,
        op: str,
    ) -> None:
        self.tool = tool
        self.layer = layer
        self.tier = tier
        self.op = op
        super().__init__(
            f"Tier {tier} is not permitted for {op!r} on layer {layer!r} via {tool!r}.",
            advice=f"Use a lower-trust layer or escalate caller identity above {tier}.",
        )


class TierCeilingViolation(CrystaliumEnforcementError):
    """Raised when consolidated.tier > layer ceiling (D7 MIN-trust propagation).

    G4: summarizer reads {T1,T2,T3} → emits Semantic candidate → consolidated.tier=T3
    → layer ceiling for Semantic is T1 → TierCeilingViolation raised.
    """

    reason_code = "TIER_CEILING"

    def __init__(self, consolidated_tier: Tier, layer: str) -> None:
        self.consolidated_tier = consolidated_tier
        self.layer = layer
        ceiling = LAYER_CEILING.get(layer)
        super().__init__(
            f"Consolidated trust tier {consolidated_tier} exceeds layer {layer!r} ceiling "
            f"({ceiling}). T3 input in the source set launders trust.",
            advice="Exclude T3 inputs from the summarization pass, or commit to Episodic instead.",
        )


class PromotionGated(CrystaliumEnforcementError):
    """Raised when promotion is attempted without satisfying the verifier gate.

    G2: T2 caller → Procedural commit lands as 'candidate'; attempting force-promote
    without T1+ verifier-pass raises this.
    """

    reason_code = "PROMOTION_GATE"

    def __init__(self, reason: str = "") -> None:
        super().__init__(
            f"Promotion gated: {reason}" if reason else "Promotion requires verifier-pass first.",
            advice="Run crystalium.skill_invoke to satisfy the verifier gate (G3).",
        )


class VerifierFailed(CrystaliumEnforcementError):
    """Raised when a skill verifier subprocess exits non-zero or overflows output cap."""

    reason_code = "VERIFIER_FAIL"

    def __init__(self, skill_id: str, exit_code: int | None = None, overflow: bool = False) -> None:
        self.skill_id = skill_id
        self.exit_code = exit_code
        self.overflow = overflow
        parts = []
        if exit_code is not None:
            parts.append(f"exit_code={exit_code}")
        if overflow:
            parts.append("output_overflow=True")
        detail = ", ".join(parts) or "unknown failure"
        super().__init__(
            f"Verifier for skill {skill_id!r} failed: {detail}.",
            advice="Fix the verifier script or reduce verifier verbosity.",
        )


class PathEscape(CrystaliumEnforcementError):
    """Raised when a path resolves outside its permitted root (path-traversal attempt)."""

    reason_code = "PATH_ESCAPE"

    def __init__(self, target: Path, root: Path) -> None:
        self.target = target
        self.root = root
        super().__init__(
            f"Path {str(target)!r} resolves outside permitted root {str(root)!r}. "
            "Path-traversal rejected.",
            advice="Use an absolute path that resolves inside the permitted directory.",
        )


class RateLimitExceeded(CrystaliumEnforcementError):
    """Raised when the per-process rate limit (sliding 60s window) is exceeded."""

    reason_code = "RATE_LIMIT"

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f"Rate limit exceeded: max {limit} calls/minute. Wait before retrying.",
            advice="Reduce call frequency or increase rate_limit_per_minute in crystalium.yaml.",
        )


class OutputCapExceeded(CrystaliumEnforcementError):
    """Raised when an output payload exceeds the configured byte cap."""

    reason_code = "PATH_ESCAPE"  # reuses PATH_ESCAPE code; overflow is a safety violation

    def __init__(self, actual: int, cap: int) -> None:
        self.actual = actual
        self.cap = cap
        super().__init__(
            f"Output size {actual} bytes exceeds cap {cap} bytes.",
            advice="Reduce output verbosity or increase skill_invoke_output_cap_bytes.",
        )


# ---------------------------------------------------------------------------
# D1 Tier × Layer × Operation Matrix (frozen)
#
# Key: (layer: str, op: str) → dict[Tier, rule: str]
# rule ∈ {"allow", "allow_quarantine", "deny", "n/a"}
#
# "allow_quarantine": write is permitted but _MARK_QUARANTINE is set on the thread.
# "n/a":             operation not applicable for this layer (treated as deny in code).
#
# Full matrix from D1 emit (.forge/reasoning-report.md §D1):
#   episodic   commit:           T0=allow, T1=allow, T2=allow, T3=allow_quarantine
#   episodic   propose_promote:  T0=allow, T1=allow, T2=deny,  T3=deny
#   episodic   force_promote:    T0=allow, T1=deny,  T2=deny,  T3=deny
#   semantic   commit:           T0=allow, T1=allow, T2=deny,  T3=deny
#   semantic   propose_promote:  T0=allow, T1=allow, T2=deny,  T3=deny
#   semantic   force_promote:    T0=allow, T1=deny,  T2=deny,  T3=deny
#   procedural commit:           T0=allow, T1=allow, T2=allow, T3=deny  (T1/T2 lands as candidate)
#   procedural propose_promote:  T0=allow, T1=allow, T2=deny,  T3=deny
#   procedural force_promote:    T0=allow, T1=deny,  T2=deny,  T3=deny
#   execution  commit:           T0=allow, T1=allow, T2=deny,  T3=deny
#   execution  propose_promote:  T0=n/a,   T1=n/a,   T2=n/a,   T3=n/a  (ephemeral, no promotion)
#   execution  force_promote:    T0=n/a,   T1=n/a,   T2=n/a,   T3=n/a
#   any        recall:           ALL allow (short-circuited at top of assert_tier_allowed)
#   any        update:           same rules as commit for each layer
# ---------------------------------------------------------------------------

_MATRIX: dict[tuple[str, str], dict[Tier, str]] = {
    # Episodic
    ("episodic", "commit"): {
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "allow",
        Tier.T3: "allow_quarantine",
    },
    ("episodic", "propose_promote"): {
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    ("episodic", "force_promote"): {
        Tier.T0: "allow",
        Tier.T1: "deny",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    ("episodic", "update"): {  # same as commit
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "allow",
        Tier.T3: "allow_quarantine",
    },
    # Semantic
    ("semantic", "commit"): {
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    ("semantic", "propose_promote"): {
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    ("semantic", "force_promote"): {
        Tier.T0: "allow",
        Tier.T1: "deny",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    ("semantic", "update"): {  # same as commit
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    # Procedural — T1/T2 land as candidate; T3 denied entirely
    ("procedural", "commit"): {
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "allow",
        Tier.T3: "deny",
    },
    ("procedural", "propose_promote"): {
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    ("procedural", "force_promote"): {
        Tier.T0: "allow",
        Tier.T1: "deny",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    ("procedural", "update"): {  # same as commit for procedural
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "allow",
        Tier.T3: "deny",
    },
    # Execution — ephemeral, T0/T1 only; no promotion ops
    ("execution", "commit"): {
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
    ("execution", "propose_promote"): {
        Tier.T0: "n/a",
        Tier.T1: "n/a",
        Tier.T2: "n/a",
        Tier.T3: "n/a",
    },
    ("execution", "force_promote"): {
        Tier.T0: "n/a",
        Tier.T1: "n/a",
        Tier.T2: "n/a",
        Tier.T3: "n/a",
    },
    ("execution", "update"): {  # same as commit
        Tier.T0: "allow",
        Tier.T1: "allow",
        Tier.T2: "deny",
        Tier.T3: "deny",
    },
}

# Make the matrix immutable at the module level
# (can't frozendict without a dep; we freeze by convention + test coverage)


# ---------------------------------------------------------------------------
# Enforcement class
# ---------------------------------------------------------------------------


class Enforcement:
    """Per-server-process enforcement state.

    Owns: tier-matrix lookups, rate limiter, path-traversal guard, output cap,
    quarantine marker, and the telemetry record() call.

    Every tool handler MUST call assert_* methods before any storage mutation.
    The record() method MUST be called in a finally block so telemetry is emitted
    even on rejection.

    Pattern mirrors atlas-aci/mcp-server/src/atlas_aci/enforcement.py (FINDING-001).
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        # Sliding deque: entries are monotonic timestamps of recent calls.
        # maxlen is set to rate_limit_per_minute so the deque naturally caps.
        self._call_times: deque[float] = deque(
            maxlen=max(1, config.rate_limit_per_minute)
        )

    # ------------------------------------------------------------------
    # Tier matrix gate (D1)
    # ------------------------------------------------------------------

    def assert_tier_allowed(self, tool: str, layer: str, tier: Tier, op: str) -> None:
        """Check the D1 tier × layer × operation matrix.

        recall is universally allowed (short-circuit at top per D1 notes).
        allow_quarantine sets _MARK_QUARANTINE; commit handler reads it.

        Args:
            tool:  MCP tool name (e.g. 'crystalium.commit') — for error messages.
            layer: Memory layer ('episodic'|'semantic'|'procedural'|'execution').
            tier:  Caller's trust tier.
            op:    Operation ('commit'|'propose_promote'|'force_promote'|'recall'|'update').

        Raises:
            TierViolation: If the matrix rule is 'deny' or 'n/a'.
        """
        # recall is universally allowed (D1 table; read-side trust enforced via D7 downstream)
        if op == "recall":
            return

        row = _MATRIX.get((layer, op))
        if row is None:
            # Unknown (layer, op) combination — deny by default
            raise TierViolation(tool, layer, tier, op)

        rule = row.get(tier, "deny")

        if rule in ("deny", "n/a"):
            raise TierViolation(tool, layer, tier, op)

        if rule == "allow_quarantine":
            # T3 → Episodic: permitted but mark for quarantine state
            _MARK_QUARANTINE.set(True)
            log.info(
                "quarantine_flagged",
                tool=tool,
                layer=layer,
                tier=str(tier),
                op=op,
            )
        # rule == "allow": fall through silently

    # ------------------------------------------------------------------
    # D7 layer ceiling guard (MIN-tier propagation)
    # ------------------------------------------------------------------

    def assert_tier_within_layer_ceiling(
        self, consolidated_tier: Tier, layer: str
    ) -> None:
        """Enforce D7: consolidated.tier must not exceed the layer's admission ceiling.

        Called AFTER the caller provides the consolidated tier (min of all input
        tiers). If any input carries T3 and the target is Semantic, this raises.

        Raises:
            TierCeilingViolation: If consolidated_tier > LAYER_CEILING[layer].
        """
        ceiling = LAYER_CEILING.get(layer)
        if ceiling is None:
            return  # unknown layer; let assert_tier_allowed handle it

        # Tier enum: lower value = higher trust.
        # "exceeds ceiling" means the integer value is greater (less trusted).
        if consolidated_tier > ceiling:
            raise TierCeilingViolation(consolidated_tier, layer)

    # ------------------------------------------------------------------
    # Rate limiter (atlas-aci sliding window pattern, P0-7)
    # ------------------------------------------------------------------

    def assert_rate_limit(self) -> None:
        """Sliding 60-second window rate limiter.

        Default: 200 calls/minute (config.rate_limit_per_minute, MISSION P0-7).

        Raises:
            RateLimitExceeded: If the window is saturated.
        """
        limit = self.config.rate_limit_per_minute
        if limit <= 0:
            return  # rate limiting disabled

        now = time.monotonic()
        cutoff = now - 60.0

        # Trim expired entries from the left
        while self._call_times and self._call_times[0] < cutoff:
            self._call_times.popleft()

        if len(self._call_times) >= limit:
            raise RateLimitExceeded(limit)

        self._call_times.append(now)

    # ------------------------------------------------------------------
    # Path-traversal guard (P0-7, D5, FINDING-001 pattern)
    # ------------------------------------------------------------------

    def assert_no_path_escape(self, target: Path, root: Path) -> None:
        """Resolve *target* strictly and ensure it stays inside *root*.

        Uses Path.resolve(strict=True) which raises OSError if the path does not
        exist — this is intentional: symlinks to non-existent targets are rejected.

        Args:
            target: Path to validate (must already exist on disk).
            root:   Permitted root directory.

        Raises:
            PathEscape: If target resolves outside root.
        """
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(root.resolve())
        except (ValueError, OSError):
            raise PathEscape(target, root)

    # ------------------------------------------------------------------
    # Output cap helper
    # ------------------------------------------------------------------

    def cap_output(self, payload: bytes, max_bytes: int) -> tuple[bytes, bool]:
        """Truncate *payload* to *max_bytes* if necessary.

        Returns:
            (truncated_payload, overflow_flag)
            overflow_flag is True if payload was truncated.
        """
        if len(payload) <= max_bytes:
            return payload, False
        return payload[:max_bytes], True

    # ------------------------------------------------------------------
    # Quarantine state reader
    # ------------------------------------------------------------------

    def quarantine_active(self) -> bool:
        """Return True if the current call context has been flagged for quarantine.

        This ContextVar is set by assert_tier_allowed when rule == 'allow_quarantine'
        (T3 caller → Episodic commit). The commit handler consumes this to stamp
        validation_state='quarantined' on the crystal.

        Reset the ContextVar after the commit handler consumes it to avoid leakage
        into sibling coroutines.
        """
        return _MARK_QUARANTINE.get()

    def reset_quarantine_flag(self) -> None:
        """Reset the quarantine ContextVar after the commit handler has consumed it."""
        _MARK_QUARANTINE.set(False)

    # ------------------------------------------------------------------
    # Telemetry record (called in finally blocks by all tool handlers)
    # ------------------------------------------------------------------

    def record(
        self,
        tool: str,
        layer: str | None,
        tier: Tier | None,
        op: str | None,
        outcome: str,
        latency_ms: float,
        overflow_flag: bool = False,
        error: str | None = None,
    ) -> None:
        """Emit one telemetry record for a tool call.

        Must be called from a try/finally block so it fires even on rejection.
        Delegates to telemetry.record_call() (structlog JSONL + OTel).

        Args:
            tool:         Tool name.
            layer:        Memory layer, if applicable.
            tier:         Caller's trust tier, if known.
            op:           Operation type.
            outcome:      'ok' | 'error' | 'rejected' | 'pending'.
            latency_ms:   Wall-clock duration.
            overflow_flag: True if any output cap was hit.
            error:        Error reason_code if outcome != 'ok'.
        """
        record_call(
            tool=tool,
            layer=layer,
            tier=str(tier) if tier is not None else None,
            op=op,
            result=outcome,
            latency_ms=latency_ms,
            overflow_flag=overflow_flag,
            error=error,
        )

    # ------------------------------------------------------------------
    # Operator warning (D5 — skill_invoke sandbox)
    # ------------------------------------------------------------------

    def warn_skill_invoke(self, skill_id: str) -> None:
        """Emit the operator-warning required by D5 for every skill_invoke call.

        Mirrors atlas-aci's 'SANDBOXING IS THE OPERATOR'S RESPONSIBILITY' warning.
        """
        log.warning(
            "skill_invoke_operator_warning",
            skill_id=skill_id,
            message=(
                "SANDBOXING IS THE OPERATOR'S RESPONSIBILITY. "
                "skill_invoke runs a subprocess inside the crystalium container. "
                "The container IS the sandbox boundary; the harness cannot prevent "
                "a malicious verifier from reading its own container filesystem. "
                "Only invoke skills from trusted (T0/T1) sources."
            ),
        )
