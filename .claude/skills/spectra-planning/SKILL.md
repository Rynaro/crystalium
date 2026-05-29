---
name: spectra-planning
description: "Decision-ready specifications via the SPECTRA cycle. Use for complex features (complexity ≥7/12), multi-component/service changes, ambiguous requirements needing structured decomposition, or any task where 'just start coding' would require significant rework. Produces a dual-format spec (Markdown + YAML/JSON), never code."
methodology: SPECTRA
methodology_version: "4.2"
---

# SPECTRA — Planning Skill

Use this skill when the user needs a specification before implementation.

## Activation signals

- Complex features (complexity ≥7/12 on the SPECTRA rubric)
- Multi-component or multi-service changes
- Ambiguous requirements needing structured decomposition
- Specification refinement needing deliberate process
- Any task where "just start coding" would likely require significant rework

## The cycle

CLARIFY → Scope → Pattern → Explore → Construct → Test → Refine → Assemble

READ-ONLY during all phases — produce specifications, never code.

## Progressive disclosure

This SKILL.md is the routing card. Escalate on demand:

- `.eidolons/spectra/SPEC.md` — full cognitive architecture
- `.eidolons/spectra/scoring.md` — scoring rubrics and matrices
- `.eidolons/spectra/templates.md` — output formats per phase
- `.eidolons/spectra/templates/planning-artifact.md` — spec artifact template

## Hard constraints (P0)

1. READ-ONLY. No code, no file edits, no mutations. Plans only.
2. Dual-format output: human-readable Markdown + agent-executable YAML/JSON.
3. Never skip CLARIFY. Parse WHO / WHAT / WHY / CONSTRAINTS before planning.
4. Complexity ≥7/12 → extended thinking (2× token budget).
5. Confidence <85% at Assemble → return to Refine (max 3 cycles).
6. Output is a specification. Execution belongs to a separate agent.
7. Every output path lives under `.spectra/` — plans at `.spectra/plans/`, state at `.spectra/state/`, logs at `.spectra/logs/`. Never scatter files outside `.spectra/`.

## On activation

Load `.spectra/setup/spectra-conventions.md` if it exists. When present, its project vocabulary (real module names, test framework, deploy targets) supersedes SPECTRA's generic placeholders. When absent, continue with generic defaults — the conventions file is optional enrichment, not a prerequisite.

## ECL emission (Assemble exit gate)

If `ECL_VERSION` is present in the install root, emit `<payload>.envelope.json` co-located with the Markdown spec at the end of the Assemble phase. The envelope MUST:

1. Validate against `schemas/ecl-envelope.v1.json`.
2. Have `integrity.method: sha256` and `integrity.value` equal to the sha256 hex digest of the Markdown payload bytes at emit time (same value as `artifact.sha256`).
3. Have `performative: PROPOSE`, `from.eidolon: spectra`, `to.eidolon: apivr`, `edge_origin: roster`.
4. Have `artifact.kind: spec` and `artifact.schema_version: "1.0"`.

The Markdown frontmatter MUST validate against `schemas/spec-profile.v1.json` (required fields: `eidolon: spectra`, `kind: spec`, `target_repos`, `stories_count`, `validation_gates_count`).

Use `templates/spec.envelope.json` as the skeleton — fill every `<placeholder>` before emitting.

When `ECL_VERSION` is absent, skip envelope emission entirely. Non-ECL consumers experience zero behaviour change.

---

*SPECTRA v4.3.0*
