# CRYSTALIUM · Wave 8 — Conformance Freeze & Roster Publication (v1.0.0)

**Paste this whole file into a fresh Claude Code session at the `crystalium` repo root.**
**Prereq:** Waves 1–7 merged. This is the 1.0 bar.

Freeze the mechanical guarantees, finish the rationale with honest provenance, prove the canary
bar, and publish to the roster.

## Run it like this
- `/model opus`, `/effort xhigh`. **Shift+Tab → plan mode** first. Six-phase todo list.
- Branch `feat/crystalium-v1.0.0`. Conventional Commits. **Never push** (prepare the release for
  operator review; the operator pushes/tags).
- Approve token: **`APPROVED: BUILD W8`**.

## Invariants (never violate)
Container-first (W1 hook). Chokepoint sacred. ECL v2.0 + EIIS v1.4. `PERSONA.md` ≤ 1000 tokens;
working set ≤ 3,500. Local-first.

## Objective
1. **Conformance suite** (the atlas-aci bar): a passing test for **every** mechanical invariant —
   tier×layer×op matrix (G1–G4), path-escape guard, rate limit, ECL envelope integrity (11 fields +
   SHA-256), **trust-tier MIN propagation**, never-hard-delete (except the W4 right-to-be-forgotten
   op), working-set cap. Green = conformant.
2. **DESIGN-RATIONALE complete**: every augment (W2–W7) traced to its source with
   `[verified]`/`[MEDIUM]`/`[CONTESTED]`/`[UNVERIFIED]` markers; the **neuroscience-as-hypothesis,
   ablation-as-arbiter** stance stated explicitly; **all eight ablation results recorded** in a
   summary table.
3. **Docs & release**: README, CHANGELOG (Keep-a-Changelog), **migration notes** (crystal schema
   evolution v1, config keys added per wave, default-flag flips), per-wave ablation summary.
4. **Canary ≥ 0.80** across all waves' missions; **default-on every augment flag whose A/B won**;
   leave the rest off and documented.
5. **Availability SLO** (Extended-Mind parity): record recall availability % and p95 against the
   target using the W1 panel.
6. **Roster publication**: draft the roster-entry PR to `Rynaro/eidolons` (the nexus); flip status
   from "Pre-roster / Standalone" to roster member in README.
7. Prepare the **v1.0.0 tag** for operator review (do not push).

## Definition of done (the 1.0 gate)
- Conformance suite **green**.
- Canary **≥ 0.80** across all waves.
- **Every defaulted-on flag has a recorded winning A/B**; nulls documented.
- Roster entry drafted; migration notes complete. Run `/prepush` with the full ablation summary.

## Out of scope
New algorithms. W8 hardens, documents, and ships — it does not invent.
