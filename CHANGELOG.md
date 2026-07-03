# Changelog

All notable changes to CRYSTALIUM are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows
[SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.6.0] — 2026-07-03

Wave 4 — memory diagnosability + guards. MOTIVATING INCIDENT: a live project
store held 9 crystals yet answered EVERY recall with 0 records. Forensic root
causes: (1) the plan's only checkpoint was `status=deprecated` and
`recall_active_only` (default ON) filtered it; (2) writers used 3 different
free-typed `scope.project` keys for the same project, so scoped recall
silently partitioned; (3) summaries were terse machine labels (`plan_checkpoint:
08234787`) — the only FTS-indexed text; (4) `embedding_ref` was null on every
crystal (heavy deps absent → Null vector store) so the dense arm was silently
inactive. Nothing surfaced any of this. This release makes all four
impossible-silently: diagnosability and mechanical guards, not new faculties —
none of it is gated behind an ablation flag.

### Added

- **Canonical project-key derivation + write-time scope normalization
  (`scope.py`, new module).** `scope.canonical_project_key(data_dir)` derives
  the canonical `scope.project` from the basename of `CRYSTALIUM_DATA_DIR` —
  the store IS the project. Every write path (`crystalium.commit`,
  `crystalium.ingest`, `crystalium.plan_checkpoint`, `crystalium.plan_replan`)
  now normalizes `scope.project` to that canonical key: a differing
  caller-supplied value is preserved verbatim in a new optional
  `scope.project_raw` field, and the tool result carries a
  `scope_normalized: true` advisory. `recall` does the opposite: an *explicit*
  `scope.project` passes through unrewritten (it's a read filter, not a write
  of record — legacy/fragmented keys must stay queryable for diagnosis), and
  only an *omitted* `scope.project` defaults to the canonical key instead of
  the literal string `"default"`. No migration of existing rows (out of
  scope) — `doctor` and `recall --explain` surface pre-existing fragmentation
  instead of papering over it. `crystal.v1.json`'s `scope` object gains the
  matching optional `project_raw` property.

- **Summary-quality gate at write (`quality.py`, new module; soft in 1.6).**
  `crystalium.commit` and `crystalium.plan_checkpoint` mechanically check the
  summary: ≥ 24 chars, ≥ 3 alphabetic words, and not shaped like a bare
  machine label (`^[a-z_]+:[0-9a-f-]+$`). A failing summary is still
  **accepted** — this never breaks a writer in a minor — but the result
  carries `summary_quality: "poor"` plus a one-line `advisory`.
  `plan_checkpoint` additionally auto-enriches its own server-composed
  default label: where it used to fall back to the bare
  `f"plan_checkpoint:{id[:8]}"`, it now composes plan name + project + phase
  words (e.g. `"plan checkpoint for Wave 4 rollout (project=eidolons)
  phase=verify [08234787]"`) — an *explicit* caller-supplied summary,
  however poor, is never silently rewritten, only flagged.

- **`recall --explain`** (CLI flag on `crystalium recall`, and MCP
  `crystalium.recall` param `explain: bool`). The result gains an `explain`
  object: `{candidates_prefilter, filtered_by_status, filtered_by_scope,
  arms: {bm25: on|off, dense: active|inactive(reason), graph: on|off}, store:
  {total_crystals, active, embedded}, project_keys_present: [...]}`. A
  zero-record recall against a non-empty store is now diagnosable from the
  result alone — this is the MOTIVATING INCIDENT test. `explain=true` always
  bypasses the recall cache (both read and write), so a diagnostic call is
  always fresh and never leaks a stale `explain` object into a normal
  caller's cached response. `RecallResult` gains an optional `explain` field
  (default `None`, dumped with `exclude_none=True`), so a non-explain call's
  JSON shape is byte-identical to pre-1.6.

- **`RelationalStore.diagnostics_summary()`** — aggregate, unscoped store
  counts (`total_crystals`, `active`, `embedded`, `by_status`, `by_project`).
  Backs both `recall --explain`'s `store`/`project_keys_present` and the
  `doctor` upgrade below.

- **`doctor` upgrades.** The `crystalium doctor` CLI subcommand now also
  reports embedded-vs-total crystal counts, crystal counts by status and by
  project key (with a `[WARN]` when more than one distinct project key is
  present in a single store — the fragmentation the MOTIVATING INCIDENT
  hinged on), and dense/graph arm status with a reason when inactive. Purely
  informational — never affects the P0 exit code, and any diagnostics error
  degrades to a `[WARN]` line rather than crashing `doctor`.

- **Never-deprecate-last-checkpoint guard (`dream/worker.py::DreamWorker._prune`).**
  Before auto-deprecating a below-threshold Execution-layer crystal, the
  prune phase now checks whether it is the ONLY active checkpoint for its
  `scope.plan` (falling back to `scope.project` when `plan` is absent, and
  conservatively protecting the crystal when neither is resolvable). If so,
  the deprecation is skipped and the reason is recorded via `record_call(...,
  op="prune_guard", result="skipped")` instead. This is the MOTIVATING
  INCIDENT's actual root cause: `dream.prune`'s threshold heuristic — the
  ONLY code path that sets a crystal's `status` to `deprecated` besides the
  T0-operator-gated quarantine reject (which never reaches Execution-layer
  crystals, since those are never quarantined) — had no concept of "this is
  the last one." A new `RelationalStore.count_active_by_scope_key()` backs
  the guard (parameterized `json_extract` query; scope-key path is a
  whitelisted constant, never string-interpolated).

### Fixed

- **`mcp.server.lowlevel.Server` was never told its own version.**
  `server._build_server()` constructed `Server("crystalium")` without a
  `version=` argument, so `create_initialization_options()` fell back to the
  *installed `mcp` SDK package's* version for `serverInfo` — not even the
  stale `crystalium.__version__` literal, a different component's version
  entirely. Now passes `version=__version__` explicitly.
- **`crystalium.__version__` was a stale `"1.0.0"` literal** untouched since
  the earliest releases. Now single-sourced via
  `importlib.metadata.version("crystalium")`, falling back to a literal kept
  in sync with `pyproject.toml` when the package isn't installed with
  metadata (e.g. a raw `PYTHONPATH=src` dev checkout).
- **Tool-count claims corrected from 7 to 9** in `SPEC.md`, `server.py`
  docstrings, and the `README.md` tool-surface table (which was also missing
  the `graph_export` row entirely, added in 1.5.0). The count grew via
  `ingest` (v0.7/W7, the 8th tool) and `graph_export` (1.5.0/W-GE5, the 9th);
  `SPEC.md`'s "(7 tools)" line predates both.
- **G1.2 / G1.3 (`ROADMAP-POST-1.0.md`)** — confirmed already resolved
  (FTS5 sanitization landed pre-1.0; `tool_calls` audit wiring landed in
  1.3.0) and marked `RESOLVED` in the gap ledger, which had never been
  updated after the CHANGELOG 1.3.0 fix. v1.6 adds an end-to-end FTS5-
  injection regression through the full `recall(explain=True)` path (not
  just the low-level `bm25_search` unit that already covered it).

### Notes

- Six new `TestNeverDeprecateLastCheckpoint` / `TestRecallExplain` /
  `TestWriteScopeNormalization` / `TestSummaryQualityGate` /
  `TestCanonicalProjectKey` / `TestFts5InjectionRegression` classes land in
  the new `tests/test_diagnosability.py` (43 tests), including a direct
  reproduction of the MOTIVATING INCIDENT (a deprecated sole checkpoint +
  fragmented project keys in one store → `recall --explain` surfaces both
  from the result alone).

## [1.5.1] — 2026-06-29

### Fixed

- **`crystalium.commit` no longer hard-fails on a descriptive `provenance.source`.**
  Any harness/LLM that supplied a non-enum source (e.g. `"spectra-planning-session"`)
  previously raised a pydantic `literal_error`, forcing a manual retry. The commit
  handler now coerces an absent/empty/non-enum source to the caller's trust class via
  `source_for_tier(caller_tier)` (T1→`verified_agent`, T2/T3→`unverified_agent`).
  Valid sources pass through byte-for-byte, and a non-T0 caller is **never** coerced to
  `"human"` (preserving forgetting-protection semantics). The descriptive label is
  retained in `author_agent`. Pure server-side coercion — no schema, ECL envelope,
  manifest, or hash change.

### Added

- **Observable coercion advisory.** When `commit` coerces a non-enum `provenance.source`
  or a non-ISO/epoch `created_at`, the success result carries a non-fatal
  `provenance_coercion` advisory (`{field, from, to}`). The clean/identity path attaches
  nothing, so result bytes — and the ECL SHA-256 — stay byte-identical to before.
- **Timestamp tolerance in `commit`.** `provenance.created_at` now accepts epoch
  ints/floats and `Z`-suffixed ISO strings, and safely falls back to server-now on
  malformed input instead of raising — mirroring the existing ingest path.

## [1.5.0] — 2026-06-22

### Added

- **`crystalium.graph_export` — export the scoped memory lattice as a portable
  node-graph (nodes = crystals, edges = typed relationships).** Ships on two
  surfaces sharing one `GraphExporter.export()` core, so their output is
  byte-identical: a new `crystalium export` CLI subcommand (mirrors `recall`)
  and a new `crystalium.graph_export` MCP tool (read-op; auto-inherits the
  ECL v2.0 envelope with SHA-256 integrity and rate-limit enforcement).
- **Rich edge synthesis.** The kuzu graph is near-edgeless by default
  (`LINKS_TO` co-occurrence only), so meaningful edges are synthesized from
  relational state, each tagged with its provenance (`source: kuzu|derived`):
  `LINKS_TO` (kuzu) + derived `SUPERSEDES` (from `temporal.superseded_by`),
  `MERGED_FROM` (from `provenance.merged_authors/merged_sources/corroboration`),
  and `CONFLICTS_WITH` (from the `conflicts` ledger; opt-in `drift_audit`).
  `CITES` is passed through from kuzu when present. The `CAN-GE1` canary proves
  rich-synthesis beats kuzu-only (4 edge types vs 1).
- **Formats.** Canonical JSON `{nodes[], edges[]}` validated against the new
  `schemas/graph-export.v1.json`, plus pure, count-preserving **GraphML** and
  **Cytoscape** adapters (`--format json|graphml|cytoscape`). Obsidian-markdown
  is intentionally out of scope.
- **Safety & bounds.** Visibility/redaction defaults emit **summary-only —
  never raw blob content** (hard invariant), exclude quarantined/deprecated/
  superseded crystals, and respect `agent_class_visibility`; each default is
  overridable by an explicit flag. A 10K-node guard keeps the exporter
  bounded/paginated with a `truncated` flag + `nodes_total_estimate`. Edge
  hygiene drops dangling-endpoint edges, de-dups, drops self-loops, and applies
  deterministic ordering (guarantees CLI⇆MCP parity).
- **New bounded read APIs** backing the exporter: `RelationalStore.list_for_export`
  / `count_for_export` and `GraphStore.all_edges` (with a `_NullGraphStore` stub).
- **Runnable demo** at `examples/graph_export_demo.py` (seeds a small project
  exhibiting all four edge types; `--save-json`/`--save-graphml`/`--save-cytoscape`/
  `--out-dir`), and the decision-ready spec at
  `.spectra/graph-export-v0.1.0-spec.md`. Covered by 8 feature gates
  (`G-GE1`…`G-GE8`) + the `CAN-GE1` canary, with zero regression on the house
  G1–G8 suite.

### Notes

- `MERGED_FROM` currently resolves by author name → every in-scope crystal by
  that author, which can over-connect in dense single-author projects
  (`[GAP-MERGE-RESOLUTION]`, spec §13). This v0.1 behavior is intentional and
  faithful to the spec; a tighter resolution (specific contributing crystal or
  author-proxy nodes) is deferred to a future release.

## [1.4.0] — 2026-06-11

### Fixed

- **`index` CLI crash — `Redactor()` constructed without required `config` argument.**
  `python -m crystalium index <path>` raised `TypeError: Redactor.__init__() missing 1
  required positional argument: 'config'` because `__main__.index` instantiated `Redactor()`
  bare while the constructor requires `config: Config`. The existing index tests passed
  vacuously because they mocked `Redactor`. Fixed by mirroring the `recall` command pattern:
  `Redactor(config=config)`. Regression tests added: a mock-based kwarg assertion
  (`test_index_redactor_receives_config`) and a full CliRunner end-to-end smoke
  (`test_index_single_file_exits_0`).

### Added

- **One-shot `recall` CLI subcommand (GAP-2 — out-of-MCP-session memory pre-flight for
  the Eidolons harness).** `python -m crystalium recall --query TEXT --scope-project TEXT`
  returns a slot-budgeted `RecallResult` as JSON to stdout without requiring a running MCP
  server. Designed for use in a plain-bash SessionStart hook that cannot hold an MCP transport.
  **BM25-only fast path by default** (no torch/lance/kuzu imports on the common path; cold-start
  is seconds, not 30s). `--full` opt-in constructs the full vector+graph arms with the server's
  Null-fallback pattern. **Never writes to the store** (`persist_dynamics=False`,
  `forgetting_fsrs=False`). The enforcement chokepoint at `Aetheryte.recall()` is preserved:
  `caller_tier` passes through `assert_tier_allowed` (recall is universally allowed at all tiers;
  the assertion runs for telemetry). `--format json` (default) emits `RecallResult.model_dump()`;
  `--format text` emits compact `[layer/tier] summary` lines. Exit 0 on success, exit 1 on any
  error (stderr message, no partial JSON on stdout). Flags: `--query` (required),
  `--scope-project` (required), `--scope-visibility`, `--k` (default 10), `--layers` (CSV),
  `--full/--no-full`, `--format`, `--config`.

## [1.3.0] — 2026-06-04

### Changed (T2 — earn the OFF flags)

- **W3 Dream — re-examined, honest null confirmed; `dream_replay_evb` / `dream_interleave`
  / `dream_stc` all stay OFF.** The baseline still ties exactly (consolidation_gain 1/1,
  drift 0/0, STC retention 1.0/1.0) — the v0.1 `_gather` collapses seeds + graph
  neighbours into a single mixed cluster, so consolidation count is ~1 regardless of
  replay ordering or STC. Two routes to discrimination were identified, both beyond a
  fixture tweak: (1) a `_gather` per-topic-cluster refinement (a production Dream-worker
  change); (2) the ledger's interleaved-multi-task **backward-transfer** harness (the
  `forgetting`/`backward_transfer` R-matrix functions exist in metrics.py but no
  workload drives them). Per ablation-as-arbiter, no consolidation-count win is
  manufactured on the coarse fixture — the flags stay OFF until one harness shows a
  confound-free win. BENCH-NOTES §W3 updated with both paths.

- **W5(i) pattern completion earned ON — `recall_completion` default flipped to `True`;
  `recall_context_match` stays OFF.** The `retrieval_gate` corpus now adds 24
  lexically-close distractors (share query words, not relevant, not graph-linked) so
  flat dense recall fills its top-k *without* the graph-distant spokes — creating the
  "missed-by-similarity but reachable-by-graph" gap the 7-crystal fixture couldn't.
  Result: the decaying multi-hop walk recovers the missed 2-hop spoke → **multihop
  recall 0.67 → 1.0, F1 0.12 → 0.18** (graph store confirmed real, not the null stub).
  A genuine graph-reachability win → `recall_completion` ON; full suite green with the
  flip (661 passed). `recall_context_match` shows **no rank lift** (the context crystal
  already ranks first in both arms) → stays OFF (honest null). BENCH-NOTES §W5(i)
  updated; guard tests `test_retrieval_gate.py`.

- **W4 FSRS forgetting — discriminating workload built, honest null, `forgetting_fsrs` stays OFF.**
  The `forgetting_gate` was rebuilt to the ledger's prescription (60 ticks, sustained
  noise 4/tick, prune-every-tick) PLUS the keystone is now recalled only every 8th
  tick — the value×recency discriminator that should make pure-recency LRU drop it
  while FSRS's spaced-repetition stability keeps it. Noise is seeded by index (not
  uuid) so the memory axes are reproducible; the gate now requires a *meaningful*
  (≥10%) plateau margin, not a noise-level strict-`<`. Result: **FSRS does not beat
  LRU** — both arms retain the keystone at an 8-tick gap (LRU's accumulated access
  keeps it above the prune threshold) and the plateau is identical (1.379 both). An
  honest null, confirmed not for lack of trying — a win would need params engineered
  to make LRU drop the keystone (manufacturing). BENCH-NOTES §W4 updated; guard tests
  `test_forgetting_gate.py`.

- **W5 predictive prefetch — confound fixed, honest null, `recall_prefetch` stays OFF.**
  The `prefetch_gate` now predicts the next query with an **imperfect** first-order
  rotation model (prediction accuracy 0.73 < 1.0) instead of handing the checkpoint
  the verbatim future query — closing the fabricated-perfect-prediction confound the
  ledger flagged (a `gate_pass` guard now requires accuracy < 1.0). With that fixed,
  a **deeper** confound surfaced: the OFF arm has *no recall cache at all*, so the
  p95 win is cache-vs-no-cache (ordinary cache-warming of repeated queries), not
  isolated protention. The gate encodes this (`protention_isolated` → False →
  `gate_pass` False); `recall_prefetch` stays OFF until a cache-on/prefetch-off
  baseline can credit protention. An honest null, not a flip. BENCH-NOTES §W5(iii)
  updated; guard tests `test_prefetch_gate.py`.

- **W2 EVB earned ON — `evb_enabled` default flipped to `True`.** A *discriminating*
  ablation gate (`evals/evb_gate.py`) now decides on **retained-set purity**
  (`retention_precision`) under a no-high-value-regression guard — the axis EVB's
  multiplicative `gain×need` actually moves. The original criterion (promotion
  precision AND high-value retention) **saturated at 1.0 in both arms** and could
  never discriminate (the post-1.0 ledger's "INCONCLUSIVE"). Result: EVB
  `retention_precision` **1.0 vs legacy 0.33** (legacy retains high-need/zero-gain
  distractors + unscored-old; EVB keeps only genuine value) with **high-value
  retention tied at 1.0** (no recall cost). Full suite green with the flip — no
  production coupling. DESIGN-RATIONALE §D6.1 + BENCH-NOTES W2 updated; gate
  regression test `test_run_returns_evb_wins_on_retention_purity`.

### Fixed

- **kuzu graph store: bound the per-database virtual reservation (1 GiB default,
  env-tunable) instead of kuzu's ~8 TB default `max_db_size`.** kuzu mmaps
  `max_db_size` of virtual address space up front; in a constrained CI container
  (memory cgroup / `RLIMIT_AS`) that 8 TB mmap fails ("Buffer manager exception:
  Mmap for size 8796093022208 failed"). It only surfaced once the graph is actually
  *queried* — which `recall_completion` (earned ON in T2) now does on every recall —
  so `test_rtbf` started failing in CI though it passed locally. `GraphStore` now
  opens `kuzu.Database(..., buffer_pool_size=256 MiB, max_db_size=1 GiB)` (both via
  `CRYSTALIUM_KUZU_BUFFER_POOL` / `CRYSTALIUM_KUZU_MAX_DB_SIZE`), with a `TypeError`
  fallback for older kuzu. Graph ops + completion unchanged (21 graph/rtbf tests pass).

### Fixed (T1 correctness — three real behavior gaps)

- **G1.1 — `semantic.update()` re-embeds the new revision into the vector store.**
  Before this fix, the semantic layer's `update()` method inserted the new revision
  into the relational/FTS index but never called `vector_store.upsert()` — identical
  to the episodic-fallback gap closed in a prior battle-test sweep. With
  `recall_active_only=True`, the superseded original is excluded from recall, so an
  updated semantic fact silently vanished from the dense recall arm. The fix mirrors
  the `commit()` embed+upsert pattern (best-effort; null/SKIP_SLOW embedder → no-op).
  Regression test: `test_semantic_update_reembeds_new_revision`.

- **G1.2 — `bm25_search` FTS5 query sanitization (already in place; fuzz test added).**
  The `_fts5_query()` sanitizer that prevents `OperationalError` on queries containing
  `:`, `-`, `*`, `"`, `AND`, etc. was already implemented (`test_bm25_special_chars_no_crash`
  covers it). The gap ledger entry is closed by confirming the existing tests pass.

- **G1.3 — `tool_calls` audit table is now populated by `record_call()`.**
  `record_tool_call()` had zero callers; `DreamWorker._orient()` queried a table
  nothing ever wrote. Fixed by wiring `telemetry.record_call()` to the relational
  store via `register_relational_store()` (called from `_build_server()`). Every MCP
  tool call now writes one audit row — `_orient()` reads real recall counts.
  Best-effort: a DB write failure never propagates to the caller.
  Regression tests: `test_tool_calls_populated_via_telemetry`,
  `test_tool_calls_readable_by_orient`.

- **Canary reproducibility — `_build_live_handlers` isolates each run in a fresh
  ephemeral `data_dir`** (the real resolution of the ledger's "G1.1 blocks the
  canary 0.80"). The headline A/B (`make bench`) shared the persistent
  `~/.crystalium/default` store (the mounted `crystalium_data` volume) across
  runs, so cross-run `write_dedup_merge` merged new writes into prior runs'
  crystals and defeated the per-mission scope filter, collapsing the headline to
  `pass_rate_on=0.25`. **This was a test-harness confound, not a production
  re-index gap:** the update path already re-embeds, so on a clean store the
  canary scores `pass_rate_on=1.0` (beats off `0.0`, CAN-4 passes) on **both
  `main` and this branch**. Each canary run now gets its own store (an explicit
  `data_dir` override opts out), making `make bench` deterministic without a
  manual `docker compose down -v`. Regression test:
  `test_canary_run_uses_fresh_ephemeral_data_dir`.

## [1.2.1] — 2026-06-02

### Fixed

- **Multi-arch image — the published GHCR image now includes `linux/arm64`** (was `linux/amd64`-only). Apple Silicon hosts could not `docker pull` it ("no matching manifest for linux/arm64/v8"), which broke `eidolons mcp install crystalium` on arm64. The release workflow now builds each platform on its **native runner** (`ubuntu-latest` for amd64, `ubuntu-24.04-arm` for arm64), pushes each by digest, and assembles a manifest list — avoiding slow/flaky qemu cross-builds for the ML-heavy CPU image. No code change; the nexus roster re-pins the new multi-arch index digest.

## [1.2.0] — 2026-06-01

### Added

- **Env-var caller identity (`CRYSTALIUM_CALLER_EIDOLON` / `CRYSTALIUM_CALLER_TIER`).** All six
  Eidolons share one MCP server process; identity is correctly a process-level env var. Setting
  `CRYSTALIUM_CALLER_EIDOLON=atlas` (or any roster member) resolves to tier T1, enabling writes
  to the semantic/execution layers and `plan_checkpoint`/`plan_replan` that were previously
  blocked under the T2 default. `CRYSTALIUM_CALLER_TIER` allows an explicit tier override.
  Both follow the MIN-trust rule: `final = max(declared_tier, identity_tier)` so a low-trust
  identity cannot be self-elevated via an explicit override.
  The two env vars are documented in `config.py` alongside the other `CRYSTALIUM_*` vars.
  The ingest path (`crystalium.ingest`) is unaffected — it calls `resolve_caller_tier(envelope)`
  independently from `ingest_adapter` and never consults the process env, so a T3-origin
  envelope cannot be laundered upward by the process identity.
  Falls back to T2 when neither env var is set (D4 backward-compatible default preserved).

## [1.1.0] — 2026-06-01

### Added

- **CPU/GPU build variants.** `ARG TORCH_VARIANT` (`make build VARIANT=gpu`) selects the
  torch wheel. CPU is the default and the only published image; GPU (CUDA cu121, amd64-only)
  is buildable-only, for hosts that do bulk re-embedding.

### Changed

- **Container image slimmed ~4.5×: 8.9 GB → 1.97 GB** (published runtime) / 2.13 GB (dev).
  `torch` is now a direct dependency pinned to PyTorch's CPU index, dropping the ~4.4 GB
  NVIDIA CUDA stack (`nvidia/*` + `triton`) that the single-text `sentence-transformers`
  embedding workload never used. The published `ghcr.io/rynaro/crystalium:latest` is
  CPU-only and runtime-only — the dev toolchain (pytest/mypy/ruff/jsonschema) is split into
  the `dev` image stage and no longer shipped to consumers.
- Runtime entrypoint is `uv run --no-sync`: the container runs the venv baked at build time
  instead of re-resolving (and re-pulling the CUDA torch wheel) on every start. Dependency
  changes now require an explicit `uv sync`.

### Fixed

- `docker compose run` / `make test` work without manual flags: the compose file declares an
  anonymous `/app/.venv` volume (un-shadows the baked venv under the source bind-mount) and
  sets `PYTHONPATH=/app/mcp-server/src:/app` (so the `evals`-importing tests collect).

## [1.0.0] — 2026-05-31

### Added

- Conformance suite: a `conformance` pytest marker over all 8 G-gates + mechanical
  invariants (`pytest -m conformance` == "green is conformant") + a blocking CI job +
  a gate-registry self-check; working-set cap pinned to the literal 3500.
- Availability SLO: recall availability (success/attempts) metric + the W1 latency
  panel now reports it (target ≥99% availability, recall p95 <200 ms).
- `MIGRATION.md` (per-wave config-key delta, schema-v1-stable, the one behavior change)
  and `docs/roster-pr.md` (drafted nexus roster entry, operator-opened).
- DESIGN-RATIONALE D6.6 (W7 Extended Mind) + D6.7 consolidated 8-result ablation table
  + marker legend.

### Changed

- **Default ON (recorded A/B wins):** `write_dedup_merge` (W5) and `recall_active_only`
  (W6). All other augment flags stay OFF (honest nulls).
- Canary honestly repaired (de-vacuumed off-arm, episodic + isolated missions,
  single-run headline, restated gate): memory-on beats memory-off **+0.75** (was −0.75).
- Version 0.8.0 → 1.0.0.

### Fixed

- `Config.from_env()` defaulted `write_dedup_merge` / `recall_active_only` to False,
  contradicting the dataclass True — env-built configs silently reverted the flips.
  Reconciled (both default True; guarded by a default-parity test).
- Canary harness bit-rot (`_get_crystal`/`_row_count` read a non-existent
  `enforcement._store`) and the `run_all` double-run (headline computed from a
  different execution than the displayed results).
- install manifest now validates against `install.manifest.v1.json` (`ecl_version`,
  role `schema`, schema extended for `profile`/`roster`/`scope`).

### Known limitations

- Canary below the 0.80 bar by one mission (recall-after-bi-temporal-update re-index
  `[GAP]`); recall p95 ~205 ms marginally over the 200 ms embedder-bound target. Both
  `[PROXY]` (synthetic harness). See `evals/BENCH-NOTES.md`.

## [0.8.0] — 2026-05-31 — Wave 7: Eidolons Integration

### Added

- `crystalium.ingest` (8th MCP tool): ingest a roster ECL handoff envelope (v1.x/v2.x)
  → `crystal.v1` via a generic adapter, preserving the native artifact verbatim in
  `encoding_context` and committing through the chokepoint (MIN trust tier preserved;
  T3 → episodic-quarantined, never laundered).
- EIIS v1.4 finalization: install `--version`/`--manifest-only`/`--hosts`/`--members`;
  AGENTS.md YAML frontmatter (`version`, `handoffs.upstream/downstream`, ECL/EIIS pins);
  host `serve` wiring + repo `.mcp.json` self-wire; standalone + 2-member verified.

## [0.7.0] — 2026-05-30 — Wave 6: Security & Integrity Hardening

### Added

- Belief-drift detection (`drift_detect`, OFF), quarantine triage CLI (T0, audited,
  reject = soft-deprecate), write-conflict detection (`write_conflict_detect`, OFF),
  and `recall_active_only` (**ON** — excludes deprecated/superseded from recall;
  poisoning ASR 1.00→0.00). Three append-only audit ledgers.

## [0.6.0] — 2026-05-29 — Wave 5: Retrieval Intelligence (Aetheryte II)

### Added

- Pattern completion (`recall_completion`, OFF), encoding-specificity re-rank
  (`recall_context_match`, OFF), pattern-separation dedup-merge (`write_dedup_merge`,
  **ON** — write amp 1.0→0.667), predictive prefetch (`recall_prefetch`, OFF).

## [0.5.0] — 2026-05-29 — Wave 4: Forgetting as a Faculty

### Added

- FSRS/DSR forgetting (`forgetting_fsrs`, OFF), value-aware eviction, spaced
  re-surfacing, Ricoeur-protected class, and the right-to-be-forgotten operator op
  (`crystalium forget`, T0, audited — the one sanctioned hard-delete).

## [0.4.0] — 2026-05-28 — Wave 3: The Dream Becomes Intelligent

### Added

- Prioritized replay (`dream_replay_evb`, OFF), CLS interleaving (`dream_interleave`,
  OFF), synaptic-tagging consolidation (`dream_stc`, OFF).

## [0.3.0] — 2026-05-28 — Wave 2: Importance as Expected Value of Backup

### Added

- EVB importance scorer (`evb_enabled`, OFF; Gain×Need, Mattar & Daw 2018) + the
  `memory_dynamics` persistence column.

## [0.2.0] — 2026-05-28 — Wave 1: Foundations & Eval Spine

### Added

- Container-first PreToolUse hook, the `/prepush` command, the evals/canary spine, and
  the `memory_dynamics` schema field.

## [0.1.0] — 2026-05-28

### Added

- Initial implementation of the four-layer memory harness (Episodic, Semantic,
  Procedural, Execution) with one mechanical write-gate chokepoint
  (`enforcement.py`).
- MCP server (stdio JSON-RPC 2.0) exposing 7 tools: `recall`, `commit`,
  `update`, `skill_invoke`, `plan_checkpoint`, `plan_replan`, `session_end`.
- Storage adapters: SQLite + FTS5 (relational + sparse indexing), LanceDB
  (vector), KuzuDB (graph), content-addressed filesystem blob tier.
- Aetheryte hybrid recall surface: BM25 ⊕ vector ⊕ graph retrieval with
  optional reranking when k > 20.
- ECL v2.0 envelope sidecar emission on every tool result (11 required fields,
  SHA-256 integrity via `hashlib`).
- EIIS v1.4-conformant `install.sh` with canonical inventory whitelist sweep
  (Appendix A reference implementation).
- Dream consolidation worker (async, `apscheduler`-backed) with dual-trigger
  (idle-poll every 60s + explicit `session_end` tool call).
- Bounded slotted working-set composer: enforces ≤3,500 tokens across six
  typed slots (executive 300, procedural 600, semantic 800, episodic 800,
  execution 1000, buffer 300). Deterministic eviction by importance score.
- Tier × Layer × Operation matrix (§4): 12 rows × 4 tiers; guards admission
  per trust tier and target layer. Prevents T3 pollution, blocks multi-agent
  poisoning via MIN-tier propagation rule.
- 10-mission canary suite with memory-on/off A/B harness. Headline metric:
  memory-on beats memory-off on ≥80% of canaries.
- 8 P0 conformance gates (G1–G8) with `test_anchor` paths in
  `test_enforcement.py`, `test_skill_invoke.py`, `test_composer.py`,
  `test_dream_scheduler.py`, `test_ecl_envelope.py`, `test_trust_propagation.py`,
  `test_promotion_gate.py`.
- Container-first architecture: all Python toolchain (uv, pytest, embeddings,
  storage engines) runs inside `docker compose service crystalium`. Host runs
  only `docker compose`, `git`, `make`.
- Redaction layer: regex pre-pass + small-LLM judge for sensitivity-tagged
  content. Re-applied at every cross-agent handoff (ECL envelope).
- Operator CLI: `crystalium promote list` / `crystalium promote review <id>
  [--accept|--reject]` for Semantic promotion inbox.
- Importance function (D6): `importance_score(record, *, now) -> float` with
  frozen signature; weights tuple externally tunable (entry point for v0.2
  adaptive learning, D11).
- Bi-temporal update primitive: `crystalium.update(id, patch, reason)`
  invalidates-old, writes-new with `superseded_by` link. Never hard-delete.

### Out of scope (v0.1.0 — hooks left, not built)

- Polyglot skill abstraction (`language` + `capability_class` fields
  reserved in `skill.v1.json`; raised by v0.2).
- Adaptive/learned importance weights (`WEIGHTS` tuple is swap point; D11
  deferred).
- Belief-drift detection (`provenance` field on every crystal + audit log
  populated; analysis layer deferred).
- Quarantine review UI (`validation_state: quarantined` field reserved;
  `crystalium promote` CLI can enumerate, v0.2 adds UI).
- Server profile (Postgres/Qdrant/Neo4j) and LangGraph adapter (`config.profile`
  field raises `NotImplementedError("v0.2")` on `"server"`; local-only in v0.1).
- In-weights consolidation / LoRA fine-tuning (eviction is highest-importance-first
  only; no gradient-based consolidation).
- Multi-agent CRDT/consensus (append-only + content-addressed + last-write-wins
  with `superseded_by` sufficient for v0.1; CRDT complexity deferred).
- REM-style associative linkage (graph tier exists for fact retrieval; optimal
  link learning deferred).
- Streamable-HTTP transport (`CRYSTALIUM_TRANSPORT=http` raises
  `NotImplementedError("v0.2")`; stdio only).
- Nexus roster entry (blocked by `capability_class` enum closure;
  standalone repo in v0.1, roster integration in v0.2+).

### Quality metrics

- `agent.md`: ≤1,000 tokens (verified by CI).
- Composer: ≤3,500 tokens (G6 invariant).
- Test coverage: G1–G8 all passing; canary suite ≥0.80 A/B pass rate.
- `install.sh` idempotency: second-run produces identical install target
  (CI job enforces).
- DESIGN-RATIONALE.md: ≥10 citations (anchor list from MISSION.md), [UNVERIFIED]
  markers on unverifiable claims.
- EIIS v1.4 conformance: source-repo has all 6 required files; install target
  whitelist enforced; `agent.md` + `SPEC.md` dual-write recorded in
  `install.manifest.json`.
- ECL v2.0 conformance: every tool result emits envelope with 11 required
  fields; `integrity.value` matches `sha256(payload_bytes)`.

### Known limitations

- Verifier sandbox is soft (subprocess inside container, not DinD or microVM).
  OS-level isolation (DevContainer, Firecracker) is operator's responsibility.
- Offline consolidation (Dream) cannot perform gradient-based learning; it proposes
  (via clarifying LLM call), never auto-learns. Weights remain static.
- Importance `novelty_at_write` is frozen at write time; not recomputed as
  neighbourhood shifts (OQ-9).
- k=3 corroboration may be hard to achieve in single-operator or single-Eidolon
  workflows (OQ-5).
- `force_promote` (T0 only) writes straight through (no inbox); audit lives in
  telemetry (OQ-1).

---

**Starting from v0.1.0, CRYSTALIUM is versioned according to
[SemVer 2.0.0](https://semver.org/spec/v2.0.0.html). Breaking changes will bump
MAJOR; new backwards-compatible features will bump MINOR; bugfixes will bump
PATCH.**
