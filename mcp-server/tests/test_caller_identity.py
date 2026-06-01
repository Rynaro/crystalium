"""v1.2.0 — env-var caller identity: unit tests for _extract_caller_identity().

Tests the env→tier resolution matrix:
  - No env vars set         → T2 (D4 backward-compatible default preserved)
  - CRYSTALIUM_CALLER_TIER=T1  → T1 (explicit override, no eidolon)
  - CRYSTALIUM_CALLER_EIDOLON=atlas   → T1 (roster Eidolon)
  - CRYSTALIUM_CALLER_EIDOLON=spectra → T1 (roster Eidolon)
  - CRYSTALIUM_CALLER_EIDOLON=somethingbogus → T3 (unknown → most conservative)
  - MIN-trust: env tier T1 + identity T3 → T3 (less trust wins)
  - Regression: ingest of a T3-origin envelope is unaffected by CRYSTALIUM_CALLER_TIER=T1

The ingest path independently calls resolve_caller_tier(envelope) from
ingest_adapter and never consults _extract_caller_identity(), so process-env
identity cannot launder a T3 artifact upward.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crystalium.ecl import compute_sha256
from crystalium.ingest_adapter import resolve_caller_tier
from crystalium.server import _caller_tier, _extract_caller_identity
from crystalium.trust import Tier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _t3_envelope(payload_str: str) -> dict:
    """Build a T3 (unknown-eidolon, tool-origin) ECL envelope."""
    sha = compute_sha256(payload_str.encode())
    return {
        "envelope_version": "2.0",
        "message_id": "m-t3",
        "thread_id": "thread-t3",
        "parent_id": None,
        "from": {"eidolon": "some-unknown-tool", "version": "0.0.1"},
        "to": {"eidolon": "crystalium", "version": "1.2.0"},
        "performative": "INFORM",
        "objective": "tool data",
        "artifact": {
            "kind": "tool-output",
            "schema_version": "1.0",
            "path": "stdin",
            "sha256": sha,
            "size_bytes": len(payload_str),
        },
        "integrity": {"method": "sha256", "value": sha},
        "trace": {"ts": "2026-06-01T00:00:00Z", "host": "h", "model": "m", "tier": "T3"},
    }


# ---------------------------------------------------------------------------
# Env-var → tier matrix
# ---------------------------------------------------------------------------


def test_unset_env_vars_yield_t2_default(monkeypatch) -> None:
    """D4 backward-compat: no env vars set → T2 default preserved."""
    monkeypatch.delenv("CRYSTALIUM_CALLER_EIDOLON", raising=False)
    monkeypatch.delenv("CRYSTALIUM_CALLER_TIER", raising=False)
    caller = _extract_caller_identity()
    assert _caller_tier(caller) == Tier.T2
    assert caller["tier"] == "T2"


def test_explicit_tier_t1_no_eidolon(monkeypatch) -> None:
    """CRYSTALIUM_CALLER_TIER=T1 with no eidolon → T1."""
    monkeypatch.delenv("CRYSTALIUM_CALLER_EIDOLON", raising=False)
    monkeypatch.setenv("CRYSTALIUM_CALLER_TIER", "T1")
    caller = _extract_caller_identity()
    assert _caller_tier(caller) == Tier.T1


def test_roster_eidolon_atlas_yields_t1(monkeypatch) -> None:
    """CRYSTALIUM_CALLER_EIDOLON=atlas → T1 (roster Eidolon)."""
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "atlas")
    monkeypatch.delenv("CRYSTALIUM_CALLER_TIER", raising=False)
    caller = _extract_caller_identity()
    assert _caller_tier(caller) == Tier.T1


def test_roster_eidolon_spectra_yields_t1(monkeypatch) -> None:
    """CRYSTALIUM_CALLER_EIDOLON=spectra → T1 (roster Eidolon)."""
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "spectra")
    monkeypatch.delenv("CRYSTALIUM_CALLER_TIER", raising=False)
    caller = _extract_caller_identity()
    assert _caller_tier(caller) == Tier.T1


def test_unknown_eidolon_yields_t3(monkeypatch) -> None:
    """CRYSTALIUM_CALLER_EIDOLON=somethingbogus → T3 (unknown, most conservative).

    The _eidolon_identity_tier helper returns T3 for any name not in
    _ROSTER_EIDOLONS or _HOST_EIDOLONS (same mapping as ingest_adapter).
    """
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "somethingbogus")
    monkeypatch.delenv("CRYSTALIUM_CALLER_TIER", raising=False)
    caller = _extract_caller_identity()
    assert _caller_tier(caller) == Tier.T3


def test_min_trust_env_t1_identity_t3_yields_t3(monkeypatch) -> None:
    """MIN-trust rule: CRYSTALIUM_CALLER_TIER=T1 + unknown eidolon (T3) → T3.

    The less-trusted tier (higher int) always wins.
    """
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "somethingbogus")
    monkeypatch.setenv("CRYSTALIUM_CALLER_TIER", "T1")
    caller = _extract_caller_identity()
    assert _caller_tier(caller) == Tier.T3


def test_min_trust_env_t0_roster_eidolon_yields_t1(monkeypatch) -> None:
    """MIN-trust: CRYSTALIUM_CALLER_TIER=T0 + atlas (T1 identity) → T1.

    A self-elevation attempt via tier override is clamped by identity tier.
    """
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "atlas")
    monkeypatch.setenv("CRYSTALIUM_CALLER_TIER", "T0")
    caller = _extract_caller_identity()
    # identity_tier=T1 (atlas is roster), declared_tier=T0 → max(T0, T1) = T1
    assert _caller_tier(caller) == Tier.T1


def test_eidolon_name_recorded_in_caller_dict(monkeypatch) -> None:
    """The eidolon name from env is recorded in the returned caller dict."""
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "forge")
    monkeypatch.delenv("CRYSTALIUM_CALLER_TIER", raising=False)
    caller = _extract_caller_identity()
    assert caller["eidolon"] == "forge"
    assert _caller_tier(caller) == Tier.T1


def test_case_insensitive_eidolon_lookup(monkeypatch) -> None:
    """Eidolon names are normalised to lowercase before set lookup."""
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "ATLAS")
    monkeypatch.delenv("CRYSTALIUM_CALLER_TIER", raising=False)
    caller = _extract_caller_identity()
    assert _caller_tier(caller) == Tier.T1


def test_invalid_tier_string_falls_back_to_identity(monkeypatch) -> None:
    """An unparseable CRYSTALIUM_CALLER_TIER falls back to identity tier (conservative)."""
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "atlas")
    monkeypatch.setenv("CRYSTALIUM_CALLER_TIER", "INVALID")
    caller = _extract_caller_identity()
    # identity_tier=T1 (atlas), declared falls back to identity_tier=T1
    assert _caller_tier(caller) == Tier.T1


# ---------------------------------------------------------------------------
# Regression: ingest path is unaffected by process-env identity
# ---------------------------------------------------------------------------


def test_ingest_t3_envelope_unaffected_by_env_t1(monkeypatch) -> None:
    """Regression: resolve_caller_tier(envelope) is independent of process env.

    Even when CRYSTALIUM_CALLER_TIER=T1 is set in the process environment, a
    T3-origin envelope (unknown eidolon + T3 trace.tier) resolves to T3.
    The ingest path calls resolve_caller_tier(envelope) directly from
    ingest_adapter and never consults _extract_caller_identity() — so no
    elevation leak is possible.
    """
    monkeypatch.setenv("CRYSTALIUM_CALLER_TIER", "T1")
    monkeypatch.setenv("CRYSTALIUM_CALLER_EIDOLON", "atlas")

    payload_str = json.dumps({"data": "tool output"})
    envelope = _t3_envelope(payload_str)

    # ingest_adapter.resolve_caller_tier reads only the envelope, not os.environ
    tier = resolve_caller_tier(envelope)
    assert tier == Tier.T3, (
        f"Expected T3 for unknown-eidolon envelope, got {tier}. "
        "The ingest path must not be elevated by process-env identity."
    )
