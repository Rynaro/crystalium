# Ablation bench — notes & tracked discrepancies (W1, v0.2.0)

## What the bench is

The bench is the **arbiter** for the ablation-or-revert rule. Every later wave's
algorithm ships behind a Config flag defaulted **off**, and only flips on if the
bench shows it beating the prior version on the wave's named metric.

- `python -m evals canary --mode both` — legacy memory-on/off A/B headline.
- `python -m evals ab --flag <name> --missions CAN-1,CAN-3,...` — generalized
  ablation: `ab(flag, missions) -> {axis: (on, off, delta)}`. `--flag memory`
  reproduces the legacy null-arm; any Config boolean field toggles live-on/off.
- `python -m evals axes [--demo|--matrix JSON]` — SWE-Bench-CL axes
  (average_accuracy, forgetting, backward/forward transfer, tool_use_efficiency).
- `python -m evals forget` — selective-forgetting probe (reuses
  `selective_forgetting.py`; also drives CAN-4).
- Container-first runner (CI-proven): `docker run --rm crystalium:dev python -m evals ...`
  or `make bench`. (`docker compose run` is unusable for the venv — see below.)

The deterministic seeded fixture repo is `evals/fixtures/fixture_repo.json`,
loaded by `evals/fixture_repo.py` (byte-stable across loads); both A/B arms can
seed it for a shared baseline.

## Reproduced v0.1 parity (DoD G-A) — PASS

`ab(flag="memory", missions={CAN-1,CAN-3,CAN-4,CAN-5})` reproduces the v0.1
headline **exactly**: `pass_on=0.25, pass_off=1.0, delta=-0.75` — byte-identical
to the committed baseline `evals/results/2026-05-28T20-09-30…json`.

### [UNVERIFIED] Honest null result (not massaged)
The headline delta is **negative** (memory-off "wins"). This is a **pre-existing
property of the canary suite**, not a v0.2.0 regression: the null arm's mission
oracles are lenient (an empty store trivially satisfies "no-leakage"-style
criteria), so the A/B does not yet show memory *helping*. Reported plainly per
the ablation-or-revert rule. Strengthening the missions with genuinely
memory-dependent oracles is later-wave work; W1 only builds the measurement
spine and reproduces the baseline.

## [DISPUTED] DECISION-2 — A/B arm-set drift (tracked, NOT changed in W1)

The A/B arm set disagrees between code and the frozen spec:

| Source | A/B arms |
|---|---|
| code (`evals/missions.py:AB_ARM_MISSION_IDS`) | `{CAN-1, CAN-3, CAN-4, CAN-5}` |
| spec (`.spectra/crystalium-v0.1.0-spec.md:713`) | `{CAN-1, CAN-3, CAN-5, CAN-6}` |

Note the code and spec also number missions differently (code CAN-4 =
selective-forget, CAN-5 = multi-agent-isolation). W1 **keeps the code set** as
the reproduction baseline (DECISION-2) so it proves v0.1 parity, and logs the
drift here. Reconcile in a later wave before the roster consumes the headline.

## Toolchain caveat — `docker compose run` vs baked image

`docker-compose.yml` bind-mounts `.:/app`, which shadows the image's baked
`/app/.venv`; `uv run` then falls back to the dependency-less root
`pyproject.toml` and cannot find pytest/the deps. CI and these notes therefore
use `docker run --rm crystalium:dev <cmd>` against the **baked** image (see
`.github/workflows/ci.yml` rationale). `make bench`/`make test` use the compose
path for convention parity and share this caveat. **[GAP]** A future wave could
relocate the venv (e.g. `UV_PROJECT_ENVIRONMENT=/opt/venv`) so the compose path
works with live edits; out of scope for W1.
