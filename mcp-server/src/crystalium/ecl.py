"""ECL v2.0 envelope helper — G7 anchor.

Emits a sibling ecl-envelope.<message_id>.json sidecar for every
crystalium.* MCP tool result that produces a payload artefact.

Design decisions traced to .forge/reasoning-report.md:
  D4: Declare ECL_VERSION=2.0 in v0.1; every tool result emits an envelope.
  D4: from.eidolon='crystalium', to inferred from caller-identity header or 'unknown'.
  D4: message_id uses UUID4 (UUID7 preferred but stdlib falls back, see [UNVERIFIED]).
  D4: integrity.method='sha256'; integrity.value == artifact.sha256 == hashlib.sha256(payload).

ECL v2.0 required fields (eidolons-ecl/schemas/envelope.v2.json §required[]):
  envelope_version, message_id, thread_id, parent_id,
  from{eidolon,version}, to{eidolon,version},
  performative, objective, artifact{kind,schema_version,path,sha256,size_bytes},
  integrity{method,value},
  trace{ts,host,model,tier}
"""

from __future__ import annotations

import hashlib
import json
import socket
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crystalium import __version__

# ---------------------------------------------------------------------------
# Datetime helper
# ---------------------------------------------------------------------------


def now_rfc3339() -> str:
    """Return the current UTC time as an RFC 3339 timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# SHA-256 helper
# ---------------------------------------------------------------------------


def compute_sha256(payload_bytes: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of *payload_bytes*.

    Uses hashlib.sha256 (stdlib — no external dep required, per D4).
    """
    return hashlib.sha256(payload_bytes).hexdigest()


# ---------------------------------------------------------------------------
# Message-ID generator
# ---------------------------------------------------------------------------


def new_message_id() -> str:
    """Generate a unique message ID for an ECL envelope.

    [UNVERIFIED] UUID7 (time-ordered) is preferred per ECL §message_id guidance
    and FORGE D4, but requires an external library (uuid7 / uuid_extensions) not
    yet in Python 3.11 stdlib. UUID4 is used here for compatibility.
    Revisit when uuid7 is available in the stdlib or as a pinned dep.
    """
    try:
        import uuid_extensions  # type: ignore[import-untyped]

        return str(uuid_extensions.uuid7())
    except ImportError:
        pass
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# EclEnvelope dataclass
# ---------------------------------------------------------------------------


@dataclass
class _AgentRef:
    eidolon: str
    version: str


@dataclass
class _Artifact:
    kind: str
    schema_version: str
    path: str
    sha256: str
    size_bytes: int


@dataclass
class _Integrity:
    method: str
    value: str


@dataclass
class _Trace:
    ts: str
    host: str
    model: str
    tier: str


@dataclass
class EclEnvelope:
    """ECL v2.0 envelope.

    All 11 required fields are populated by the constructor.
    Field order matches the envelope.v2.json required[] block.

    Args:
        message_id:    Globally unique ID (UUID4/7).
        thread_id:     UUID grouping all envelopes of one logical mission.
        parent_id:     message_id of the immediate causal predecessor, or None.
        from_eidolon:  Sending agent slug (always 'crystalium').
        from_version:  Sending agent semver (always __version__).
        to_eidolon:    Receiving agent slug (from caller identity or 'unknown').
        to_version:    Receiving agent semver (from caller identity or 'n/a').
        performative:  ECL performative (INFORM | PROPOSE | REFUSE | ACKNOWLEDGE).
        objective:     Single-sentence description of the message goal (≤240 chars).
        artifact:      Payload descriptor dict with required sub-fields.
        trace:         Emission-time trace dict.
    """

    message_id: str
    thread_id: str
    parent_id: str | None
    from_eidolon: str
    from_version: str
    to_eidolon: str
    to_version: str
    performative: str
    objective: str
    artifact: dict[str, Any]
    trace: dict[str, Any]
    # Computed from artifact.sha256 at to_dict() time
    _integrity_value: str = field(init=False, repr=False, default="")

    def __post_init__(self) -> None:
        # Derive integrity.value from artifact.sha256 (must match per G7)
        self._integrity_value = self.artifact.get("sha256", "")
        # Clamp objective to 240 chars per ECL spec
        if len(self.objective) > 240:
            self.objective = self.objective[:237] + "..."

    def to_dict(self) -> dict[str, Any]:
        """Return a dict with all 11 required ECL v2.0 fields populated.

        Field names mirror envelope.v2.json property names exactly.
        """
        return {
            "envelope_version": "2.0",
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "parent_id": self.parent_id,
            "from": {
                "eidolon": self.from_eidolon,
                "version": self.from_version,
            },
            "to": {
                "eidolon": self.to_eidolon,
                "version": self.to_version,
            },
            "performative": self.performative,
            "objective": self.objective,
            "artifact": self.artifact,
            "integrity": {
                "method": "sha256",
                "value": self._integrity_value,
            },
            "trace": self.trace,
        }


# ---------------------------------------------------------------------------
# Sidecar emit
# ---------------------------------------------------------------------------


def emit_sidecar(
    payload: bytes,
    run_dir: Path,
    envelope: EclEnvelope,
) -> Path:
    """Write an ECL envelope sidecar next to the payload artefact.

    Naming: <run_dir>/ecl-envelope.<message_id>.json

    Args:
        payload:   Raw payload bytes (used to verify integrity before write).
        run_dir:   Directory where the sidecar is written.
        envelope:  Populated EclEnvelope instance.

    Returns:
        Path to the written sidecar file.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = run_dir / f"ecl-envelope.{envelope.message_id}.json"
    sidecar_data = envelope.to_dict()
    sidecar_path.write_text(json.dumps(sidecar_data, indent=2), encoding="utf-8")
    return sidecar_path


# ---------------------------------------------------------------------------
# High-level factory
# ---------------------------------------------------------------------------


def build_for_tool_result(
    tool_name: str,
    payload: bytes,
    artifact_kind: str,
    caller_identity: dict[str, Any] | None = None,
    performative: str = "INFORM",
    thread_id: str | None = None,
    parent_id: str | None = None,
) -> EclEnvelope:
    """Build an ECL envelope for a crystalium.* tool result.

    Populates all 11 required fields per D4:
      - from.eidolon = 'crystalium', from.version = __version__
      - to.eidolon = caller_identity.get('eidolon', 'unknown')   (OQ-3 note: 'unknown' in v0.1)
      - to.version = caller_identity.get('version', 'n/a')
      - artifact.sha256 == integrity.value == compute_sha256(payload)

    Args:
        tool_name:        MCP tool name (e.g. 'crystalium.recall').
        payload:          Serialized result bytes — drives sha256 + size_bytes.
        artifact_kind:    ECL artifact kind slug (e.g. 'recall-result').
        caller_identity:  Dict extracted from MCP headers / context.
                          Expected keys: 'eidolon', 'version', 'tier'.
        performative:     ECL performative string. Default: 'INFORM'.
        thread_id:        Existing thread UUID. Generated if None.
        parent_id:        Parent message UUID or None for thread-root envelopes.

    Returns:
        Populated EclEnvelope ready for to_dict() and emit_sidecar().
    """
    ci = caller_identity or {}
    sha = compute_sha256(payload)
    msg_id = new_message_id()
    t_id = thread_id or str(uuid.uuid4())

    # Objective: auto-generated per OQ-10 (simple; revisit if multi-Eidolon hand-offs
    # require richer objective text in v0.2)
    objective = f"{tool_name} result"
    if len(objective) > 240:
        objective = objective[:237] + "..."

    artifact: dict[str, Any] = {
        "kind": artifact_kind,
        "schema_version": "1.0",
        "path": "stdin",
        "sha256": sha,
        "size_bytes": len(payload),
    }

    trace: dict[str, Any] = {
        "ts": now_rfc3339(),
        "host": socket.gethostname(),
        "model": "crystalium",
        "tier": ci.get("tier", "standard"),
    }

    return EclEnvelope(
        message_id=msg_id,
        thread_id=t_id,
        parent_id=parent_id,
        from_eidolon="crystalium",
        from_version=__version__,
        to_eidolon=ci.get("eidolon", "unknown"),
        to_version=ci.get("version", "n/a"),
        performative=performative,
        objective=objective,
        artifact=artifact,
        trace=trace,
    )


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "envelope_version",
        "message_id",
        "thread_id",
        "parent_id",
        "from",
        "to",
        "performative",
        "objective",
        "artifact",
        "integrity",
        "trace",
    }
)

_ARTIFACT_REQUIRED: frozenset[str] = frozenset(
    {"kind", "schema_version", "path", "sha256", "size_bytes"}
)


def validate_envelope(envelope: dict[str, Any]) -> None:
    """Validate that *envelope* has all 11 required ECL v2.0 fields.

    Also checks artifact sub-fields and that integrity.value == artifact.sha256.

    Raises:
        ValueError: with a descriptive message listing missing or mismatched fields.
    """
    missing = _REQUIRED_FIELDS - set(envelope.keys())
    if missing:
        raise ValueError(f"ECL envelope missing required fields: {sorted(missing)}")

    # Check artifact sub-fields
    artifact = envelope.get("artifact", {})
    missing_artifact = _ARTIFACT_REQUIRED - set(artifact.keys())
    if missing_artifact:
        raise ValueError(f"ECL envelope artifact missing fields: {sorted(missing_artifact)}")

    # Check integrity structure
    integrity = envelope.get("integrity", {})
    if "method" not in integrity or "value" not in integrity:
        raise ValueError("ECL envelope integrity must have 'method' and 'value' fields.")

    # Check from/to structure
    for role in ("from", "to"):
        ref = envelope.get(role, {})
        if "eidolon" not in ref or "version" not in ref:
            raise ValueError(
                f"ECL envelope '{role}' must have 'eidolon' and 'version' fields."
            )

    # Integrity value must match artifact.sha256 (G7)
    if integrity.get("value") != artifact.get("sha256"):
        raise ValueError(
            "ECL envelope integrity.value does not match artifact.sha256 (G7 violation)."
        )
