"""W7 C1 — version-tolerant inbound ECL envelope validation.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_ecl_inbound.py -v
"""

from __future__ import annotations

import pytest

from crystalium.ecl import (
    UnsupportedEnvelopeVersion,
    compute_sha256,
    validate_inbound_envelope,
)


def _envelope(version: str) -> dict:
    sha = compute_sha256(b"artifact-bytes")
    return {
        "envelope_version": version,
        "message_id": "m1", "thread_id": "t1", "parent_id": None,
        "from": {"eidolon": "atlas", "version": "1.0.0"},
        "to": {"eidolon": "spectra", "version": "1.0.0"},
        "performative": "INFORM", "objective": "scout report",
        "artifact": {"kind": "scout-report", "schema_version": "1.0",
                     "path": "stdin", "sha256": sha, "size_bytes": 14},
        "integrity": {"method": "sha256", "value": sha},
        "trace": {"ts": "2026-01-01T00:00:00Z", "host": "h", "model": "m", "tier": "T1"},
    }


def test_accepts_v1():
    validate_inbound_envelope(_envelope("1.0"))   # vendored roster fixtures are v1.0


def test_accepts_v2():
    validate_inbound_envelope(_envelope("2.0"))   # CRYSTALIUM's own emitted version


def test_accepts_v1_patch():
    validate_inbound_envelope(_envelope("1.0.3"))


def test_rejects_v3():
    with pytest.raises(UnsupportedEnvelopeVersion):
        validate_inbound_envelope(_envelope("3.0"))


def test_rejects_missing_version():
    env = _envelope("2.0"); del env["envelope_version"]
    with pytest.raises(UnsupportedEnvelopeVersion):
        validate_inbound_envelope(env)


def test_rejects_integrity_mismatch():
    env = _envelope("2.0"); env["integrity"]["value"] = "0" * 64
    with pytest.raises(ValueError):
        validate_inbound_envelope(env)


def test_rejects_missing_artifact_field():
    env = _envelope("1.0"); del env["artifact"]["sha256"]
    with pytest.raises(ValueError):
        validate_inbound_envelope(env)
