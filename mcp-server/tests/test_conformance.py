"""W8 C4 — the conformance gate registry: green == mechanically conformant.

Asserts every named G-gate (G1-G8) + the cross-cutting mechanical invariants are
represented by a present, non-empty gate file. A silently removed/renamed gate
file fails HERE with the named gate id — so 'pytest -m conformance' cannot quietly
lose coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import CONFORMANCE_FILES

_TESTS_DIR = Path(__file__).resolve().parent

# Every mechanical invariant the 1.0 bar requires → the gate file that proves it.
_GATE_REGISTRY = {
    "G1 tier×layer×op (commit)": "test_enforcement.py",
    "G2 procedural candidate": "test_enforcement.py",
    "G3 skill sandbox": "test_skill_invoke.py",
    "G4 MIN-tier propagation": "test_trust_propagation.py",
    "G5 promotion gate": "test_promotion_gate.py",
    "G6 working-set cap": "test_composer.py",
    "G7 ECL 11-field + SHA-256": "test_ecl_envelope.py",
    "G8 dream dedup": "test_dream_scheduler.py",
    "path-escape guard": "test_enforcement.py",
    "rate limit": "test_enforcement.py",
    "never-hard-delete (P0-5)": "test_bitemporal.py",
    "RTBF exception": "test_rtbf.py",
}


@pytest.mark.conformance
@pytest.mark.parametrize("gate,filename", sorted(_GATE_REGISTRY.items()))
def test_every_gate_has_a_present_file(gate: str, filename: str) -> None:
    path = _TESTS_DIR / filename
    assert path.exists(), f"conformance gate {gate!r} -> missing file {filename}"
    assert filename in CONFORMANCE_FILES, (
        f"gate {gate!r} file {filename} not in the conftest CONFORMANCE_FILES registry"
    )
    assert "def test_" in path.read_text(), f"{filename} has no tests"


@pytest.mark.conformance
def test_registry_files_all_exist() -> None:
    for filename in CONFORMANCE_FILES:
        assert (_TESTS_DIR / filename).exists(), f"registry file missing: {filename}"


@pytest.mark.conformance
def test_all_eight_g_gates_present() -> None:
    gates = {g.split()[0] for g in _GATE_REGISTRY if g.startswith("G")}
    assert gates == {f"G{i}" for i in range(1, 9)}, f"missing G-gates: {gates}"
