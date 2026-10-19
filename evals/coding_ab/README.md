# Executable coding memory A/B calibration

Issue [#74](https://github.com/Rynaro/crystalium/issues/74) adds a fixture-first
experiment inspired by the paired coding protocol in
[VibeMemBench](https://arxiv.org/abs/2609.23570). All memory arms run Crystalium's
own MCP server. This is an instrument calibration, with deliberately planted
helpful and harmful records. It cannot establish coding-model efficacy or justify
changing a production default.

```bash
docker compose run --rm crystalium python -m evals.coding_ab --check-determinism
docker compose run --rm crystalium pytest mcp-server/tests/test_coding_ab.py -q
```

The first command executes the entire experiment twice and requires identical
reports. It writes `results/latest/report.json` and `report.md`. The committed
[first fixture report](results/fixture/report.md) and
[machine-readable evidence](results/fixture/report.json) use four seeds, every arm,
2,000 bootstrap resamples and RNG seed 74. CI repeats the experiment with Docker
networking disabled and checks the report hash against this artifact.

## Protocol

`tasks.json` identifies fixture directories containing buggy `implementation.py`,
an executable failing `check.py`, and `reference.txt`. Reference repairs are used
only to validate the oracle before runs. The solver gets source, task instruction,
seed and injected memory; it receives neither test code nor reference fixes.
Each fixture must first fail and its reference must pass. Final success is a real
subprocess exit from the same test on the solver's returned source.

Every task/seed/arm has a newly created source workspace, absent store directory,
new configuration and new server session. Data is under a temporary directory,
never the shared development data volume. Ten records are committed identically
even in `off`; no arm reuses stores or solver output from another arm. Writes use
the advertised MCP `commit` tool as T1, through normal enforcement, and retrieval
uses `assemble_context`. The SDK performs initialize, tools/list and tools/call
over paired in-memory transport streams. The scheduler is not started: this is a
single pre-trajectory retrieval, without Dream consolidation.

The fixture profile uses production SQLite/blob persistence, sparse Aetheryte
retrieval, active/trust filtering, redaction and policy assembly. Vector and graph
adapters are explicitly null; it does not download embedding models. Composer
tokenization is explicitly whitespace words for deterministic offline accounting.
These are protocol units, **not model tokens**. This profile is also used when an
external solver is selected; it does not assess dense retrieval quality.

| Arm | Policy | k | Record form |
|---|---|---:|---|
| `off` | off | 3 | raw, no injection |
| `always_on_k10` | always_on | 10 | raw |
| `always_on_k3` | always_on | 3 | raw |
| `gated_k3` | gated, task's fixed `needs_memory` signal | 3 | raw |
| `fix_span` | always_on | 3 | typed #73 repair |
| `raw` | always_on | 3 | same repair facts in historical text |
| `irrelevant` | always_on | 10 | unrelated image-padding repair |

Raw and structured records contain the same repair facts; raw includes a short
historical-text prefix. Duplicate records intentionally calibrate k and token
accounting, rather than retrieval diversity. The irrelevant arm retrieves records
using an unrelated query from its own clean store, then truncates/pads the actual
injected text to **exactly** the paired `always_on_k10` word count. Raw retrieval
cost is retained separately as `retrieved_tokens`. Its arithmetic advice is wrong
for every target, so the instrument must detect positive distraction. This is a
planted harm check, not a realistic distribution of irrelevant memory.

The deterministic solver prioritizes patch directives in the actual memory text.
Without a directive, a seed-controlled exploration budget determines whether it
uses the function's public docstring. It does not branch on arm names or read
reference patches. Exploration budgets deliberately create baseline 0/4, 2/4,
and 4/4 success cases. Two manifest variants per repository have an existing file
anchor or a stale historical anchor; `no_anchor` means no applicable file anchor,
not an invalid empty `fix_span.anchors` list. Anchor applicability is independent
of baseline difficulty; this small solver does not exploit the anchor field.
These variants share code and are not independent real-world repositories.

## Metrics and claims

Arms always share the same task and seed set (default `0 1 2 3`). Custom seeds are
accepted with `--seeds`; baseline headroom labels display `N` for non-four counts.
Reports include overall, headroom, anchor and intersected headroom/anchor groups.

- `resolve_rate`: executable test successes / runs.
- `injected_tokens`: mean words actually delivered to the solver, after control
  matching. Zero for closed gates.
- `distraction_rate`: mean paired loss indicator (`off` passes, arm fails), with
  **all paired task/seed samples** in the denominator. Equal seed counts make this
  equivalent to averaging task-level loss fractions. It is zero when no loss occurs.
- `steps`: source-edit steps for the fixture solver; external solver's reported
  trajectory steps otherwise.
- `total_tokens`: unavailable (`null`) for the fixture solver. External adapters
  must return their actual full trajectory input/output token usage, including
  injected memory as consumed by the model. Never infer this from word counts.

For each arm versus `off`, compute the paired success difference for each seed,
average within task, and resample **whole tasks**, preserving all seed pairings.
The percentile 95% CI uses at least 1,000 resamples (default 2,000), fixed RNG seed
74. Seeds are not independent bootstrap observations. One-task strata yield
degenerate intervals; these are calibration output, not population uncertainty.
Shared-code anchor variants further limit external validity.

Only delta resolve-rate has a CI. Every metric without its own CI carries
`claims.<metric> = "no claim"`; other resolve comparisons are labeled fixture
calibration only. `evals.coding_ab.report.check_claims(report)` rejects unsupported
claim labels and is called before writing artifacts. It is the machine-readable
guard for [P0-1 / #72](https://github.com/Rynaro/crystalium/issues/72).
Shorter trajectories alone establish no useful-memory claim.

For a later representative live study, [#71](https://github.com/Rynaro/crystalium/issues/71)
proposes considering gated defaults when the paired resolve-delta lower bound is
nonnegative (or it beats always-on) and injected cost is lower. This fixture report
does not satisfy that efficacy decision: cost has no inferential CI and outcomes
are planted. It changes no production flags. Structured repair integration is
[#73](https://github.com/Rynaro/crystalium/issues/73); upstream consumers are tracked
in [eidolons#635](https://github.com/Rynaro/eidolons/issues/635) and
[cost/Pass^k #630](https://github.com/Rynaro/eidolons/issues/630).

## Opt-in external solver

The same small tasks can be solved by a trusted external adapter. No provider SDK
or credentials are bundled; live inference is never part of CI. Enable it explicitly:

```bash
docker compose run --rm -e CRYSTALIUM_CODING_AB_LIVE=1 crystalium \
  python -m evals.coding_ab \
  --live-command-json '["/app/.venv/bin/python", "/app/my_solver_adapter.py"]' \
  --out evals/coding_ab/results/live
```

The adapter argv receives two appended absolute paths, request JSON then response
JSON, and runs in that sample's isolated workspace. Request keys are `source`,
`task`, `memory`, `seed`, `exploration`. Ignore fixture-only `exploration` in a live
adapter; pin and record model/settings in your study notes and use `seed` for the
same sampling configuration across arms. Return `source` (complete implementation),
`steps` (nonnegative integer), and `total_tokens` (nonnegative integer). The adapter
has a five-minute timeout. Missing usage, malformed output, failed MCP or subprocess
errors abort the experiment instead of being counted as task failures.

Do not reuse conversation state or provider caches across samples. This subprocess
interface is for trusted code in the development container, not a filesystem
security sandbox: adapters must honor the source-only protocol and never inspect
reference files or evaluator tests. A real benchmark requires a stronger external
sandbox and a representative task corpus. The report still states synthetic task
calibration, even when a live adapter is used.

The committed report is ready to link from #71. Publishing a GitHub comment or
attachment is a separate operator action; this change does not post externally.
