---
description: Emit the CRYSTALIUM Pre-Push Report (the wave gate before any push)
---

You are generating the **Pre-Push Report** for the current CRYSTALIUM wave branch.
This report is the wave's final gate (the roadmap's sixth phase). Gather every
number by running the container-first commands below — never invoke host
`python`/`pytest`/`uv` (the PreToolUse guard blocks them). Report results
faithfully: if a number is missing or a step failed, say so plainly. Do **not**
push and do **not** open a PR — that is always the operator's call.

Emit the report as six clearly-headed sections, in this order:

## 1. Changed files
Run and summarize:
- `git --no-pager diff --stat $(git merge-base HEAD main)..HEAD`
- `git --no-pager log --oneline $(git merge-base HEAD main)..HEAD`
Group changed files by area (mcp-server/src, evals/, schemas/, .claude/, tests).
Call out anything touching `enforcement.py` (the sacred chokepoint).

## 2. Ablation delta vs prior version
For each feature flag introduced this wave, report the A/B delta on its named
metric from the ablation bench (flag on vs off). If the wave introduced no new
flag, report the memory-on/off headline parity instead.
- `make bench` (or `docker compose run --rm crystalium python -m evals ...`)
State the metric, the (on, off, delta) triple, and whether it **beats** the
prior version. If the augment does not beat prior: say so, and confirm the flag
is left **defaulted off** (ablation-or-revert — never massage the metric).

## 3. Canary pass rate
Report the canary A/B headline pass rate over the arm set and whether it meets
the ≥ 0.80 target. Name the arm set used (note any tracked drift between code
and spec). Command: the bench from §2.

## 4. Token budgets
Report current token counts against their caps:
- `agent.md` ≤ 1000 tokens
- composer working set ≤ 3500 tokens
Use the in-container tokenizer (composer's tiktoken) if a check exists; otherwise
state the method used and mark the number `[approx]`.

## 5. Open TODOs
List unresolved items for this wave: `[GAP]`/`[DISPUTED]`/`[UNVERIFIED]` markers,
skipped tests, flags left off, and anything deferred to a later wave. Be honest —
an empty list is only valid if truly nothing is outstanding.

## 6. Run commands (reproduce every number above)
Give the exact container-first commands a reader can copy to regenerate each
number, e.g.:
- `make test` / `make test-w1` — full + W1 gate suites
- the six invariant suites (enforcement, trust, promotion, skill, bitemporal, redaction)
- `make bench` — ablation + canary
- `make test-schemas` — schema round-trip

End with a one-line **verdict**: `READY` only if all wave DoD/ablation-gate items
pass and all v0.1 invariant tests are green; otherwise `NOT READY` with the
blocking items named.
