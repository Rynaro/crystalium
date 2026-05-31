# CRYSTALIUM · Wave 3 — The Dream Becomes Intelligent (v0.4.0)

**Paste this whole file into a fresh Claude Code session at the `crystalium` repo root.**
**Prereq:** Wave 2 merged (`evb()` is the single source of truth).

The Dream worker currently scans chronologically on idle. Make it order by value, interleave to
avoid drift, and capture context around important moments.

## Why (research → algorithm)
- **Prioritized replay** — Mattar & Daw 2018: replay ordered by EVB; reverse replay propagates new
  outcomes backward, forward replay serves what's needed next.
- **Complementary Learning Systems** — McClelland/McNaughton/O'Reilly 1995; Kumaran/Hassabis/
  McClelland 2016 (TICS): interleave new with old to avoid catastrophic interference in semantics.
- **Synaptic tagging & capture** — Frey & Morris 1997 (*Nature* 385:533); behavioral tagging
  (Moncada & Viola 2007): a salient event lets *nearby-in-time* weak memories consolidate too.
- **Funes / abstraction** — Borges 1942; Nietzsche, active forgetting: a promotion that doesn't
  generalize isn't consolidation. Measure it.

## Run it like this
- `/model opus`, `/effort xhigh` (or `opusplan`). **Shift+Tab → plan mode** first.
- Six-phase todo list. Run the bench in a dedicated subagent to keep context clean.
- Branch `feat/crystalium-v0.4.0`. Conventional Commits. **Never push.**
- Approve token: **`APPROVED: BUILD W3`**.

## Invariants (never violate)
Container-first (W1 hook). Chokepoint sacred — the Dream still only *proposes*; admission goes
through the gate. ECL v2.0 + EIIS v1.4. Working set ≤ 3,500. **Ablation-or-revert** per augment.

## Objective (each behind its own flag, default-off)
1. **Prioritized replay** (`dream.replay=evb`): order the consolidation queue by EVB — reverse
   replay of recent high-Gain episodes, forward replay biased by Need.
2. **Interleaved replay** (`dream.interleave=on`): each batch mixes new + a sampled ratio
   (`dream.interleave_ratio`) of existing semantic facts.
3. **Synaptic tagging & capture** (`dream.stc=on`): when an episode's EVB exceeds `stc_threshold`,
   episodes within `stc_window_s` get a **lowered promotion threshold**.
4. **Abstraction metric**: log compression ratio of each episodic→semantic promotion; flag
   promotions with ratio ≈ 1 (no generalization).

## Definition of done (ablation gate)
- `dream.replay=evb` + `dream.interleave=on` vs chronological: **consolidation gain up AND
  semantic-drift rate not worse**.
- `dream.stc=on` ablated separately: **useful-context-retention-around-key-events up** without a
  precision regression.
- Flags that don't win stay off; report nulls. Run `/prepush` with the A/B tables.

## Out of scope
FSRS decay/forgetting (W4), retrieval/prefetch (W5). The Dream proposes; it never force-writes.
