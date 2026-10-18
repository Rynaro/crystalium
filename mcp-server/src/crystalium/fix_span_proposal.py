"""Optional deterministic ECL repair proposals: inert data, never a second write."""

from __future__ import annotations

from typing import Any

from crystalium.enforcement import Enforcement
from crystalium.trust import Tier


def propose_fix_span(
    native: Any,
    ingested_id: str | None,
    scope: dict[str, Any],
    caller_tier: Tier,
    enforcement: Enforcement,
    relational: Any,
) -> dict[str, Any] | None:
    """Validate explicit candidate and scoped sources only under both opt-in flags.

    The current ingest ID is a mandatory source so later recommits also re-resolve
    its authoritative tier. A forged displayed tier is ignored by write validation.
    """
    config = enforcement.config
    if not config.fix_span_enabled or not config.fix_span_extractor_enabled:
        return None
    outcome, reason = "skipped", "no_candidate"
    effective = caller_tier
    try:
        if not isinstance(native, dict) or "fix_span_candidate" not in native:
            return None
        candidate = native["fix_span_candidate"]
        if (
            not isinstance(candidate, dict)
            or set(candidate) != {"fix_span", "source_ids"}
            or not ingested_id
        ):
            reason = "malformed"
            return None
        ids = candidate["source_ids"]
        # Reject malformed/empty source lists before adding the current ingest.
        effective = enforcement.resolve_fix_span_sources(ids, scope, caller_tier, relational)
        source_ids = list(dict.fromkeys([*ids, ingested_id]))
        payload, effective = enforcement.validate_structured_payload(
            {
                "kind": "fix_span",
                "fix_span": candidate["fix_span"],
                "scope": scope,
                "encoding_context": {
                    "fix_span_sources": source_ids,
                    "extraction_method": "explicit_ecl_candidate",
                },
            },
            effective,
            "episodic",
            relational,
        )
        outcome = "downgraded" if effective > caller_tier else "accepted"
        reason = "source_trust" if outcome == "downgraded" else "valid"
        return {
            **payload,
            "layer": "episodic",
            "source_ids": source_ids,
            "extraction_method": "explicit_ecl_candidate",
            "trust_tier": str(effective),
            "validation_state": "quarantined" if effective == Tier.T3 else "unverified",
        }
    except Exception as exc:
        reason = getattr(exc, "reason_code", "unverified_sources")
        return None
    finally:
        enforcement.record(
            "fix_span_proposal", "episodic", effective, "propose", outcome, 0.0, error=reason
        )
