"""Layer adapters — thin facades over storage + enforcement.

Each layer module exposes a single class that funnels ALL writes through
enforcement.assert_tier_allowed() BEFORE any store call, then delegates
to the appropriate storage backends.

  episodic.py   — raw capture, quarantine, bi-temporal update
  semantic.py   — curated facts + conventions; gate-guarded admission
  procedural.py — verifier-sandboxed skill admission
  execution.py  — ephemeral TTL-bound plan state

W3 scope: gate.py, aetheryte/retrieve.py, and dream/* are assembled here too
but live in sibling modules.
"""
