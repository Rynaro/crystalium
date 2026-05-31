# CRYSTALIUM 0.1.0 → 1.0.0 — Wave Prompt Set

Nine files. One index (this), eight waves. **Each wave file is self-contained** — open a fresh
Claude Code session at the `crystalium` repo root and paste the whole wave file. Run them in
order; each is a clean minor version bump and independently shippable.

---

## Run conventions (the same for every wave)

1. **Model & effort.** `/model opus`, then `/effort xhigh`. For the algorithm-heavy waves (W2–W5)
   `opusplan` (Opus plans, Sonnet executes) is a good cost/quality trade.
2. **Plan first.** Press **Shift+Tab** to enter **plan mode** *before* the agent touches anything.
   The plan-mode approval prompt **is** the wave's approval gate — you'll release it with the
   wave's token (e.g. `APPROVED: BUILD W1`).
3. **Track the six phases as todos** (Scout → Verify → Plan → Approval → Execute+Commit →
   Pre-Push Report). Ask the agent to keep a todo list; don't let it skip the gate.
4. **Subagents.** This repo ships `.atlas`, `.spectra`, `.forge`. If those subagents are wired,
   delegate **Scout** to ATLAS and **Plan** to SPECTRA; otherwise use the built-in **Explore**
   subagent for scouting. Run the ablation bench in a dedicated subagent to keep the main context
   clean.
5. **Commits are your revert points.** Conventional Commits, one logical change per commit, on the
   wave's feature branch. **Never `git push`, never open a PR.** The ablation-or-revert rule relies
   on a clean commit history.
6. Type `/help` once to confirm these features exist in your installed version.

---

## One-time setup (do this inside W1, keep for all later waves)

CRYSTALIUM is **container-first**: the host runs only `docker compose`, `git`, `make`. Rather than
trust the prompt to remember that, enforce it mechanically with a **PreToolUse hook** so the agent
*cannot* run host `python`/`pip`/`pytest`/`uv`. W1 instructs the agent to create this; it persists
for every later wave.

`.claude/settings.json` (illustrative — have the agent verify the exact schema with `/hooks`):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": ".claude/hooks/no-host-python.sh" }
        ]
      }
    ]
  }
}
```

`.claude/hooks/no-host-python.sh` rejects any Bash command that invokes `python`, `pip`, `pytest`,
or `uv` unless the command is wrapped in `docker compose run` / `make`. Exit non-zero to block.

W1 also creates a reusable `/prepush` command (`.claude/commands/prepush.md`) that emits the
standard **Pre-Push Report** (changed files, ablation result vs prior version, canary pass rate,
token budgets, open TODOs, run commands). Every later wave ends by invoking `/prepush`.

---

## The ablation-or-revert rule (read once, applies everywhere)

Every algorithmic augment ships **behind a config flag, defaulted off**. The wave's Pre-Push Report
must show the augment **beating the prior version** on the wave's named metric. If it doesn't:
leave the flag off, keep the code for iteration, and **report the null/negative result honestly**.
Neuroscience and philosophy are the *generative hypotheses*; the **A/B is the arbiter**. Never
massage a metric to clear a gate.

---

## Invariants (never violate — repeated in every wave)

- Container-first: all Python toolchain inside Docker; host runs only `docker compose`/`git`/`make`.
- The `enforcement.py` chokepoint is sacred: new behavior goes *behind* it, never around it; its
  invariant tests stay green.
- ECL v2.0 envelopes on every tool result (11 fields + SHA-256). EIIS v1.4 conformance.
  `install.sh` bash-3.2-safe. `agent.md` ≤ 1000 tokens. Working set ≤ 3,500 tokens.
- Pointer-indexed storage; episodic payloads stay content-addressed on the blob tier. Local-first;
  no new mandatory external service (optional local Ollama only).
- Schema-first: add fields now (even unpopulated); bump `crystal.v2.json` only on a breaking change.

---

## The eight waves

| File | Version | Title | Approval token |
|---|---|---|---|
| `W1-foundations-eval-spine.md` | v0.2.0 | Foundations & Eval Spine | `APPROVED: BUILD W1` |
| `W2-importance-evb.md` | v0.3.0 | Importance as Expected Value of Backup | `APPROVED: BUILD W2` |
| `W3-dream-intelligence.md` | v0.4.0 | The Dream Becomes Intelligent | `APPROVED: BUILD W3` |
| `W4-forgetting-faculty.md` | v0.5.0 | Forgetting as a Faculty | `APPROVED: BUILD W4` |
| `W5-retrieval-intelligence.md` | v0.6.0 | Retrieval Intelligence (Aetheryte II) | `APPROVED: BUILD W5` |
| `W6-security-hardening.md` | v0.7.0 | Security & Integrity Hardening | `APPROVED: BUILD W6` |
| `W7-eidolons-integration.md` | v0.8.0 | Eidolons Integration | `APPROVED: BUILD W7` |
| `W8-conformance-roster.md` | v1.0.0 | Conformance Freeze & Roster Publication | `APPROVED: BUILD W8` |

**Hard ordering constraints:** W1 before everything (it's the measurement spine). W2 before
W3–W5 (they consume EVB). **W6 before W7** (never expose unhardened shared memory to the roster).
W8 last.
