# Changelog

All notable changes to CRYSTALIUM are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows
[SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Containers no longer write into the bind-mounted source tree as root (#66).**
  `docker-compose.yml` mounts `.:/app` read-write and carried no `user:` key, while the
  Dockerfile adds no `USER` (so `python:3.12-slim` runs as root). Every `docker compose run`
  therefore left `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` and `.venv` on the host owned
  by uid 0 — undeletable by the developer who invoked `make`, and enough to make
  `git worktree remove` fail partway through and leave a deregistered orphan. Measured: 297
  root-owned paths in a normal checkout, and 2,353 across 96M of campaign worktrees that could
  not be reclaimed without `sudo`. The service now runs as the invoking user
  (`user: "${DOCKER_UID:-1000}:${DOCKER_GID:-1000}"`, exported by `make`).

  Two consequences of running non-root, both handled:
  - **The data dir moved off `/root`.** `/root` is mode 0700, so a non-root uid could not open
    SQLite/LanceDB/Kuzu there. The dev volume now mounts at `/data`
    (`CRYSTALIUM_DATA_DIR=/data/default`), and the dev image pre-creates `/data` as 1777 so a
    fresh volume is writable by any host uid rather than only 1000.
  - **`HOME=/tmp`.** `uv` initialises a cache under `$HOME` on startup; as a non-root uid `HOME`
    defaulted to `/` and `uv` died with `Failed to initialize cache at /.cache/uv` before the
    entrypoint reached Python.

  **The published image is deliberately unchanged.** It is built from `target: base`, still runs
  as root, and still resolves its data dir under `$HOME` — so existing MCP wiring that
  bind-mounts a host directory onto `/root/.crystalium/<project>` keeps working. A `--user` pin
  belongs on that `docker run` invocation, where the bind mount already carries host ownership.

### Added
- **`make check-ownership`** — asserts that everything the container wrote into the working tree
  is deletable by the host user, wired into CI as its own job. Every other CI job runs
  `docker run` against the baked image with no bind mount, so none of them could observe this
  defect. Gates on *removability* rather than raw ownership: POSIX removal permission comes from
  the parent directory's write bit, and Docker creates the `/app/.venv` anonymous-volume
  mountpoint as root regardless of `user:` — but that is an empty directory in a
  developer-owned parent, so `rmdir` clears it. Red-checked: removing the `user:` key makes this
  fail with 22 undeletable paths.
- **`scripts/check-ownership.sh`** — the removability rule, extracted from the Makefile after an
  independent checker found the inline one-liner had a silent blind spot. It flagged a path only
  when that path's *own* parent was unwritable — the right unlink condition for a file, but
  incomplete for a directory. A root-owned mode-0700 directory with content, in a host-owned
  parent, passed the gate while `rm -rf` on it genuinely failed: `find` could not descend into
  it, and the traversal error went straight into the `2>/dev/null` the check itself installed, so
  its children were never enumerated. Mode-0700 directories are routine output of
  `tempfile.mkdtemp`, `.ssh`, `.gnupg` and assorted caches, so this was not a corner case.

  The rule is now stated properly for both kinds — a path is stuck unless its parent is writable
  **and**, if it is a directory, the host user can actually clear it (readable + traversable +
  writable, or genuinely empty). Unreadable directories fail closed, since `ls` on one returns
  nothing and would otherwise read as "empty". `find`'s stderr is captured rather than discarded
  and any traversal failure is itself a finding. Stating the condition properly also caught a
  case the checker did not report: a root-owned **0755** non-empty directory — readable and
  traversable, so `find` enumerates it fine, but not writable, so its children cannot be
  unlinked. Validated by a 6-case fixture rather than by re-running the single counter-example,
  which shows the fix does not over-fire: the `.venv` mountpoint still passes with **no**
  exception entry.
- **`make fix-ownership`** — one-time migration for checkouts predating this change, using a
  throwaway root container to `chown -h` the tree back to the invoking user (`-h` because
  `.venv/bin/python` and `.venv/lib64` are symlinks a plain `chown` would follow). Verified: 297
  → 0 on a real checkout.

### Changed
- The dev data volume is now `crystalium_data_v2`. The old `crystalium_data` was populated by
  root and would fail every write under the non-root user, so a new name gives a
  correctly-owned store on first run instead of a confusing crash. **This discards local dev
  crystals** — scratch state for the dev container, never the MCP server's real data. The old
  volume is not deleted; drop it with `docker volume rm crystalium_data`.

### Notes
- Running non-root revives **two tests that could never fail as root**:
  `test_doctor_readonly_data_dir_nonzero` and `test_doctor_fail_shows_fail_marker` self-skipped
  with *"Running as root; chmod 0o444 does not prevent writes for root"*. They exist to verify
  `doctor` reports failure on an unwritable data dir — precisely the property root cannot
  exercise. Measured in one checkout varying only the container user, so the attribution carries
  no confound: **1095 passed / 6 skipped as root → 1097 passed / 4 skipped as the host user**,
  a difference of exactly these two tests. (A first comparison against a fresh clone suggested
  four; two of those were `test_roundtrip_handoff` skipping on *"roster fixtures not mounted"*,
  an artifact of the clone rather than of the user change.)

## [2.1.0] — 2026-08-06

Recall **result order and membership change by design.** Minor, not patch: no tool rename, no
schema change, no `isError` semantics change, no removed field — but a client issuing the same
query gets a differently-ordered (and, on the deprecated-heavy path, differently-populated)
result set.

### Fixed
- **Cross-layer rank blocking (#45).** The sparse and dense arms fetched per layer and appended
  layer-major, so a record that was the best BM25 match *globally* could only reach rank
  `n_layers`. Replaced with a score-space merge: one global `bm25_search(layer_filter=None)` for
  the all-layers case, the existing filtered call for a single layer, and global+post-filter with
  a per-layer starvation backstop for a strict subset. Measured on the #52 gate: the semantic
  target moves from fused rank 3 to rank 0 while remaining global BM25 rank 0 throughout.
  `bm25_search`'s signature and SQL are untouched (AC-349 verified empty diff).
- **Status-blind sparse candidate set (#44).** Deprecated rows consumed fetch slots and starved
  active hits — silently, with no error, log line or explain anomaly. This is live at default
  deployment: `config.py` defaults `recall_active_only=True` and both entrypoints pass it
  through. Each *individually* censored-and-dirty fetch now widens at most once, caller-side,
  with the censoring signal recomputed against the fetch actually performed. Widening only the
  global head was rejected — measured RED on the strict-subset path, where it would have
  reported `fired: true` with recovery structurally absent.
- **Seed exclusion is now a policy, not an accident (#42).** `neighbor_expand` and
  `decaying_walk` take `exclude_seeds: bool = True`, threaded through all five sites that
  enforce it (including `hop_ids -= original_seeds`, added by #41 to exclude seeds at *every*
  hop, which made a two-site fix a no-op at depth 1). Default `True` is byte-identical to
  v2.0.2 on all three oracle topologies.

### Added
- `explain.fusion.sparse_topup {fired, k_initial, k_final, n_inactive_observed}`, plus per-fetch
  raw counts and `sparse_fetch_shape` — derived from the fetch actually performed, so the
  counter cannot stay truthful after the code it describes is removed.
- `Config.recall_seed_derived_credit` (**default `False`**) and
  `CRYSTALIUM_RECALL_SEED_DERIVED_CREDIT`.

### Not claimed
- **The seed-exclusion relaxation ships OFF by default.** STOP S-1 has two triggers: DP-1(b) P1
  re-creation (cleared — `p1_recreated: false` at `w=1.0`, with a `w=100.0` positive control
  returning `true`, so the instrument works), and "relaxation regresses multi-hop F1" — which
  was **never measured**. The available fixture returned identical output with the flag on and
  off only because its topology has no seed-to-seed edge, so it could not have detected a
  regression either way. That run is not evidence and must not be cited as such. Any PR flipping
  this default must attach a flag-on/off multi-hop measurement from production traces or a real
  corpus.
- No claim of improved retrieval **quality**. #45 changes ordering to match BM25 score; whether
  that is better on real corpora is not measured here.

## [2.0.2] — 2026-08-06

Falsifiability batch. **Zero production-behaviour change** — tests, evals and comments only;
the sole `mcp-server/src/` diff is comment-only text in `config.py`.

### Added
- `test_server_entrypoint.py` — drives `python -m crystalium serve` as a real subprocess over
  stdio (`initialize` → `notifications/initialized` → `tools/list`) and asserts a clean exit.
  Closes the gap where a server that crashed instantly on `serve` passed the whole suite
  (972/976 green at v1.12.0). Not `slow`-marked, so it protects CI. (#57)
- `evals/_corpus_rig.py` — shared deterministic corpus rig: crystal minting, store seeding,
  stub dense arm, an all-flags-explicit `Aetheryte` harness, arm-liveness self-checks and a
  pure verdict classifier that emits no numbers when an axis is confounded.
- `evals/cross_layer_gate.py` — measures cross-layer rank blocking through `Aetheryte.recall`.
  Ships RED as a strict xfail: a semantic record that is the best BM25 match GLOBALLY fuses at
  rank 3 behind three episodic fillers. Self-enforcing — the #45 fix turns it XPASS. (#52)
- `evals/corpus_scaling_gate.py` — shows `candidate_k` truncation dropping a planted record once
  the corpus exceeds it, with `len(sparse_ranking) == candidate_k` asserted so the gate proves
  the fetch was really censored. (#47)
- `evals/weight_discrimination.py` — weight-discriminating fixture; the DP-1(b) re-check oracle
  for #42, explicitly NOT a band characterisation. (#55)
- `evals/floor_sensitivity_gate.py` — floor-sensitivity gate plus the VP-M1 probe. (#48)
- CLI registration for the four new gates.

### Changed
- `evals/fusion_gate.py`: the `cross_layer` axis is renamed `sparse_arm_per_layer_probe`, because
  it measured `bm25_search` directly and never reached `Aetheryte.recall` — both values were
  pinned at 0 and it could not fail. The AC-125 fixture is byte-identical. (#52)
- Two pre-#41 docstring claims that the `FETCH_WIDTH_FLOOR` channel is "LIVE and MEASURED" are
  marked HISTORICAL rather than deleted. They were written 2026-08-03, one day before #41 landed,
  and describe the single-seed abort lottery that #41 removed. (#48)

### Fixed
- `config.py` records that sub-1.0 `fusion_weight_derived` values are legal, **unsupported**, and
  will remain uncharacterised until a fixture with non-stipulated ground truth exists; plus a
  forward obligation to build a d2-identity harness before any future combiner-arithmetic
  change. (#55)

### Notes on what is NOT claimed
- No retrieval **quality** improvement. Every gate here is a falsifiability instrument on a
  stipulated fixture; none measures quality on a real corpus.
- No `candidate_k` scaling law is validated (#47) and the sub-1.0 weight band is not
  characterised (#55).
- #48's floor gate is a **regression guard and existence proof**, not evidence that
  `FETCH_WIDTH_FLOOR` matters in practice.

## [2.0.1] — 2026-08-05

### Fixed

- **#35 fix-forward** — a post-release independent check caught two
  defects the #35 tool-rename left behind in v2.0.0:

  1. **Telemetry double-write on `recall`.** `Aetheryte.recall()`
     (`aetheryte/retrieve.py`) recorded its own telemetry row in a
     `finally:` block that fired on every call — success AND failure —
     under the pre-#35 dotted tool name (`"crystalium.recall"`), in
     addition to `server.py`'s `_call_tool` dispatcher recording the SAME
     call under the canonical name (`"recall"`) the manifest was renamed
     to. Every recall therefore wrote TWO rows to `tool_calls`: one
     stale-keyed duplicate, one canonical. The inner write carried no
     information the outer one lacks for this tool (`op="recall"` is
     identical to the tool name, and `layer=None` matches the outer's
     `layer_hint` for recall in every case), so it was removed rather than
     repointed.

  2. **`DreamWorker._orient()` read the stale key.** Its `total_recalls`
     query still filtered `tool_calls WHERE tool='crystalium.recall'` — the
     same pre-#35 name. It only appeared to "work" because the double-write
     above kept that stale bucket alive as an orphaned duplicate stream;
     `_orient()` was silently counting the wrong stream, not the canonical
     per-`tools/call` one. Repointed to `telemetry.RECALL_TOOL` (imported,
     not repeated as a literal) via a parameterized query.

  A sweep of `mcp-server/src/` for the same class of stale
  `"crystalium.<tool>"` literal feeding a telemetry write turned up five
  more layer-adapter call sites left over from #35 (each layer adapter
  records its own telemetry in a `finally:` block, independently of
  `server.py`'s dispatcher — a pre-existing pattern, not new in #35):

  - `layers/episodic.py` `commit()` and `layers/procedural.py` `commit()`:
    same shape as the recall fix — the inner write was 100% redundant with
    the outer dispatcher's (the layer argument is dispatcher-required, so
    `layer_hint` always agrees; `op` always equals the tool name for these
    two). **Removed.**
  - `layers/procedural.py` `invoke()` (`skill_invoke`), `layers/semantic.py`
    `commit()` and `update()`, `layers/execution.py` `checkpoint()`
    (`plan_checkpoint`) and `replan()` (`plan_replan`): the stale dotted
    literal was **repointed** to the canonical tool name, but the write
    itself was **kept** — each of these carries a field the outer
    dispatcher's write genuinely lacks and cannot reconstruct: a failed
    verifier or promotion-gate rejection sets `outcome="rejected"` /
    `"pending"` without raising an exception (the outer dispatcher
    hardcodes `result="ok"` for any non-raising return, so it can never see
    this), `update`'s caller-supplied schema has no `layer` field at all
    (the outer's `layer_hint` is always `None` for it — only the inner
    write records the real target layer), and `plan_checkpoint`/
    `plan_replan`'s `op="commit"` (the D1 tier-matrix bucket) genuinely
    differs from the tool name, a distinction the outer dispatcher never
    records for any tool. These five sites are still a duplicate WRITE
    (one extra row per call) but not a duplicate of INFORMATION — deleting
    them would have dropped real audit-trail signal, not just de-duped a
    row.

  A `server.py`-level double-write was also found during the sweep — its
  `_call_tool` exception handler calls both `enforcement.record(...)` and
  `record_call(...)` back-to-back for every tool's error path — but it
  predates #35 by several releases, affects the outer dispatcher itself
  rather than a stale tool-name literal, and is left for its own dedicated
  fix-forward.

- Added a regression test (`test_server.py`
  `test_recall_writes_exactly_one_tool_calls_row`) driving a real recall
  through the dispatcher and asserting exactly one `tool_calls` row keyed
  under `telemetry.RECALL_TOOL`, plus
  `test_dream_orient_counts_recalls_via_canonical_key` proving
  `DreamWorker._orient()` now counts that same canonical stream.
  Red-checked against the pre-fix code (2 rows / stale-key miscount) before
  the fix landed.
- Added a parametrized test (`test_server.py`
  `test_schema_violation_is_error_with_no_side_effect`) covering the
  hand-rolled `jsonschema` input validation added in v1.12.0, which had no
  coverage in `mcp-server/tests/` (only in an external script CI never
  runs). Derives the tool list from `build_tool_manifest()` — not a
  hardcoded list — and for each tool asserts a schema-violating call sets
  `isError=True`, its message starts with `"Input validation error:"`, and
  it has no side effect (no `tool_calls` row, no `runs/` artifact).

## [2.0.0] — 2026-08-05 — **BREAKING**

### Changed

- **#35, #33** renamed all 9 advertised MCP tool names from dotted
  (`crystalium.<tool>`) to single-segment (`<tool>`). MCP tool names cannot
  contain `.`, so the host sanitises `.`->`_` AND namespaces the server,
  producing the double-prefixed `mcp__crystalium__crystalium_recall` — a
  caller reaching for the intuitive `mcp__crystalium__recall` got
  "No such tool available" (root cause of #33). Every sibling MCP
  (`mcp__tonberry__list`, `mcp__atomos__compose_handoff`, ...) already used
  single-segment names; crystalium was the lone outlier.

  | Old (v1.x)                     | New (v2.0.0)       |
  |---------------------------------|---------------------|
  | `crystalium.recall`             | `recall`             |
  | `crystalium.commit`             | `commit`             |
  | `crystalium.ingest`             | `ingest`             |
  | `crystalium.update`             | `update`             |
  | `crystalium.skill_invoke`       | `skill_invoke`       |
  | `crystalium.plan_checkpoint`    | `plan_checkpoint`    |
  | `crystalium.plan_replan`        | `plan_replan`        |
  | `crystalium.session_end`        | `session_end`        |
  | `crystalium.graph_export`       | `graph_export`       |

  **Migration:** glob grants (`mcp__crystalium__*`) are rename-transparent
  and keep working unchanged. Only callers pinned to the explicit
  double-prefixed wire name (`mcp__crystalium__crystalium_recall`, etc.)
  are affected — those callers should re-list tools and pick up the new
  advertised (single-prefixed) name, `mcp__crystalium__recall`.

- Added a `tools/call` dispatch alias (Option B deprecation cushion): a
  caller still sending the pre-rename dotted name (`crystalium.recall`) or
  the double-prefix-collapsed name (`crystalium_recall`) is still routed
  to the canonical handler, with an observable deprecation WARN on every
  strip. This only rescues a client that cached the old wire name from a
  prior `tools/list` — a client that re-lists after the rename never
  reaches the alias at all, since the host gates `tools/call` on the
  advertised name before forwarding. It is a deprecation cushion for one
  window, not a permanent alias; FORGE gates eventual alias removal on the
  warning going quiet in logs.

### Fixed

- crystalium's own error path (enforcement rejections, `UNKNOWN_TOOL`, and
  any other exception crystalium raises inside `tools/call`) now sets
  `isError: true`. Previously the error came back as ordinary content
  (`isError: false`), so a client checking `isError` (rather than parsing
  content) read an enforcement rejection or an unknown tool as success.
  The payload TEXT is unchanged byte-for-byte — a client parsing content
  keeps working; a client checking `isError` starts getting the truth.

- **#35 fix-forward:** the tool rename above moved the telemetry SLO key
  out from under itself. `server.py`'s dispatch calls
  `record_call(tool=name, ...)` with the post-rename runtime name
  (`recall`), but `telemetry.py`'s `availability()` / `recall_p95()` still
  defaulted to the pre-rename dotted `"crystalium.recall"` — every recall
  call landed in a bucket nothing ever read again. The `session_end` SLO
  panel would have silently emitted an empty `recall_p95`/availability
  reading forever, with no error and no log line. `telemetry.py` now
  exports a single canonical `RECALL_TOOL` constant that `availability()`,
  `recall_p95()`, and the SLO panel all read from, so a future rename
  can't desync them the same way again (wiring `server.py`'s manifest to
  the same constant is a follow-up; out of scope here).
  The guarding test (`test_recall_p95_panel_metric`) could not have caught
  this: it recorded a call and asserted against the *same* hardcoded
  literal it just wrote, so it stayed green regardless of what the
  dispatcher actually recorded under. A new regression test
  (`test_recall_slo_key_matches_dispatch_tool_name`) derives the recorded
  tool name from the production manifest (`build_tool_manifest()`) instead
  of a literal, so it goes red if telemetry's key and the dispatcher's
  name ever diverge again.

## [1.12.0] — 2026-08-05

### Changed

- **#39** migrated the MCP server to SDK 2.x (`mcp>=2,<3`). `mcp.server.lowlevel
  .Server` dropped the `@server.list_tools()` / `@server.call_tool()` decorator
  API in 2.x; `server.py` now registers `tools/list` and `tools/call` via the
  replacement `Server.add_request_handler(method, params_type, handler)`. Every
  other surface used by `server.py` (`Server(name, version=...)`, `Server.run`,
  `Server.create_initialization_options`, `mcp.server.stdio.stdio_server`,
  `mcp.server.streamable_http_manager.StreamableHTTPSessionManager`, and
  `mcp.types.{Tool,TextContent}` with the manifest's camelCase `inputSchema`)
  is unchanged.
- 2.x's low-level `Server` no longer performs the 1.x decorator's implicit
  jsonschema validation of `tools/call` arguments against the tool's
  advertised `inputSchema` before invoking the handler — that behaviour is
  now replicated explicitly inside the `tools/call` handler (same
  `"Input validation error: <message>"` / `isError: true` shape) so the wire
  output stays byte-identical to v1.11.0 for a schema-violating call.
- Added a startup log field (`mcp_sdk_version`) and a fast (non-`slow`) test
  asserting the resolved `mcp` distribution is on the 2.x major line
  (`importlib.metadata.version("mcp")`), so a stale venv/cached image that
  still resolves `mcp` 1.x fails loudly instead of silently re-running the
  old decorator codepath (crystalium#39; same lesson as the v1.9.0
  cached-image gate).

### No client-observable change

- This is a MINOR release: the wire protocol (`initialize`, `tools/list`,
  `tools/call` — success, SDK schema-violation, and crystalium's own
  `UNKNOWN_TOOL` error paths) is byte-identical to v1.11.0 modulo
  `serverInfo.version` and per-call volatile record ids/timestamps, verified
  by re-capturing the MCP wire against the v1.11.0 golden baseline.

## [1.11.0] — 2026-08-04

### Fixed

- **#41** `GraphStore.neighbor_expand` aborted the whole multi-seed loop at
  the first seed's Kuzu cursor exhaustion — the driver RAISES there rather
  than returning `None`, so the `row is None` branch was dead code, and
  `neighbor_expand(seeds) == neighbor_expand([seeds[0]])`. An edge-less
  FIRST seed returned the empty set even when later seeds had neighbours.
  Fixed with a per-seed `try` + `has_next()` idiom.
- **#50** `all_edges` used the same dead-cursor idiom as `neighbor_expand`;
  it was correct only by accident of where its inner `try` was placed.
- **#53** `neighbor_expand(depth>=2)` bound every hop to the same Cypher
  variable, so it matched only self-loops. Reimplemented as iterative
  depth-1 frontier expansion. Latent — every shipped caller used
  `depth=1`.
- **#51** `recent_crystal_ids` had no `id ASC` tiebreak under
  `ORDER BY created_at DESC` (unlike its sibling query) and no index on
  `created_at`; this runs on every commit at production defaults.
- **#43** the retrieval gate's isolation docstring was false —
  `link_cooccurrence` was wired to the arm flag under test, so the two
  arms differed by ~150 co-occurrence edges, and a uniform `_T0` made "5
  most recent" a total tie. Added a tri-state `Config.link_cooccurrence`
  (`None` = today's behaviour, so default-neutral), a uniform link policy
  across arms, strictly-increasing `created_at`, and an isolation
  self-check that returns a `confounded` verdict rather than numbers when
  independence doesn't hold.
- **#54** the retrieval gate emitted confident numbers with an EMPTY dense
  arm under `CRYSTALIUM_SKIP_SLOW`; it now returns an `inconclusive`
  verdict and no numbers.
- **#46** `embed(query)` was called once per layer in a loop-invariant
  position; hoisted to a single call. NOTE: `VectorStore` caches by text,
  so calls 2-4 were already dict lookups — this is a cosmetic cleanup,
  **not** a 4x latency win. No speedup is claimed.
- **#47** `candidate_k` used a bare literal `10` unlinked to
  `FETCH_WIDTH_FLOOR`; the two are now linked. No-op at shipped defaults.

### Changed

- `recall_completion`'s EARNED-ON justification was re-measured on the
  deconfounded gate after #41: `multihop_f1` flat `0.3077` ->
  `completion` `0.4615`, stable across 14 runs (7 hash seeds x 2). The
  previous `0.12->0.18` / `recall 0.67->1.0` figures were measured on the
  confounded fixture (#43) and are withdrawn. `recall_context_match` still
  shows no rank lift and stays OFF.
- DP-2: the #38 `fusion_weight_derived` cliff figures (0.90 deterministic
  fail, 0.95 flake, "1.0% margin") are HISTORICAL to the pre-#41 tree
  (`56c8510`) — the cliff was an artifact of the one-seed `neighbor_expand`
  bug (#41). The supported band is re-grounded on the §D2 identity
  property and the P1 ceiling; sub-1.0 remains unsupported, now because it
  is **uncharacterized** rather than because it is flaky. Post-#41, a
  3-weight x 7-hash-seed sweep is fully degenerate (21/21 green, a single
  distinct `multihop_f1.completion` value across every cell).

### Verification

- The #38 mandate recorded in the 1.10.0 entry below is **discharged**: with
  #41 landed, **AC-124**, **AC-125** and **AC-133** were re-run against a
  re-baselined `eval-before.json` captured at the pre-fix SHA `56c8510`, with
  the post-fix results in `eval-after.json`. **AC-125** (fusion gate):
  `gate_pass` true 7/7 post-fix. **AC-124** (completion): `completion_pass`
  true 7/7, median `multihop_f1.completion` unchanged at `0.4615` — not worse
  than baseline. **AC-133** (context rank): median `context_rank.context`
  unchanged at `2` — not worse than baseline.
- The headline determinism result: pre-fix, the reverted floor probes
  returned two distinct membership sets across seeds (`['N1','target']` and
  `['Z','target']`); post-fix they return one (`['Z','target']`) on all
  seven — the #41 membership nondeterminism is eliminated.
- Both baselines were captured on the same **confounded** retrieval gate
  (DP-2), deliberately: holding the confound constant on both sides is what
  makes the differential isolate #41. The separately deconfounded
  measurement lives in `eval-baseline-deconfounded.json`.
- Artifacts live in the ESL change record
  `crystalium-open-issues-sweep-50`: `eval-before.json`, `eval-after.json`,
  `eval-baseline-deconfounded.json`, `dp2-sweep-postfix.json`,
  `dp2-control-prefix.json`, `red-evidence.txt`.

## [1.10.0] — 2026-08-03

### Changed

- **`CrystalSummary.score` is now a WEIGHTED hybrid-retrieval RRF fusion
  value (previously unweighted).** Ordering semantics are unchanged (still
  non-increasing, id-ascending tiebreak) but the *magnitude*, and the
  relative order among candidates, can differ from 1.9.0: a record present
  in many weak arms no longer automatically outranks a record ranked first
  by the single arm that actually fits the query (crystalium#38). The
  manifest description and this entry are the disclosure required by the
  score-semantics change (DP-7). No storage migration, no schema break.

### Fixed

- **RRF fusion summed unweighted arms, so a record seen by many weak arms
  could outrank the one arm's exact top match.** `weighted_rrf_merge_scored`
  (new; `rrf_merge`/`rrf_merge_scored` are UNCHANGED and remain the
  `recall_weighted_fusion: false` path) applies a per-arm weight:
  `score(id) = sum(w_arm / (60 + rank_arm(id)))`. Deterministic tiebreak by
  `(-score, id)` — insertion order is not stable once graph/completion rank
  order can vary between processes (see the determinism fix below).
- **The graph and completion arms were not independent evidence — both are
  seeded from the dense ranking, so the dense arm's opinion was counted up
  to three times.** They are now collapsed into ONE derived voter by
  min-rank before fusion (`fusion_weight_derived`, default `1.0` — at that
  value the merge is the exact identity of today's fusion when only one of
  the two source arms is populated, measured bitwise: 20/20 in-process
  comparisons, max score diff exactly `0.0`).
- **Graph and completion expansion was seeded from `dense_ranking` alone,**
  so a record the lexical (BM25) arm surfaced but the dense arm ranked
  outside the seed width never had a chance to seed a neighbourhood walk.
  Seeding now uses a preliminary fusion of the sparse + dense arms only
  (never the derived arms — no feedback loop); the seed *count*
  (`fetch_width`) is unchanged, only its *composition*.
- **A query-conditional selectivity boost for the sparse arm**
  (`fusion_sparse_boost_alpha`, default `1.0`): `w_sparse` resolves to
  `1.0 + alpha * selectivity`, where selectivity measures how far the
  lexical arm narrowed the corpus for THIS query, scoped to the searched
  layers and to the same active/all-statuses population the response
  itself applies (`recall_active_only`) — a query matching every crystal in
  the searched layer, or the search-space count drawn from a different
  status population than the match count, both resolve to no boost
  (`w_sparse = 1.0`) rather than an inflated one.
- **The graph and completion arms had no deterministic rank order at all.**
  `neighbor_expand` returns a `set[str]` and `decaying_walk` scores a set
  comprehension, so their iteration order was per-process hash-randomised —
  every fused score touching those arms varied between processes (this is
  the mechanism behind #36's `evals/BENCH-NOTES.md` F-V6 non-reproducible
  `context_rank.both` figure, now annotated there). Two consumer-side
  `sorted()` calls in `retrieve.py` fix the *ordering* — deliberately
  OUTSIDE the `recall_weighted_fusion` flag, since gating a determinism fix
  behind a flag would leave the flag-off path irreproducible too. This does
  **not** fix the graph store's own *membership* nondeterminism (see
  Known limitations below).

### Added

- `Config.recall_weighted_fusion` (default `true`) — the master gate for
  the weighted fusion path, SUBSUMED under `recall_relevance_primary`
  (either flag off restores the unweighted, pre-1.10.0 fusion exactly —
  same single revert lever as 1.9.0's flag).
- `Config.fusion_weight_dense` (default `1.0`), `Config.fusion_weight_derived`
  (default `1.0`; measured cliff — 0.90 fails the shipped fusion-gate
  deterministically, 0.95 is a flake tied to an open store-side determinism
  bug, 1.00 carries a bitwise-measured identity property no other value
  has; values outside `[1.0, ...]` remain legal config but are outside the
  documented/supported band) `[superseded in 1.11.0 — this cliff was an
  artifact of #41; see 1.11.0 notes]`, `Config.fusion_sparse_boost_alpha`
  (default `1.0`). All four are env-var- and `crystalium.yaml`-configurable.
  Deliberately no `fusion_weight_sparse`: RRF ordering is invariant to a
  global positive scale factor.
- `result.explain.fusion` (only when `explain=true`): the three arm
  weights, the selectivity inputs that produced them (`n_sparse`,
  `n_sparse_cap`, `n_scoped`, `n_scoped_layers`, `n_scoped_status`), and
  `fetch_width`/`candidate_k`/`arm_sizes` — never disagrees with the
  surfaced `score`. Note `n_sparse` (the population-resolved selectivity
  numerator) and `arm_sizes.sparse` (the raw, unfiltered sparse-arm length)
  are deliberately DIFFERENT fields and will diverge on any store carrying
  deprecated/superseded rows — this is by design (DP-9(b)), not a bug in
  either number.
- `evals/fusion_gate.py` (`python -m evals fusion-gate`, `--floor` for a
  `FETCH_WIDTH_FLOOR`-overridden single-arm probe): a weighted-vs-unweighted
  A/B over an identical real-`RelationalStore` + real-`GraphStore` corpus,
  plus a multi-layer sparse-arm-rank axis.

### Known limitations (recorded, not fixed here — see follow-up issues)

- **The determinism fix above is ordering-only.** `GraphStore.neighbor_expand`
  wraps its whole seed loop in one `try`, and the underlying Kuzu driver
  raises at cursor exhaustion instead of returning `None`, so in practice
  only the FIRST seed's neighbourhood is ever explored
  (`neighbor_expand(seeds) == neighbor_expand([seeds[0]])`) — a
  **membership**, not merely ordering, nondeterminism this release does not
  touch (`storage/graph.py` is out of scope for this change). Every
  fusion-gate figure in this release's evidence trail was measured on a
  one-seed expansion; follow-up **F-A = #41** (deliberation.md §7, opened
  before this change's tag per C-13) tracks the store-side fix and the
  re-baseline it requires — #41's own text mandates re-running #38's
  AC-124/AC-125/AC-133 against a re-baselined `eval-before.json` once it
  lands, because repairing membership changes arm composition.
- **`FETCH_WIDTH_FLOOR` remains a shipped constant (`10`); this change does
  not remove it or make it conditional.** Measured (not modelled): with the
  floor artificially lowered to `1` — well below the shipped default — the
  target still holds fused rank 0 at `k` in `{1, 3, 5}`, unanimous across 5
  independent `PYTHONHASHSEED` values. The mechanism is D4's base-arm
  reseeding, not the floor: at `fetch_width = 1` the reseeded build's seed
  set already contains the correct record, where the pre-1.10.0 build's
  `dense_ranking[:1]`-seeded walk did not (also measured, as the pair's
  falsifiability precondition). This is evidence toward the deferred
  corpus-scaling question (`FETCH_WIDTH_FLOOR` stays a constant for eval
  reproducibility, DP-6) — it is not itself a claim that the floor is
  redundant at every `k`, fixture, or corpus this change did not test.
- **Cross-layer rank blocking is unchanged.** `sparse_ranking`/
  `dense_ranking` are still built layer-by-layer in a fixed layer order, so
  with `layers=None` a hit in an earlier-iterated layer can still precede a
  more relevant hit in a later one. `evals/fusion_gate.py`'s multi-layer
  axis measures this; the fix itself is deferred to follow-up **D-1 = #45**
  (deliberation.md §7).

## [1.9.0] — 2026-08-02

### Fixed

- **`crystalium.recall` no longer returns query-independent results.** The
  BM25 + dense + graph RRF fusion order reached the composer only as a *fetch*
  order: `Composer.compose()` took no `k` and ranked slot survival strictly by
  `(importance, last_access, id)`, so a topically-unrelated record with accumulated
  access history always outlived a topically-relevant one. Combined with `importance`
  being hardcoded to `0.0` on every episodic/procedural/semantic commit — and with a
  fresh crystal's only routes off 0.0 being an access event it could only earn by
  *already* winning, or an idle-gated Dream sweep — a freshly committed crystal was
  effectively unretrievable, making `commit` silent write-only storage. Relevance is
  now the primary composition signal, with `importance` retained as the secondary.
  Revertible via `recall_relevance_primary: false`. (#36)
- **The retrieval arms' seed width no longer follows the caller's `k`.** Graph and
  completion arm membership was seeded from `dense_ranking[:k]`, so a small `k` changed
  which arms voted, not just how many records came back — at `k<=3` that could push a
  freshly committed, exactly-matching crystal out of the result entirely. Arm seeding
  now uses `max(k, 10)`; the `k` slice remains the only consumer of the caller's `k`.
  (#36)
- **`k` is now an upper bound on the number of returned records.** It previously only
  sized the per-layer candidate fetch (`max(k*3, 10)`) and the graph seed set, so
  `k=3` and `k=15` returned identical result sets. `k` is also clamped to `[1, 100]`
  at the MCP handler and the CLI verb, with a non-coercible `k` falling back to the
  default 10. (#36)
- **A freshly committed crystal now starts at a non-zero `utility.importance`,**
  computed from the layer's injected `importance_fn` (wired into every layer
  constructor but never called) and clamped to a documented cold-start ceiling of
  0.30 so the legacy scorer cannot invert the ranking. No storage migration:
  pre-existing rows keep their stored value and are reachable via the new relevance
  ranking. (#36)
- **`schemas/recall-result.v1.json` now matches the emitted result.** It declared
  `additionalProperties: false` while omitting the v1.6 `explain` field; `budget` and
  `explain` are both declared now, and a round-trip test validates a live
  `RecallResult` against the file. (#36)
- **Pin `mcp` SDK to `>=1.2.0,<2`.** `mcp` 2.0.0 removed the low-level
  `Server.list_tools` decorator API; unpinned fresh builds (CI and release
  images) installed it and broke the server at import. Migration tracked
  separately.

### Added

- `CrystalSummary.score` is populated with the raw hybrid-retrieval RRF score
  (previously declared `Optional[float]` and never set), so client-side ranking is
  inspectable. Populated in both ranking modes. (#36)
- `RecallResult.budget` surfaces the working-set token budget, the requested and
  applied `k`, and `truncated_count`. `evicted_count` keeps its existing meaning
  (token-budget evictions only). The hard 3500-token cap (P0-9) is unchanged. (#36)
- `Config.recall_relevance_primary` (default `true`) — set `false` to restore the
  pre-1.9.0 composition ordering, `k` behaviour and result sets. (#36)
- `crystalium.commit` attaches a `summary_size` advisory when a summary cannot fit
  its destination layer's slot. Advisory only — never a rejection. (#36)
- The `crystalium.commit` tool description now names the four accepted
  `provenance.source` literals, and the `TIER_VIOLATION` advice names the
  procedural-candidate fallback and states where caller identity comes from. (#36)

## [1.8.1] — 2026-07-17

### Fixed

- **`crystalium.recall` no longer crashes when a stored crystal's `summary`
  embeds a cl100k_base special-token string (e.g. `<|endoftext|>`).** The
  composer's `tiktoken` tokenizer called `enc.encode(text)`, which defaults to
  `disallowed_special="all"` and raises `ValueError` on any such text —
  taking down the entire recall with no partial result. `composer.py`'s
  `_tiktoken` now calls `enc.encode(text, disallowed_special=())`, counting an
  embedded special-token string as ordinary text instead of raising.
  Defense-in-depth: `retrieve.py` also wraps the `composer.compose()` call so
  any *future* tokenizer/composer fault degrades a recall to an empty,
  diagnostic working set with a logged warning rather than crashing the whole
  call. (#32)

## [1.8.0] — 2026-07-07

### Added

- **`ingest` CLI verb (`crystalium ingest`) — the one-shot WRITE of an inbound
  ECL handoff envelope, out-of-MCP-session.** The third verb of the GAP-2
  out-of-session pairing (`recall` reads; `commit` writes a caller-typed
  summary; `ingest` writes a roster ECL envelope + artifact payload).
  Options: `--envelope <json-string>` (required), `--payload <text>`
  (required), `--payload-encoding utf8|base64|json` (default `utf8`),
  `--format json|text` (default `json`), `--config`. Reuses
  `crystalium.server._handle_ingest` verbatim over a light one-shot component
  stack (`commit`-verb precedent: `None` vector/graph stores; all four layers
  real-instantiated for exact-signature parity, though only Episodic is
  reachable while `ingest_adapter._KIND_TO_LAYER` stays empty). Every
  behavior below is inherited from the existing MCP `crystalium.ingest` tool,
  not reimplemented: 11-required-field envelope validation (extra top-level
  fields tolerated), the G7 raw-payload-bytes-hash-to-`artifact.sha256`
  binding, and MIN-trust tier resolution.
  **Tier is ENVELOPE-DERIVED, not read from `CRYSTALIUM_CALLER_TIER`** — unlike
  `commit` (env var, default `T0`) and `recall` (env var, default `T1`), this
  verb's trust anchor is the envelope's own attested source identity
  (`from.eidolon` / `trace.tier` MIN-trust clamp); an env override here would
  be a laundering vector. Tool-origin / unknown-identity envelopes land
  Episodic-quarantined exactly as the MCP tool does, and quarantine does not
  exclude a crystal from default one-shot recall (no recall-side change).
  **No summary-quality gate** on this path (MCP `crystalium.ingest` parity) —
  unlike `commit`'s hard CLI rejection: ingest's summary is server-composed
  (`f"{artifact.kind}: {objective}"`), not caller-typed prose, so gating it
  here would make the CLI wrapper reject envelopes the MCP tool accepts.
  **Scope is canonicalized** (v1.6 `normalize_write_scope`) — `scope.project`
  is rewritten to the canonical (data-dir-derived) project key with the
  caller's original project/`thread_id` preserved verbatim in
  `scope.project_raw`. Flagging the asymmetry this creates: the `commit` CLI
  verb stores `--scope-project` **verbatim** (no normalization, test-locked);
  `ingest` **does** normalize — MCP-consistent, CLI-`commit`-inconsistent.

### Deferred

- **The `consolidate` batch verb (episode→skill promotion) is re-deferred, now
  to 1.9.** Pre-deferred from 1.7 to 1.8 (see the 1.7.0 entry below); this
  release scopes to `ingest` only. `dream` remains the sole consolidation
  entry point until `consolidate` lands. See `ROADMAP-POST-1.0.md` for the
  ledger entry.

## [1.7.0] — 2026-07-04

### Added

- **`commit` CLI verb (`crystalium commit`) — the one-shot WRITE counterpart
  to `recall`.** `recall` (v1.6/GAP-2) covers the read half of the
  out-of-MCP-session pairing; `commit` covers the write half, unblocking the
  Eidolons nexus round-trip memory canary (bash-reachable write path with no
  MCP session in the loop). Mirrors `recall`'s and `index`'s construction
  discipline exactly: lazy imports inside the function body (`commit --help`
  never pulls torch/lance/kuzu), structlog routed to the real stderr
  (`sys.__stderr__`) so stdout carries exactly one JSON document, and a
  `BlobStore` + `RelationalStore` + `Enforcement` + `Redactor` +
  `EpisodicLayer(vector_store=None, graph_store=None)` stack built from
  `Config.from_env()`/`--config`. Options: `--summary` (required),
  `--content` (defaults to `--summary`), `--scope-project` (required),
  `--scope-visibility`, `--source` (`human|verified_agent|unverified_agent|
  environment`, default `environment`), `--author-agent` (default
  `crystalium-cli`), `--task-id`, `--format json|text` (default `json`).
  `--format text` prints just the new crystal id. Caller tier defaults to
  `Tier.T0` via `CRYSTALIUM_CALLER_TIER` (asymmetric vs. `recall`'s `T1`
  default — see the option's inline comment in `__main__.py`); since
  `commit` only ever targets the Episodic layer (ceiling `T3`, universally
  writable) this mainly affects the `trust_tier` stamped on the crystal, not
  whether the write is admitted.
  **The v1.6 summary-quality gate (`quality.is_poor_summary` — `quality.py`)
  is a HARD gate here**, unlike the MCP `crystalium.commit` tool where a
  failing summary is accepted with an advisory `summary_quality: "poor"`
  result field: that soft behavior only makes sense when an agent is
  in-session to read the advisory back and fix it, and this one-shot CLI
  writer has no such reader. A failing summary now exits 1 with no stdout
  JSON instead of silently landing a crystal no BM25/FTS5 query could ever
  find.

### Fixed

- **`install.sh`'s `CRYSTALIUM_VERSION` was a stale `"1.0.0"` literal**,
  untouched since the earliest releases while the package moved on to
  1.6.0 (the same class of staleness `crystalium.__version__` fixed in
  v1.6). Now single-sourced at install time from
  `mcp-server/pyproject.toml`'s `[project].version`, with a hard-coded
  fallback (kept in sync with the package version) if the file is
  missing/unreadable. `SCRIPT_DIR` — previously resolved after argument
  parsing — is now resolved earlier in the script so the `--version` flag
  (parsed inside the argument loop) can rely on it under `set -u`.

### Deferred

- **The `consolidate` batch verb (episode→skill promotion) is deferred to
  1.8.** Scoped alongside `commit` for 1.7 but intentionally cut to keep
  this release to exactly the two features above: a k-occurrence trigger +
  held-out validation gate for promoting clustered Episodic crystals to
  Semantic skills, exposed as its own CLI/MCP surface distinct from the
  existing `dream` consolidation path. `dream` remains the sole
  consolidation entry point until `consolidate` lands. See
  `ROADMAP-POST-1.0.md` for the ledger entry.

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
