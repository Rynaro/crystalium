# Conditional memory injection

[DECISION] Issue [crystalium#71](https://github.com/Rynaro/crystalium/issues/71)
adds `assemble_context` for task context. Explicit `recall` remains a memory search
API with default `k=10`; existing clients retain that behavior.

## Migrate callers

[ACTION] Hosts that inject task context should expose and call `assemble_context`
instead of unconditionally injecting `recall` results. Keep explicit user/agent
memory searches on `recall`. Upstream Eidolons pre-flight integration is tracked in
[eidolons#635](https://github.com/Rynaro/eidolons/issues/635); this change does not
rewrite installed upstream cortex contracts.

```json
{
  "scope": {"project": "<canonical-project-key>", "agent_class_visibility": "<agent-name>"},
  "query": "parser failure and prior fixes",
  "k": 3,
  "needs_memory": true,
  "prior_failure": false,
  "layers": ["semantic", "episodic", "procedural"]
}
```

`scope.project` and a nonblank `query` are required. Use the canonical project key
advertised at initialization (the `CRYSTALIUM_DATA_DIR` basename), or an explicit
legacy key for legacy records. `layers` is optional. `k` defaults to 3 and must be
an integer from 1–100; assembly rejects invalid values rather than clamping them.
Recommend `k<=3` for task context. Both signals are strict booleans, default false:
set `needs_memory` when the task needs prior context, or `prior_failure` after a
failed attempt. There is no hidden classifier.

## Operator configuration

Set `memory_injection_mode` in YAML configuration, or use the environment-based
configuration setting `CRYSTALIUM_MEMORY_INJECTION_MODE`. Invalid modes are rejected.
The request cannot override the operator mode.

```yaml
memory_injection_mode: gated
```

| Mode | Retrieval gate | Recommended use |
|---|---|---|
| `gated` (default) | Open when either signal is true | Task context, `k<=3` |
| `always_on` | Always open | Comparison baseline; fixture uses `k=10` |
| `off` | Always closed | Disable automatic context injection |

The mode applies to `assemble_context`; explicit `recall` remains available.
Closed gates return empty `records` and zero `injected_token_count` without calling
retrieval or composition. Open gates call protected Aetheryte recall with a
candidate limit of `min(100, max(10, k))`, retaining project/tier checks, redaction,
and BM25/vector/graph retrieval. Selection uses returned redacted summaries only;
a final cap always limits injected records to `k`, including when
`recall_relevance_primary=False`. Fewer records may be available after retrieval
and composer budgets.

## Selection and accounting

Selection starts with descending hybrid scores. Each relevance band is anchored
to its highest remaining score and includes scores at least 90% of that anchor;
a zero anchor admits exact ties only. Within each band, prefer summaries of at
most 128 composer tokens containing a fix/outcome cue in the first 4,096 characters.
Then prefer shorter summaries, original hybrid rank, and record id. Materially
stronger scores retain precedence across bands.

Cues include fix/fixed/fixes, resolve/resolved, outcome, and test/tests pass/passed.
This bounded text heuristic does not verify a fix or inspect raw payloads. Typed
`fix_span.v1` records remain separate work in
[crystalium#73](https://github.com/Rynaro/crystalium/issues/73).

The result contains `records`, `gate_open`, `gate_reason`, `mode`, `k_requested`,
and `injected_token_count`. Reasons are `off`, `always_on`, `needs_memory`,
`prior_failure`, or `no_signal`; `needs_memory` takes precedence when both signals
are true in gated mode. Tokens are the sum of the existing composer's tokenizer
counts for selected redacted `summary` strings only. Metadata, JSON serialization,
and ECL envelopes are excluded, so this is not a total prompt or wire-token count.

## Reproduce the policy fixture

```bash
docker compose run --rm crystalium python -m evals.conditional_injection
```

The deterministic fixture compares gated (`k=3`), always_on (`k=10`), and off on the
same three synthetic tasks and fixed safe candidates. It uses whitespace word
counts for fixture tokens, rather than the production composer tokenizer.

- `injected_token_count`: total selected summary words over all fixture tasks.
- `resolve_rate`: memory-needed fixtures receiving an author-labeled useful record
  divided by memory-needed fixtures. The report includes numerator and denominator.
- `distraction_rate`: injected records without an author-labeled useful id divided
  by all injected records; zero when no records are injected. The report includes
  numerator and denominator.

These are label-based policy proxies. No coding agent executes a task; the fixture
does not measure task resolution, retrieval quality, or coding performance gains.
The [#74 executable coding A/B harness](../evals/coding_ab/README.md) now adds real
fixture test execution, isolated first-party MCP sessions, headroom strata,
token-matched harmful-memory controls and paired task bootstrap intervals. Its
[first report](../evals/coding_ab/results/fixture/report.md) is synthetic calibration;
it provides no live coding efficacy evidence or production default recommendation.
