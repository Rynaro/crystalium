# CRYSTALIUM · Wave 7 — Eidolons Integration (v0.8.0)

**Paste this whole file into a fresh Claude Code session at the `crystalium` repo root.**
**Prereq:** Wave 6 merged (security hardened). This is the "Eidolons-ready" payload.

Make CRYSTALIUM the **shared substrate** the roster's handoff artifacts are written into and
recalled from — the long-open "harmonize per-agent memory" thread. Per-agent quirks live in
**adapters**, never in the store.

## Why (integration surface)
- **Handoff schemas to support**: `scout-report.v1` + `findings.v1` (ATLAS), `spec-bundle.v1`
  (SPECTRA), `completion-report.v1` + delta history (APIVR-Δ), `verdict.v1` (FORGE),
  `document-bundle.v1` (IDG), `mission.v1`/`composition.v1`/`run-report.v1` (OPUS), VIGIL artifacts.
- **ECL v2.0** is the realized communication contract (`Rynaro/eidolons-ecl`) — CRYSTALIUM already
  emits envelopes; now it must **ingest** them.
- **EIIS v1.4** (`Rynaro/eidolons-eiis`): `install.manifest.v1`, AGENTS.md frontmatter, install flags.
- **Extended Mind** (Clark & Chalmers 1998): a store that is reliably available + automatically
  endorsed (trust tiers) is *constitutive* of the team's cognition — hence the availability bar.

## Run it like this
- `/model opus`, `/effort xhigh`. **Shift+Tab → plan mode** first. Six-phase todo list.
- Use the ATLAS subagent to map the roster schemas if `.atlas` is wired.
- Branch `feat/crystalium-v0.8.0`. Conventional Commits. **Never push.**
- Approve token: **`APPROVED: BUILD W7`**.

## Invariants (never violate)
Container-first (W1 hook). Chokepoint sacred. **Trust-tier MIN carries across every handoff** — an
upstream artifact recalled downstream keeps its provenance and minimum tier; no laundering to T0/T1.
ECL v2.0 + EIIS v1.4. `agent.md` ≤ 1000 tokens.

## Objective
1. **ECL v2.0 handoff ingestion**: an **adapter layer** that stores/recalls each roster artifact as
   crystals, preserving its native schema and mapping into canonical `crystal.v1`. Quirks in
   adapters only.
2. **Handoff round-trip**: a recalled upstream artifact (e.g. ATLAS `findings.v1`) is retrievable by
   a downstream Eidolon (SPECTRA) with provenance + **MIN trust tier** intact.
3. **EIIS v1.4 finalization**: emit `install.manifest.v1`; AGENTS.md frontmatter with
   `handoffs.upstream`/`downstream` split + full-semver `version`; trim `agent.md` to ≤ 1000 tokens
   if over; all install flags (`--target` default, `--hosts auto`, `--non-interactive`,
   `--manifest-only`, `--version`).
4. **Partial-team + standalone**: `--members`/scope flags; verify a **2-member** deployment
   (e.g. ATLAS + CRYSTALIUM) works with other layers simply unused, **and** CRYSTALIUM works
   standalone with zero teammates.
5. **Host wiring matured**: `hosts/{claude-code,cursor,copilot,opencode}.md` end-to-end.

## Definition of done (gate)
- A **simulated `ATLAS → SPECTRA → APIVR-Δ → IDG` pipeline round-trips every handoff** through
  CRYSTALIUM, trust tiers intact.
- Partial-team (2-member) **and** standalone both pass.
- EIIS v1.4 conformance audit clean (manifest, frontmatter, flags, token budget). Run `/prepush`.

## Out of scope
Conformance-test freeze + roster PR (W8). Do not publish the roster entry in this wave.
