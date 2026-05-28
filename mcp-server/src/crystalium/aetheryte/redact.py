"""Redactor — regex pre-pass + LLM judge between retrieval and LLM-facing response.

Implements P0-12 (MISSION.md §88-92):
  - regex pre-pass for PII + secrets at retrieval time
  - re-applied at every cross-agent handoff (D7)
  - LLM judge stub for sensitivity-tagged content (v0.1: config.redaction_llm_enabled)

Replacement sentinel: «REDACTED:<rule_id>»

Patterns frozen at module level. rule_id matches the pattern name so audit logs
can identify which rule fired.

Source: MISSION.md §Security & privacy surface, spec.yaml §P0-12.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from crystalium.config import Config
from crystalium.trust import Tier

log = structlog.get_logger("crystalium.aetheryte.redact")

# ---------------------------------------------------------------------------
# Redaction patterns — module-level frozen tuple
#
# Each entry: (rule_id: str, compiled_pattern: re.Pattern)
# Replacement: «REDACTED:<rule_id>»
#
# Coverage:
#   SSN          — 9-digit US SSN (dashes optional, not all-zero segments)
#   AWS_KEY      — AWS access key IDs (AKIA / ASIA / AROA / ABIA / ACCA prefixes)
#   GITHUB_TOKEN — GitHub personal access tokens (ghp_, gho_, github_pat_ prefixes)
#   JWT          — three base64url segments separated by dots (generic JWT shape)
#   PRIVATE_KEY  — PEM private key header/footer markers
#   PASSWORD     — password/passwd/pass followed by = or : and a non-whitespace value
# ---------------------------------------------------------------------------

_RAW_PATTERNS: list[tuple[str, str]] = [
    (
        "SSN",
        r"\b(?!000|666|9\d{2})\d{3}[-\s]?(?!00)\d{2}[-\s]?(?!0{4})\d{4}\b",
    ),
    (
        "AWS_KEY",
        r"\b(AKIA|ASIA|AROA|ABIA|ACCA)[0-9A-Z]{16}\b",
    ),
    (
        "GITHUB_TOKEN",
        r"\b(ghp_[A-Za-z0-9]{36,}|gho_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{36,})\b",
    ),
    (
        "JWT",
        # Three base64url segments. Minimum 20 chars each to avoid false positives.
        r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b",
    ),
    (
        "PRIVATE_KEY",
        # Matches "-----BEGIN PRIVATE KEY-----" and "-----BEGIN RSA PRIVATE KEY-----" etc.
        # The prefix qualifier (RSA, EC, etc.) is optional.
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----",
    ),
    (
        "PASSWORD",
        r"(?i)\b(?:password|passwd|pass)\s*[:=]\s*\S+",
    ),
]

REDACTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (rule_id, re.compile(pattern))
    for rule_id, pattern in _RAW_PATTERNS
)

# ---------------------------------------------------------------------------
# Sensitivity tag constants
# ---------------------------------------------------------------------------

_SENSITIVITY_NONE = "none"
_SENSITIVITY_PII = "pii"
_SENSITIVITY_SECRETS = "secrets"

# Rules that only fire for pii/secrets sensitivity tags (not for "none")
_PII_RULES: frozenset[str] = frozenset({"SSN"})
_SECRETS_RULES: frozenset[str] = frozenset({"AWS_KEY", "GITHUB_TOKEN", "JWT", "PRIVATE_KEY", "PASSWORD"})
# All rules fire for any non-"none" sensitivity tag
_ALL_RULES: frozenset[str] = _PII_RULES | _SECRETS_RULES


def _sentinel(rule_id: str) -> str:
    return f"«REDACTED:{rule_id}»"


# ---------------------------------------------------------------------------
# Redactor class
# ---------------------------------------------------------------------------


class Redactor:
    """Applies regex pre-pass redaction + optional LLM judge to text payloads.

    Args:
        config: Server-wide config (redaction_llm_enabled, sensitivity settings).
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    def redact(self, text: str, sensitivity_tag: str) -> str:
        """Apply redaction to *text* based on *sensitivity_tag*.

        sensitivity_tag == "none":
            Only PII regex patterns fire (SSN). Secrets patterns skipped.
        sensitivity_tag in ("pii", "secrets"):
            All patterns fire (PII + secrets).
        Any other tag:
            All patterns fire (conservative default).

        If config.redaction_llm_enabled (stub in v0.1), an LLM judge pass would
        follow the regex pass for sensitivity_tag != "none".

        Args:
            text:            Raw text to redact.
            sensitivity_tag: Scope.sensitivity_tag from the crystal.

        Returns:
            Redacted text with matched spans replaced by «REDACTED:<rule_id>».
        """
        tag = (sensitivity_tag or "none").lower()

        if tag == _SENSITIVITY_NONE:
            active_rules = _PII_RULES
        else:
            active_rules = _ALL_RULES

        result = text
        redacted_count = 0
        for rule_id, pattern in REDACTION_PATTERNS:
            if rule_id not in active_rules:
                continue
            new_result = pattern.sub(_sentinel(rule_id), result)
            if new_result != result:
                redacted_count += 1
            result = new_result

        if redacted_count:
            log.info(
                "redaction_applied",
                rules_fired=redacted_count,
                sensitivity_tag=tag,
            )

        # v0.1 LLM judge stub — config flag reserved for future activation
        # if getattr(self.config, "redaction_llm_enabled", False) and tag != _SENSITIVITY_NONE:
        #     result = self._llm_judge(result, sensitivity_tag)  # NotImplementedError("v0.2")

        return result

    def redact_for_handoff(
        self,
        payload: dict[str, Any],
        from_tier: Tier,
        to_tier: Tier,
    ) -> dict[str, Any]:
        """Re-redact *payload* at a cross-agent handoff (D7 re-redact rule).

        If to_tier > from_tier (receiver is less trusted), apply max-redaction
        profile to all text fields in the payload. This prevents trust laundering
        via forwarding.

        The payload is a shallow copy; nested dicts and lists are mutated in-place.

        Args:
            payload:    Dict representation of a crystal or recall result.
            from_tier:  Trust tier of the sender agent.
            to_tier:    Trust tier of the receiving agent.

        Returns:
            Redacted payload dict (copy if redaction was applied, original if not).
        """
        if to_tier <= from_tier:
            # Receiver is equally or more trusted — no extra redaction needed.
            return payload

        # Receiver is less trusted: apply max-redaction (all rules) to text fields.
        log.info(
            "handoff_redaction",
            from_tier=str(from_tier),
            to_tier=str(to_tier),
        )
        return self._deep_redact(payload, sensitivity_tag="secrets")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _deep_redact(self, obj: Any, sensitivity_tag: str) -> Any:
        """Recursively apply redact() to all string values in a dict/list structure."""
        if isinstance(obj, str):
            return self.redact(obj, sensitivity_tag)
        if isinstance(obj, dict):
            return {k: self._deep_redact(v, sensitivity_tag) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._deep_redact(item, sensitivity_tag) for item in obj]
        return obj
