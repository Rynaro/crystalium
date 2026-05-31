# CRYSTALIUM · Wave 6 — Security & Integrity Hardening (v0.7.0)

**Paste this whole file into a fresh Claude Code session at the `crystalium` repo root.**
**Prereq:** Waves 1–5 merged. **This wave MUST precede W7** — never expose an unhardened shared
memory to the roster. A poisoned crystal propagates through normal handoffs.

Promote the items deferred from the MVP into real defenses, and prove resistance with an adversarial
suite.

## Why (research → algorithm)
- **Memory poisoning is a catalogued threat** — OWASP ASI06; LTM Security Survey (arXiv:2604.16548);
  PoisonedRAG (USENIX Security 2025); MINJA. Its defining property is **temporal decoupling**: write
  and activation are separated in time, so retrieval-time content filtering alone is insufficient —
  the defense lives on the write/promote path and in cross-session consistency.
- **A-MemGuard** — separate consistency checks + a distinct lesson memory.
- v0.1 already propagates **MIN trust tier** across consolidation — extend, don't replace.

## Run it like this
- `/model opus`, `/effort xhigh`. **Shift+Tab → plan mode** first. Six-phase todo list.
- Branch `feat/crystalium-v0.7.0`. Conventional Commits. **Never push.**
- Approve token: **`APPROVED: BUILD W6`**.

## Invariants (never violate)
Container-first (W1 hook). Chokepoint sacred — all new checks live at or behind it. ECL v2.0 +
EIIS v1.4. Trust-tier-MIN propagation preserved. **Ablation-or-revert** where a metric applies.

## Objective
1. **Belief-drift detection** (A-MemGuard-style): periodic consistency checks over the audit log;
   flag semantic facts that silently contradict higher-trust priors.
2. **Quarantine review workflow**: mature `crystalium promote review` into a real triage queue over
   `validation_state: quarantined`, with operator accept/reject + recorded reasons.
3. **Poisoning-resistance suite** (`evals/`): inject PoisonedRAG- and MINJA-style write-time poison
   with **delayed cross-session activation**; measure **attack success rate (ASR)** under the
   trust-tier + gate + drift defenses. Set an explicit ASR pass bar.
4. **Multi-agent write-conflict detection + provenance-aware merge**: when two agents write
   conflicting semantic facts, detect, bi-temporally supersede, and record **both** lineages
   (last-write-wins resolution; conflicts surfaced, never silently dropped).

## Definition of done (gate)
- Poisoning-resistance suite **passes its ASR bar**.
- Drift detector catches seeded contradictions in the bench.
- No regression on prior waves' canaries. Run `/prepush` with the ASR table.

## Out of scope
Roster artifact ingestion (W7), conformance freeze (W8). Belief-drift *auto-remediation* is out —
detect and flag only; remediation stays operator-gated.
