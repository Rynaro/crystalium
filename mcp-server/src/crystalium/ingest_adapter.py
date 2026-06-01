"""Roster ECL handoff → crystal.v1 adapter (W7 Eidolons integration).

CRYSTALIUM is the shared substrate the roster writes handoff artifacts into. This
module is the GENERIC, schema-agnostic mapping (LOCKED W7 decision): ONE function
maps ANY inbound ECL envelope to a canonical crystal.v1 commit payload while
preserving the native artifact verbatim — per-Eidolon quirks live here, never in
the store. No per-artifact-schema validation; the native payload is stashed in the
open-shaped `encoding_context` for later re-validation if a schema appears.

Trust is load-bearing: the source tier rides through to the crystal's trust_tier
(assigned at commit from caller_tier) and MIN-tier propagation is preserved — an
ingested artifact is never laundered to a higher trust tier.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from crystalium.trust import Tier

# kind → target layer. Default is episodic: it is the only layer that accepts the
# lowest-trust (T3) inbound default, and the adapter NEVER auto-promotes to semantic
# (promotion stays gated). Verified/distilled kinds may be added here later; for now
# everything lands episodic (quarantined iff the resolved tier is T3).
_KIND_TO_LAYER: dict[str, str] = {}
_DEFAULT_LAYER = "episodic"

# [PROXY] trust-source table (W7): no canonical tier→provenance.source mapping exists
# in the codebase. This is the single documented source of truth. Flag for review if
# a canonical mapping later lands.
_TIER_TO_SOURCE: dict[Tier, str] = {
    Tier.T0: "human",            # host LLM
    Tier.T1: "verified_agent",   # roster Eidolon
    Tier.T2: "unverified_agent",
    Tier.T3: "unverified_agent",  # tool/environment-origin (also quarantined at commit)
}


# Per-eidolon default trust tier (EIDOLONS.md per-caller table) — used only when the
# envelope's trace.tier is absent or not a T0-T3 token (v1.0 envelopes carry
# trace.tier values like "standard"/"trance", not crystal tiers). Roster Eidolons are
# verified agents (T1); the host LLM is T0; anything unknown / tool-origin is T3
# (most conservative — episodic-quarantine only).
_ROSTER_EIDOLONS: frozenset[str] = frozenset(
    {"atlas", "spectra", "apivr", "apivr-delta", "apivr-Δ", "idg", "forge", "vigil",
     "opus", "crystalium"}
)
_HOST_EIDOLONS: frozenset[str] = frozenset({"host", "host-llm", "cortex"})


def _identity_tier(envelope: dict[str, Any]) -> Tier:
    """The trust tier implied by the envelope's *source identity* (from.eidolon).

    Host -> T0, roster Eidolon -> T1, unknown / tool-origin -> T3 (most conservative).
    """
    eidolon = (envelope.get("from", {}) or {}).get("eidolon", "")
    key = str(eidolon).strip().lower()
    if key in _HOST_EIDOLONS:
        return Tier.T0
    if key in _ROSTER_EIDOLONS:
        return Tier.T1
    return Tier.T3  # unknown / tool-origin: most conservative


def resolve_caller_tier(envelope: dict[str, Any]) -> Tier:
    """Resolve the inbound artifact's trust tier (W7), preserving MIN-trust.

    The source identity (from.eidolon) sets the *ceiling* on trust. A T0-T3 token in
    trace.tier may only ever move the result toward LESS trust (a self-downgrade); it
    can never elevate an artifact above what its source identity earns.

    Battle-test fix (HIGH — trust laundering): the previous implementation returned an
    explicit trace.tier token UNCONDITIONALLY, so an envelope from an unknown / tool
    source carrying trace.tier="T0" was laundered to human provenance + permanent
    protection — the exact opposite of the docstring's "never laundered up" promise.
    We now clamp to MIN-trust = max(declared, identity) over the Tier IntEnum (T0=0
    most-trusted .. T3=3 least-trusted), so the more conservative tier always wins.

    A non-tier token (e.g. v1.0 "standard"/"trance") or absent trace falls back to the
    identity tier. The chokepoint + LAYER_CEILING still enforce the tier at commit."""
    identity = _identity_tier(envelope)
    trace = envelope.get("trace", {}) or {}
    raw = trace.get("tier")
    if raw is not None:
        try:
            declared = Tier.from_str(str(raw))
        except Exception:
            return identity  # non-tier token (e.g. "standard"/"trance") -> identity
        # MIN-trust: honor a self-downgrade, refuse any self-elevation above identity.
        return max(declared, identity)
    return identity


def layer_for_kind(kind: str) -> str:
    """Target layer for an artifact kind. Default episodic; never auto-promotes."""
    return _KIND_TO_LAYER.get(kind, _DEFAULT_LAYER)


def _decode_payload(payload: Any, payload_encoding: str) -> Any:
    """Decode the artifact payload into a JSON object/str for verbatim preservation.

    Accepts a dict (already structured), or a string under utf8/base64/json encoding.
    Best-effort: an undecodable base64/json falls back to the raw string."""
    if isinstance(payload, (dict, list)):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str):
        return payload
    if payload_encoding == "base64":
        try:
            return base64.b64decode(payload).decode("utf-8", errors="replace")
        except Exception:
            return payload
    if payload_encoding == "json":
        try:
            return json.loads(payload)
        except Exception:
            return payload
    return payload  # utf8 / default: keep the string verbatim


def source_for_tier(tier: Tier) -> str:
    """[PROXY] map a trust tier to a crystal.v1 provenance.source enum value."""
    return _TIER_TO_SOURCE.get(tier, "unverified_agent")


def map_envelope_to_crystal(
    envelope: dict[str, Any],
    payload: Any,
    caller_tier: Tier,
    payload_encoding: str = "utf8",
) -> tuple[str, dict[str, Any]]:
    """Map an inbound ECL envelope + artifact payload to (layer, crystal.v1 payload).

    Generic for ANY envelope. The returned payload is id-less (the server assigns
    id/temporal/utility). trust_tier is NOT set here — it is the separate top-level
    crystal field assigned from caller_tier at commit (Provenance is extra='forbid').

    Mapping:
      from.eidolon      -> provenance.author_agent + tag from:<eidolon>
      caller_tier       -> provenance.source ([PROXY] table)
      trace.ts          -> provenance.created_at (else server now)
      thread_id         -> provenance.task_id AND scope.project (groups a handoff chain)
      artifact.sha256   -> content_ref (64-hex; required for episodic)
      artifact.kind     -> layer (table) + summary prefix + tag
      objective         -> summary body (FTS-indexed so downstream recall finds it)
      <native payload>  -> encoding_context.native_artifact (verbatim)
    """
    from_ref = envelope.get("from", {}) or {}
    eidolon = from_ref.get("eidolon") or "unknown"
    artifact = envelope.get("artifact", {}) or {}
    kind = artifact.get("kind") or "artifact"
    trace = envelope.get("trace", {}) or {}
    objective = (envelope.get("objective") or "").strip()
    thread_id = envelope.get("thread_id")
    env_version = str(envelope.get("envelope_version", "")).strip()

    # scope.project groups a handoff chain so downstream stages recall by project.
    project = thread_id or f"eidolon:{eidolon}"

    summary = f"{kind}: {objective}".strip().rstrip(":").strip() if objective else kind
    summary = summary[:4096] or kind

    native = _decode_payload(payload, payload_encoding)

    crystal_payload: dict[str, Any] = {
        "summary": summary,
        "content_ref": artifact.get("sha256"),
        "scope": {
            "project": project,
            "sensitivity_tag": "internal",
        },
        "provenance": {
            "source": source_for_tier(caller_tier),
            "author_agent": eidolon,
            "task_id": thread_id,
            "created_at": trace.get("ts"),
        },
        "tags": [kind, f"from:{eidolon}", f"env:{env_version}"],
        "encoding_context": {
            "native_artifact": native,
            "envelope_version": env_version,
            "artifact_kind": kind,
            "artifact_schema_version": artifact.get("schema_version"),
            "source_thread_id": thread_id,
            "source_message_id": envelope.get("message_id"),
            "source_performative": envelope.get("performative"),
        },
    }
    return layer_for_kind(kind), crystal_payload
