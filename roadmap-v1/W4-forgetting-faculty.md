# CRYSTALIUM · Wave 4 — Forgetting as a Faculty (v0.5.0)

**Paste this whole file into a fresh Claude Code session at the `crystalium` repo root.**
**Prereq:** Waves 2–3 merged (EVB + intelligent Dream).

Forgetting in v0.1 is implicit LRU. Make it a principled faculty: a decay model where recall
strengthens memory, eviction is value-aware, important-but-aging crystals are re-surfaced before
they fade, and a protected class is never forgotten. Expect this to be the noisiest wave — budget
several A/B iterations.

## Why (research → algorithm)
- **Forgetting curve / FSRS-DSR** (open-spaced-repetition; **verified**; runs fully local):
  each item has Difficulty, **Stability**, **Retrievability**; `R = exp(ln(0.9) · elapsed/S)`;
  successful recall **raises S**; a lapse resets it. (20–30% fewer reviews than SM-2 for equal
  retention.)
- **Reconsolidation** — Nader 2000; Schiller 2010 `[CONTESTED: Chalkia 2020]`: retrieval restabilizes
  a memory. Maps to "recall boosts stability." The implementation stands on engineering grounds
  regardless of the human-evidence dispute.
- **Active forgetting** — Nietzsche (*On the Uses and Disadvantages of History for Life*); **Funes**
  (Borges): forgetting is a positive faculty; abstraction requires discarding detail.
- **Duty of memory** — Ricoeur (*Memory, History, Forgetting*, 2000): some traces must never be lost.

## Run it like this
- `/model opus`, `/effort xhigh`. **Shift+Tab → plan mode** first. Six-phase todo list.
- Branch `feat/crystalium-v0.5.0`. Conventional Commits. **Never push.**
- Approve token: **`APPROVED: BUILD W4`**.

## Invariants (never violate)
Container-first (W1 hook). Chokepoint sacred. Never hard-delete **except** the explicit
right-to-be-forgotten op below. ECL v2.0 + EIIS v1.4. **Ablation-or-revert.**

## Objective (behind `forgetting.mode=fsrs`, default-off)
1. **FSRS/DSR decay**: populate `stability`, `retrievability`, `difficulty` (W1 schema). Compute
   `R = exp(ln(0.9)·elapsed/stability)`. Recall **boosts stability** (reconsolidation); lapse resets.
2. **Value-aware eviction**: evict / down-tier to cold blob only when `R < r_floor` **AND** `EVB`
   below percentile — never on age alone.
3. **Spaced re-surfacing**: the Dream re-promotes important-but-aging crystals *before* `R` crosses
   the floor (the spacing effect working for you).
4. **Ricoeur-protected class** (`protected` flag, W1 schema): provenance records, `[DECISION]`-tagged
   semantic facts, and the audit log are exempt from decay/eviction.
5. **Right-to-be-forgotten**: an explicit, audited, operator-gated hard-tombstone — the one
   sanctioned exception to never-hard-delete.

## Definition of done (ablation gate)
Over a long synthetic session, `forgetting.mode=fsrs` vs LRU must show:
- **memory size plateaus** (vs linear growth), and
- **high-value recall retained**, and
- **recall latency** improves or holds.
Report all three. If it regresses, keep LRU and report. Run `/prepush`.

## Out of scope
Retrieval/prefetch (W5), security (W6). Protected-class definition is final here — don't widen it
to dodge the eviction gate.
