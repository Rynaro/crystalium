# CRYSTALIUM · Wave 2 — Importance as Expected Value of Backup (v0.3.0)

**Paste this whole file into a fresh Claude Code session at the `crystalium` repo root.**
**Prereq:** Wave 1 merged (ablation bench + fixture repo exist).

The keystone reformulation. v0.1 ranks memories with a flat weighted sum
`[0.25, 0.30, 0.25, 0.20]` over (access_frequency, recency, outcome_success, novelty). Replace it
with a single, normatively-grounded function — **Expected Value of Backup** — consumed identically
by the write-gate eviction logic, the Dream's selection, and the working-set composer.

## Why (research → algorithm)
Mattar & Daw 2018 (*Nat Neurosci* 21:1609–1617, DOI 10.1038/s41593-018-0232-z; **verified**) show
that optimal memory access/replay is ordered by **EVB = Gain × Need**: Gain = how much accessing
this memory improves future choices; Need = how likely/soon it will be relevant. This one product
unifies what to keep, what to evict, and what to replay. (2025 follow-on "SuRe" ports prioritized
replay to continual LLM learning — MEDIUM.)

## Run it like this
- `/model opus` then `/effort xhigh` (or `opusplan`). **Shift+Tab → plan mode** first.
- Six-phase todo list; SPECTRA subagent for Plan if `.spectra` is wired.
- Branch `feat/crystalium-v0.3.0`. Conventional Commits. **Never push.**
- Approve token: **`APPROVED: BUILD W2`**.

## Invariants (never violate)
Container-first (Docker only; the W1 PreToolUse hook enforces it). Chokepoint sacred. ECL v2.0 +
EIIS v1.4. `agent.md` ≤ 1000 tokens; working set ≤ 3,500. Local-first. **Ablation-or-revert:** ship
behind a flag default-off; beat baseline or report the null result honestly.

## Objective
1. New module `evb.py`:
   - `gain(crystal, outcome_ctx) -> float` ≈ g(outcome_success, novelty, corroboration_potential).
   - `need(crystal, query_ctx) -> float` ≈ n(recency, access_frequency, predicted_next_task_match).
   - `evb(crystal, ctx) = gain * need`. Stable signatures, hand-tuned weights in config.
   - **Deliberate approximation** (document it): true Mattar–Daw Gain needs counterfactual policy
     evaluation; this is the SFMA-style proxy. The proxy is itself flagged/ablatable.
2. Refactor so `importance.py`, the eviction logic, and the Dream's selection all consume `evb()`
   as the **single source of truth**. Keep the old blend selectable as `importance.mode: legacy`
   for the A/B; default new mode `evb` **off** until the bench says otherwise.
3. Cache `evb` on the crystal (the W1 schema field); recompute on access/outcome events.
4. Anchor in `DESIGN-RATIONALE.md`: Mattar & Daw 2018 [verified]; SFMA proxy [MEDIUM]; state the
   neuroscience-as-hypothesis / ablation-as-arbiter stance.

## Definition of done (ablation gate)
Run `ab("importance.mode=evb", canary)`. EVB must beat `legacy` on **both**:
- **promotion precision** (fraction of promoted crystals later recalled usefully), and
- **high-value retention** on the canary.
If either regresses, leave `evb` off and report it. Run `/prepush` with the A/B table.

## Out of scope
Dream ordering, forgetting curves, retrieval changes — later waves consume EVB, they don't ship here.
