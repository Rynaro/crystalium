# CRYSTALIUM · Wave 1 — Foundations & Eval Spine (v0.2.0)

**Paste this whole file into a fresh Claude Code session at the `crystalium` repo root.**

You are upgrading the released v0.1.0 memory harness. This first wave builds **no intelligence** —
it builds the **falsification spine** that every later wave's gate depends on, plus the HTTP
transport stubbed in v0.1. Build the harness before the brain.

---

## Run it like this
- `/model opus` then `/effort xhigh`.
- Press **Shift+Tab** to enter **plan mode** now. Do not edit until I approve.
- Keep a todo list of the six phases: Scout → Verify → Plan → Approval → Execute+Commit → Pre-Push.
- Delegate **Scout** to the ATLAS subagent if `.atlas` is wired, else the Explore subagent.
- Branch `feat/crystalium-v0.2.0`. Conventional Commits. **Never push.**
- Approve token: **`APPROVED: BUILD W1`** (release plan mode only when I send it).

## Invariants (never violate)
- Container-first: all Python runs inside Docker via `docker compose run --rm crystalium <cmd>` or
  `make`. Host runs only `docker compose`/`git`/`make`. No host `python`/`pip`/`pytest`/`uv`.
- The `enforcement.py` chokepoint is sacred — extend behind it, never around it; its tests stay green.
- ECL v2.0 envelopes (11 fields + SHA-256) on every tool result. EIIS v1.4. `PERSONA.md` ≤ 1000 tokens.
- Local-first; no new mandatory external service.

## Objective
1. **Mechanical container-first guard.** Create `.claude/hooks/no-host-python.sh` + register a
   **PreToolUse `Bash` hook** in `.claude/settings.json` that blocks host `python`/`pip`/`pytest`/`uv`
   unless wrapped in `docker compose run` / `make`. Verify the hook schema with `/hooks`.
2. **Reusable `/prepush` command.** Create `.claude/commands/prepush.md` that emits the standard
   Pre-Push Report (changed files, ablation delta vs prior version, canary pass rate, token
   budgets, open TODOs, run commands).
3. **Unstub HTTP transport** (Streamable-HTTP) — remove `NotImplementedError("v0.2")`; keep stdio
   default. Smoke-test wiring to a host via `.mcp.json`.
4. **Ablation bench** in `evals/`: `ab(flag, missions) → {axis: (on, off, delta)}` running any
   canary mission with a named feature flag on vs off against a **seeded fixture repo** (create one;
   deterministic, committed). Generalize the existing memory-on/off A/B into this.
5. **SWE-Bench-CL metric axes** over a task sequence: average accuracy, **forgetting**,
   **forward/backward transfer**, tool-use efficiency. Add a **selective-forgetting probe** (plant
   fact → change it → assert stale not retrieved).
6. **Observability**: OTEL spans + structlog panels for `recall`/`commit`/`forget`/`dream`, incl.
   per-call latency and a **recall p95** panel (needed for the W8 availability SLO).
7. **Schema-first migration**: add v1.0 fields to `schemas/crystal.v1.json` *unpopulated*:
   `stability`, `retrievability`, `difficulty`, `evb`, `encoding_context`, `tags`,
   `prediction_error`, `protected`. Validate.
8. Optionally enable headless runs (`claude -p`) so the bench can run in CI later.

## Definition of done (ablation gate)
- Bench reproduces the v0.1 memory-on/off result **and** computes all new axes on the fixture repo.
- HTTP transport passes a host smoke test; stdio still default.
- Container-first hook actually blocks a host `pytest` invocation (demonstrate).
- All v0.1 invariant tests still green. Run `/prepush`.

## Out of scope
Any algorithm change. This wave only *measures* and *enforces*. No EVB, no Dream changes, no
retrieval changes.
