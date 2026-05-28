# Skill: promote — k-corroboration + human-confirm UX

Load this skill when promoting a crystal from Episodic to Semantic, or when
a Procedural skill transitions from `candidate` to `admitted`.

## When to load

- Debugging `PromotionPending` or `PromotionGated` errors.
- Scripting the `crystalium promote review <id> --accept` flow.
- Understanding the k-corroboration threshold.

## K-corroboration rule (FORGE D8)

A Semantic promotion requires **≥ k independent corroborating sources** from
distinct `provenance.author_agent` values, OR explicit human confirmation.

Default `k = 3`. Relaxation to `k = 2` for single-operator installs is an
open question (OQ-5; not implemented in v0.1).

## Human-confirm window

- Active for the first 30 days post-install (`install.ts` + 30d).
- While active: every promotion proposal lands in `pending_promotions` table
  with `status="pending"`.
- Operator confirms via CLI: `crystalium promote review <promotion_id> --accept`
  (or `--reject`).
- After 30 days: promotions auto-execute if `k` threshold met AND
  `human_confirm=false` in `crystalium.yaml`.

## CRYSTALIUM_AUTO_CONFIRM bypass

Set `CRYSTALIUM_AUTO_CONFIRM=1` in the environment to skip human-confirm for
automated testing. Emits a `WARN` log. NEVER set in production.

## force_promote (T0 only)

T0 callers may force-promote straight through without the k-corroboration
requirement. Audit trail: telemetry record emitted with `op="force_promote"`.
(OQ-1: whether force_promote should also add a pending_promotions row for
completeness is under review post-v0.1.)
