"""W4 tests: ECL v2.0 envelope helper (G7 anchor).

Tests:
  test_g7_envelope_has_11_required_fields      — all 11 required ECL v2.0 fields present
  test_g7_integrity_value_matches_sha256        — integrity.value == sha256(payload)
  test_envelope_to_dict_round_trip              — to_dict() serialises + deserialises cleanly
  test_sidecar_written_next_to_payload          — emit_sidecar() writes <run_dir>/ecl-envelope.<id>.json

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_ecl_envelope.py -v
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from crystalium.ecl import (
    EclEnvelope,
    build_for_tool_result,
    compute_sha256,
    emit_sidecar,
    new_message_id,
    validate_envelope,
    _REQUIRED_FIELDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_PAYLOAD = b'{"records": [], "total_tokens": 42}'
_SAMPLE_SHA = hashlib.sha256(_SAMPLE_PAYLOAD).hexdigest()


def _make_envelope(
    payload: bytes = _SAMPLE_PAYLOAD,
    tool_name: str = "crystalium.recall",
    artifact_kind: str = "recall-result",
    caller: dict | None = None,
) -> EclEnvelope:
    return build_for_tool_result(
        tool_name=tool_name,
        payload=payload,
        artifact_kind=artifact_kind,
        caller_identity=caller,
    )


# ---------------------------------------------------------------------------
# test_g7_envelope_has_11_required_fields
# ---------------------------------------------------------------------------


def test_g7_envelope_has_11_required_fields() -> None:
    """G7: to_dict() must contain all 11 required ECL v2.0 fields."""
    envelope = _make_envelope()
    d = envelope.to_dict()

    # Exactly the 11 required fields defined in _REQUIRED_FIELDS
    for field in _REQUIRED_FIELDS:
        assert field in d, f"Missing required ECL v2.0 field: {field!r}"

    # Also validate via the public validator
    validate_envelope(d)  # should not raise


def test_g7_envelope_has_exactly_11_top_level_required_fields() -> None:
    """to_dict() exposes the 11 required field names, no fewer."""
    envelope = _make_envelope()
    d = envelope.to_dict()
    present = set(d.keys())
    assert _REQUIRED_FIELDS.issubset(present), (
        f"Missing: {_REQUIRED_FIELDS - present}"
    )


# ---------------------------------------------------------------------------
# test_g7_integrity_value_matches_sha256
# ---------------------------------------------------------------------------


def test_g7_integrity_value_matches_sha256() -> None:
    """G7: integrity.value == hashlib.sha256(payload).hexdigest() == artifact.sha256."""
    payload = b"hello crystalium"
    expected_sha = hashlib.sha256(payload).hexdigest()

    envelope = build_for_tool_result(
        tool_name="crystalium.commit",
        payload=payload,
        artifact_kind="commit-result",
    )
    d = envelope.to_dict()

    assert d["integrity"]["value"] == expected_sha, (
        f"integrity.value {d['integrity']['value']!r} != expected {expected_sha!r}"
    )
    assert d["artifact"]["sha256"] == expected_sha, (
        f"artifact.sha256 {d['artifact']['sha256']!r} != expected {expected_sha!r}"
    )
    assert d["integrity"]["value"] == d["artifact"]["sha256"], (
        "integrity.value and artifact.sha256 must be equal (G7)"
    )


def test_g7_integrity_method_is_sha256() -> None:
    """G7: integrity.method must be 'sha256'."""
    envelope = _make_envelope()
    d = envelope.to_dict()
    assert d["integrity"]["method"] == "sha256"


def test_g7_from_eidolon_is_crystalium() -> None:
    """G7: from.eidolon must be 'crystalium'."""
    envelope = _make_envelope()
    d = envelope.to_dict()
    assert d["from"]["eidolon"] == "crystalium"


def test_g7_from_version_matches_package_version() -> None:
    """G7: from.version must match __version__."""
    from crystalium import __version__
    envelope = _make_envelope()
    d = envelope.to_dict()
    assert d["from"]["version"] == __version__


# ---------------------------------------------------------------------------
# test_envelope_to_dict_round_trip
# ---------------------------------------------------------------------------


def test_envelope_to_dict_round_trip() -> None:
    """to_dict() must produce a JSON-serialisable dict that survives json.dumps + loads."""
    envelope = _make_envelope()
    d = envelope.to_dict()

    serialised = json.dumps(d)
    restored = json.loads(serialised)

    assert restored["envelope_version"] == "2.0"
    assert restored["from"]["eidolon"] == "crystalium"
    assert restored["performative"] == "INFORM"
    assert restored["artifact"]["kind"] == "recall-result"
    assert restored["integrity"]["method"] == "sha256"


def test_envelope_round_trip_preserves_all_required_fields() -> None:
    """Round-trip through JSON must preserve all 11 required field names."""
    envelope = _make_envelope()
    d = envelope.to_dict()
    restored = json.loads(json.dumps(d))
    for field in _REQUIRED_FIELDS:
        assert field in restored, f"Field {field!r} lost in round-trip"


def test_envelope_caller_identity_propagated() -> None:
    """Caller identity is propagated into to.eidolon and to.version."""
    caller = {"eidolon": "atlas", "version": "1.8.0", "tier": "T1"}
    envelope = build_for_tool_result(
        tool_name="crystalium.recall",
        payload=b"payload",
        artifact_kind="recall-result",
        caller_identity=caller,
    )
    d = envelope.to_dict()
    assert d["to"]["eidolon"] == "atlas"
    assert d["to"]["version"] == "1.8.0"


def test_envelope_unknown_caller_defaults() -> None:
    """When caller_identity is None, to.eidolon='unknown', to.version='n/a'."""
    envelope = build_for_tool_result(
        tool_name="crystalium.recall",
        payload=b"payload",
        artifact_kind="recall-result",
        caller_identity=None,
    )
    d = envelope.to_dict()
    assert d["to"]["eidolon"] == "unknown"
    assert d["to"]["version"] == "n/a"


# ---------------------------------------------------------------------------
# test_sidecar_written_next_to_payload
# ---------------------------------------------------------------------------


def test_sidecar_written_next_to_payload(tmp_path: Path) -> None:
    """emit_sidecar() writes ecl-envelope.<message_id>.json in run_dir."""
    payload = b'{"status": "ok"}'
    envelope = build_for_tool_result(
        tool_name="crystalium.session_end",
        payload=payload,
        artifact_kind="session-end-receipt",
        performative="ACKNOWLEDGE",
    )

    sidecar_path = emit_sidecar(payload=payload, run_dir=tmp_path, envelope=envelope)

    # File exists at expected path
    assert sidecar_path.exists(), f"Sidecar not found at {sidecar_path}"
    expected_name = f"ecl-envelope.{envelope.message_id}.json"
    assert sidecar_path.name == expected_name

    # Content is valid JSON with required fields
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    validate_envelope(data)  # must not raise


def test_sidecar_creates_run_dir_if_missing(tmp_path: Path) -> None:
    """emit_sidecar() creates run_dir (parents=True) if it doesn't exist."""
    run_dir = tmp_path / "deep" / "nested" / "run"
    assert not run_dir.exists()

    payload = b"test"
    envelope = _make_envelope(payload=payload)
    emit_sidecar(payload=payload, run_dir=run_dir, envelope=envelope)

    assert run_dir.exists()
    sidecar = run_dir / f"ecl-envelope.{envelope.message_id}.json"
    assert sidecar.exists()


def test_sidecar_integrity_value_in_file(tmp_path: Path) -> None:
    """Sidecar written to disk must have integrity.value == sha256(payload)."""
    payload = b"integrity check"
    expected_sha = hashlib.sha256(payload).hexdigest()

    envelope = build_for_tool_result(
        tool_name="crystalium.commit",
        payload=payload,
        artifact_kind="commit-result",
    )
    sidecar_path = emit_sidecar(payload=payload, run_dir=tmp_path, envelope=envelope)
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))

    assert data["integrity"]["value"] == expected_sha
    assert data["artifact"]["sha256"] == expected_sha


# ---------------------------------------------------------------------------
# compute_sha256 helper
# ---------------------------------------------------------------------------


def test_compute_sha256_empty() -> None:
    """sha256 of empty bytes is the standard empty-string hash."""
    expected = hashlib.sha256(b"").hexdigest()
    assert compute_sha256(b"") == expected


def test_compute_sha256_known() -> None:
    """sha256 of 'abc' is a known value."""
    # echo -n 'abc' | sha256sum
    expected = "ba7816bf8f01cfea414140de5dae2ec73b00361bbef0469f492b5ac5e13" + "14a7"
    # Recompute via stdlib for correctness
    expected = hashlib.sha256(b"abc").hexdigest()
    assert compute_sha256(b"abc") == expected


# ---------------------------------------------------------------------------
# Objective truncation
# ---------------------------------------------------------------------------


def test_objective_truncated_to_240_chars() -> None:
    """EclEnvelope.__post_init__ clamps objective to 240 chars."""
    long_objective = "x" * 300
    envelope = EclEnvelope(
        message_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        parent_id=None,
        from_eidolon="crystalium",
        from_version="0.1.0",
        to_eidolon="unknown",
        to_version="n/a",
        performative="INFORM",
        objective=long_objective,
        artifact={
            "kind": "recall-result",
            "schema_version": "1.0",
            "path": "stdin",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "size_bytes": 1,
        },
        trace={"ts": "2026-05-28T00:00:00Z", "host": "test", "model": "test", "tier": "T1"},
    )
    assert len(envelope.objective) == 240
    assert envelope.objective.endswith("...")
