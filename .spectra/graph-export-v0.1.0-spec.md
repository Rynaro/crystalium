# CRYSTALIUM Graph-Export v0.1.0 — Specification (decision-ready)

**Spec version:** 1.0 (SPECTRA alignment-phase deliverable)
**Target feature:** `crystalium.graph_export` — export memories as a node-graph (nodes = crystals, edges = relationships; Obsidian-style topology, NOT Obsidian markdown)
**Authors:** ATLAS (feasibility scout) + SPECTRA (alignment)
**Phase:** S (SPECTRA Alignment) — downstream consumer is APIVR-Δ / Vivi (wave-by-wave implementation)
**Date:** 2026-06-22
**Branch (suggested):** `feat/graph-export-v0.1.0` — no push, no PR until the final wave gate is green (mirrors house rule, `crystalium-v0.1.0-spec.md` close).

## Input artefacts

All load-bearing claims are anchored to `path:line` form against the implemented tree. Findings are cited as `FINDING-00X`; locked human decisions as `D1–D3`; gaps resolved in this spec as `GAP-X`.

- **ATLAS scout findings** — FINDING-001 … FINDING-010 (quoted inline at each anchor).
- **Locked human decisions** — D1 (edge strategy), D2 (output format), D3 (surface: CLI + MCP). Settled; this spec encodes them, it does not re-litigate them.
- **House-style reference** — `.spectra/crystalium-v0.1.0-spec.md` (gate table, wave decomposition, scorecard, dual-format YAML block). This spec conforms to that house style.
- **Project conventions** — `.spectra/setup/spectra-conventions.md` (loaded; vocabulary supersedes generic SPECTRA placeholders). Canonical module names used throughout: `storage/relational.py`, `storage/graph.py`, `server.py`, `__main__.py`, `ecl.py`, `schemas/*.v1.json`, `mcp-server/tests/test_*.py`.

Reference-only (read; not modified by this feature):
- `schemas/crystal.v1.json`, `schemas/recall-result.v1.json` — schema house-style the new `schemas/graph-export.v1.json` mirrors (FINDING-007).
- `mcp-server/src/crystalium/ecl.py` — `build_for_tool_result` / `emit_sidecar` (auto-inherited; no new integrity code, FINDING-009).

> **Errata against the scout brief.** FINDING-003 calls LINKS_TO "gated default-OFF on `config.recall_completion`". As implemented today `config.py:199` sets `recall_completion: bool = True` ("EARNED ON (T2)"), and `config.py:301`/`config.py:430`-wiring passes it as `link_cooccurrence=` into both `EpisodicLayer` and `SemanticLayer` (`server.py:430,443`). **LINKS_TO is therefore populated by default on current `main`** for any deployment that has KuzuDB available. The backfill path (§5.4) remains specified for cold databases written while completion was OFF and for the no-Kuzu fallback. This errata does not change D1; it only narrows the "must enable" obligation to "must enable-or-backfill" (already D1's wording).

---

## §1. Problem statement & success criteria

### 1.1 Problem

CRYSTALIUM stores a rich, typed memory lattice but has **no way to emit that lattice as a portable graph artefact**. FINDING-004: the eight dispatched MCP tools (`server.py:638-697`) contain no export/dump tool; an unknown tool name raises an enforcement error (`server.py:706-711`). FINDING-001: the KuzuDB graph (`storage/graph.py:25` `VALID_RELATIONS={"LINKS_TO","SUPERSEDES","CITES"}`) exposes `node_count()` (`graph.py:289`), `neighbor_expand()` (`graph.py:200`), and `decaying_walk()` (`graph.py:258`) — but **no `all_edges()` / dump method**, and kuzu nodes carry only `(id, layer)` (no payload). FINDING-002: the authoritative full record lives in SQLite (`relational.py:41-61`), serialized by `_row_to_dict` (`relational.py:360-368`).

Operators and downstream tools (Obsidian-style graph viewers, GraphML/Cytoscape pipelines, audit reviewers) need a single command that materializes the memory graph: **nodes from SQLite crystals, edges from the typed graph PLUS synthesized relational lineage**, in a stable, schema-validated JSON form with mechanical adapters to GraphML/Cytoscape.

### 1.2 Success criteria (measurable)

| # | Criterion | Measurable target | Verified by |
|---|---|---|---|
| SC-1 | Canonical JSON graph produced | `graph_export(scope)` returns `{schema_version, generated_from, counts, truncated, nodes[], edges[]}` validating against `schemas/graph-export.v1.json` | `test_graph_export.py::test_g_ge1_json_validates` |
| SC-2 | Rich edges synthesized, not near-empty | For a fixture with co-occurrence + a supersession + a merge + a conflict, the export contains ≥1 edge of EACH of `LINKS_TO`, `SUPERSEDES`, `MERGED_FROM`, `CONFLICTS_WITH`, each carrying `{from,to,type,source,...}` | `test_graph_export.py::test_g_ge2_rich_edges` |
| SC-3 | Every edge source-tagged | 100% of emitted edges carry `source ∈ {"kuzu","derived"}` (D1) | `test_graph_export.py::test_g_ge2_edge_source_tag` |
| SC-4 | Bounded / paginated (10K guard) | No unbounded full scan; export honors `--limit` (default 5000, max 10000) and sets `truncated: true` when the node cap is hit (FINDING-010, `graph.py:27,143`) | `test_graph_export.py::test_g_ge5_truncation_flag` |
| SC-5 | Visibility & redaction defaults honored | Default export excludes superseded / quarantined / deprecated nodes, redacts to `summary` (never raw blob), honors `agent_class_visibility`; each overridable by an explicit flag (GAP-3) | `test_graph_export.py::test_g_ge3_visibility_defaults` |
| SC-6 | CLI + MCP both ship | `crystalium export ...` (CLI, `__main__.py`) AND `crystalium.graph_export` (MCP tool, `server.py`) both produce byte-identical canonical JSON for identical scope/flags | `test_graph_export.py::test_g_ge6_cli_mcp_parity` |
| SC-7 | ECL envelope auto-emitted | The MCP `graph_export` result emits a valid ECL v2.0 sidecar via `_emit_ecl_sidecar` (`server.py:560,713`) with `artifact.kind = "graph-export"`; SHA-256 integrity matches payload (FINDING-009, G7) | `test_graph_export.py::test_g_ge7_ecl_sidecar` |
| SC-8 | Adapters are mechanical & lossless-enough | GraphML and Cytoscape adapters are pure functions of the canonical JSON; round-trip node/edge **counts** are preserved | `test_graph_export.py::test_g_ge8_adapter_counts` |

**Headline acceptance:** SC-1 … SC-8 all green in-container (`docker compose run --rm crystalium pytest mcp-server/tests/test_graph_export.py -v`), no regression in the existing G1–G8 suite.

---

## §2. Scope / out-of-scope

### In scope
- A new bounded read API on `RelationalStore` (node enumerator) and `GraphStore.all_edges()` (edge enumerator) — GAP-2.
- A deterministic **edge-derivation contract** (D1): `LINKS_TO` (from kuzu) + `SUPERSEDES`, `MERGED_FROM`, `CONFLICTS_WITH` (derived from SQLite relational state/ledgers).
- A canonical JSON graph schema `schemas/graph-export.v1.json` (D2) + GraphML/Cytoscape adapters (mechanical transform).
- A CLI `export` subcommand (`__main__.py`) mirroring `recall` (FINDING-005, FINDING-008).
- An MCP tool `crystalium.graph_export` (manifest `server.py:169` + dispatch branch `server.py:638-697`), auto-inheriting ECL wrapping (`server.py:713`) and rate-limit/tier enforcement (`server.py:630`).
- A visibility / redaction policy with defaults + overrides (GAP-3).
- A dedicated `mcp-server/tests/test_graph_export.py` with gate-style coverage.

### Out of scope (explicit non-goals)
- **Obsidian-markdown-with-wikilinks output.** Explicitly declined by D2. Canonical is JSON `{nodes[], edges[]}`; the only adapters are GraphML and Cytoscape.
- **Live UI / interactive graph rendering.** This is **export only** — a static artefact. No server-rendered view, no websocket stream, no incremental/diff export.
- **New edge *writes* into KuzuDB.** Derived edges (`SUPERSEDES`, `MERGED_FROM`, `CONFLICTS_WITH`) are computed **at export time** from relational state; they are NOT persisted back to kuzu. (FINDING-003: SUPERSEDES/CITES have zero `add_edge` callers; FINDING-006: Dream proposes upserts but never `add_edge`. This spec does not change that — it derives, it does not backfill kuzu.)
- **Importing a graph back into CRYSTALIUM.** One-directional export. No `graph_import`.
- **Cross-project / global export in one call.** Export is scope-bounded (per `scope.project`); a multi-project sweep is a caller-side loop, deferred.
- **Schema-version migration of the export format.** `schemas/graph-export.v1.json` is frozen for v0.1; a `v2` is a future bump.

### Deferred (named door left open)
- `CITES` edge type: present in `VALID_RELATIONS` (`graph.py:25`) but has **zero writers** (confirmed: no non-schema `CITES` reference in `src/`). The exporter MUST pass through any `CITES` edge it finds in kuzu (`source:"kuzu"`) but MUST NOT derive any — see §5.1 rule LINKS-2.
- Incremental / delta export — `generated_from` carries a timestamp so a future delta mode can diff.

---

## §3. The 4 Validation Gates for this feature (G-GE1 … G-GE8)

Same GIVEN/WHEN/THEN + `test_anchor` / `failure_class` discipline as the house G1–G8 (`crystalium-v0.1.0-spec.md §3`). P0 = feature is non-conformant on failure; P1 = correctness regression but tool still returns.

### G-GE1 — Canonical JSON validates against schema
- **GIVEN** any non-empty scope with ≥1 active crystal,
- **WHEN** `graph_export(scope, format="json")` runs,
- **THEN** the returned payload validates against `schemas/graph-export.v1.json` (top-level `schema_version`, `generated_from`, `counts`, `truncated`, `nodes[]`, `edges[]` all present; every node has `id, layer, summary, trust_tier, validation_state, status, importance`; every edge has `from, to, type, source`).
- **test_anchor:** `mcp-server/tests/test_graph_export.py::test_g_ge1_json_validates`
- **failure_class:** `GraphExportSchemaInvalid`
- **severity:** **P0**

### G-GE2 — Rich, source-tagged edge synthesis
- **GIVEN** a fixture project containing (a) two co-occurring crystals linked `LINKS_TO` in kuzu, (b) a superseded→superseding pair (`temporal.superseded_by` set, `relational.py:558-559`), (c) a dedup-merged crystal (`provenance.merged_authors`/`merged_sources`/`corroboration`, `relational.py:619-621`), and (d) a `conflicts`-ledger row (`relational.py:151-162`),
- **WHEN** `graph_export(scope)` runs with default flags,
- **THEN** the `edges[]` set contains ≥1 edge of EACH type `{LINKS_TO, SUPERSEDES, MERGED_FROM, CONFLICTS_WITH}`; every edge carries `source` (`"kuzu"` for LINKS_TO, `"derived"` for the other three) and the type-specific metadata of §5.1.
- **test_anchor:** `mcp-server/tests/test_graph_export.py::test_g_ge2_rich_edges`
- **failure_class:** `GraphExportEdgeContract`
- **severity:** **P0**

### G-GE3 — Visibility & redaction defaults
- **GIVEN** a project with a quarantined crystal, a deprecated crystal, a superseded crystal, a crystal scoped to `agent_class_visibility="forge"`, and an episodic crystal with a `content_ref` blob,
- **WHEN** `graph_export(scope={project, agent_class_visibility:"spectra"})` runs with DEFAULT flags,
- **THEN** the export (1) omits the quarantined, deprecated, and superseded nodes; (2) omits the `forge`-only node; (3) emits the episodic node carrying `summary` only with NO raw blob / `content_ref` payload (`content_ref` hash may appear as an opaque field, never the resolved blob); AND each of these exclusions flips ON via its override flag (`--include-quarantined`, `--include-superseded`, `--include-deprecated`, `--all-visibility`, `--include-content-ref`).
- **test_anchor:** `mcp-server/tests/test_graph_export.py::test_g_ge3_visibility_defaults`
- **failure_class:** `GraphExportVisibilityViolation`
- **severity:** **P0**

### G-GE4 — Dangling-endpoint, dedup & self-loop hygiene
- **GIVEN** an edge whose endpoint node is excluded by the visibility filter, a duplicate `(from,to,type)` pair, and a self-referential `(x,x,type)`,
- **WHEN** `graph_export(scope)` runs,
- **THEN** (1) any edge with an endpoint NOT in the emitted `nodes[]` set is **dropped** (default `dangling_policy="drop"`); (2) duplicate `(from,to,type)` tuples collapse to one edge (de-dup); (3) self-loops `(x,x,*)` are dropped. `counts.edges_dropped_dangling` records the drop count.
- **test_anchor:** `mcp-server/tests/test_graph_export.py::test_g_ge4_edge_hygiene`
- **failure_class:** `GraphExportEdgeContract`
- **severity:** **P0**

### G-GE5 — 10K truncation guard
- **GIVEN** a project whose active crystal count exceeds `--limit` (default 5000, hard max 10000 per FINDING-010 `graph.py:27`),
- **WHEN** `graph_export(scope, limit=N)` runs,
- **THEN** at most `N` nodes are emitted, the enumerator paginates (no unbounded full scan), `truncated: true` is set, and `counts.nodes_total_estimate` records the pre-truncation count. A WARN is logged mirroring `graph.py:143`.
- **test_anchor:** `mcp-server/tests/test_graph_export.py::test_g_ge5_truncation_flag`
- **failure_class:** `GraphExportTruncated` (status, not error)
- **severity:** **P1** (correctness/scale; tool still returns a valid bounded graph)

### G-GE6 — CLI ⇆ MCP parity
- **GIVEN** identical `(scope, format, layers, limit, include-flags)`,
- **WHEN** the export is produced via `crystalium export ...` (CLI) and via `crystalium.graph_export` (MCP),
- **THEN** the two canonical-JSON payloads are byte-identical after `json.dumps(..., sort_keys=True)` normalization (both call the same `GraphExporter.export(...)` core; only the surface differs — FINDING-008).
- **test_anchor:** `mcp-server/tests/test_graph_export.py::test_g_ge6_cli_mcp_parity`
- **failure_class:** `GraphExportParityMismatch`
- **severity:** **P1**

### G-GE7 — ECL envelope on the MCP result
- **GIVEN** any `crystalium.graph_export` MCP call,
- **WHEN** the result is emitted,
- **THEN** a valid ECL v2.0 sidecar is written by `_emit_ecl_sidecar` (`server.py:560,713`) with `artifact.kind = "graph-export"`, `performative = "INFORM"`, `integrity.method = "sha256"`, `integrity.value == sha256(payload_bytes)` (FINDING-009 — no new integrity code; auto-inherited).
- **test_anchor:** `mcp-server/tests/test_graph_export.py::test_g_ge7_ecl_sidecar`
- **failure_class:** `EnvelopeMissing` | `EnvelopeIntegrityMismatch` (reuses house G7 classes)
- **severity:** **P0**

### G-GE8 — Adapter mechanical fidelity
- **GIVEN** a canonical JSON export with `m` nodes and `n` post-hygiene edges,
- **WHEN** the GraphML and Cytoscape adapters transform it,
- **THEN** GraphML emits `m` `<node>` + `n` `<edge>` elements with `type`/`source`/`weight` as `<data>` keys; Cytoscape emits `m` `{data:{id,...}}` node objects + `n` `{data:{source,target,type,...}}` edge objects; node/edge counts are preserved and `type`+`source` survive on every edge.
- **test_anchor:** `mcp-server/tests/test_graph_export.py::test_g_ge8_adapter_counts`
- **failure_class:** `GraphExportAdapterContract`
- **severity:** **P1**

---

## §4. Canonical JSON graph schema (`schemas/graph-export.v1.json`)

New schema, mirroring the `crystal.v1.json` / `recall-result.v1.json` house style (FINDING-007): `$schema` draft 2020-12, `$id` `https://github.com/Rynaro/crystalium/schemas/graph-export.v1.json`, `additionalProperties:false` at every object level, explicit `required` arrays.

### 4.1 Top-level object

| Field | Type | Required | Semantics |
|---|---|---|---|
| `schema_version` | string const `"graph-export.v1"` | yes | Frozen for v0.1. |
| `generated_from` | object | yes | `{ project, agent_class_visibility (nullable), layers[], generated_at (RFC3339), caller_tier }` — provenance of this export. |
| `counts` | object | yes | `{ nodes, edges, nodes_total_estimate, edges_dropped_dangling, edges_deduped }` (all int ≥0). `nodes_total_estimate` ≥ `nodes` (equal when not truncated). |
| `truncated` | boolean | yes | `true` when the node `--limit` cap was hit (G-GE5). |
| `nodes` | array of Node | yes | See §4.2. |
| `edges` | array of Edge | yes | See §4.3. |

### 4.2 Node object

A node is the **redacted, summary-only projection of a SQLite crystal row** (FINDING-002, `_row_to_dict` `relational.py:360-368`). It deliberately excludes resolved blob content (GAP-3).

| Field | Type | Required | Source / notes |
|---|---|---|---|
| `id` | string (UUID) | yes | `crystals.id` (`relational.py:41`). |
| `layer` | enum episodic\|semantic\|procedural\|execution | yes | `crystals.layer`. |
| `summary` | string | yes | `crystals.summary` AFTER redactor pass (`aetheryte/redact.py`, reused). Never the raw blob. |
| `trust_tier` | enum T0\|T1\|T2\|T3 | yes | `crystals.trust_tier`. |
| `validation_state` | enum validated\|unverified\|quarantined\|candidate | yes | `crystals.validation_state`. |
| `status` | enum candidate\|active\|deprecated | yes | `crystals.status`. |
| `importance` | number [0,1] | yes | `utility.importance` (drives node sizing in viewers). |
| `created_at` | RFC3339 string | no | `provenance.created_at`. |
| `last_access` | RFC3339 string | no | `utility.last_access`. |
| `tags` | string[] | no | `crystals.tags` (default `[]`). |
| `protected` | boolean | no | `crystals.protected` (default false). |
| `content_ref` | string (64-hex) \| null | no | **OMITTED by default** (GAP-3); included only when `--include-content-ref`, and then ONLY as the opaque SHA-256 hash, never the resolved blob. |
| `scope_project` | string | no | `scope.project` (for cross-checking; redundant with `generated_from`). |

### 4.3 Edge object

| Field | Type | Required | Source / notes |
|---|---|---|---|
| `from` | string (node id) | yes | Source crystal id. |
| `to` | string (node id) | yes | Destination crystal id. |
| `type` | enum LINKS_TO\|SUPERSEDES\|MERGED_FROM\|CONFLICTS_WITH\|CITES | yes | §5.1. `CITES` pass-through only. |
| `source` | enum kuzu\|derived | yes | D1 mandatory tag. `LINKS_TO`/`CITES` → `"kuzu"`; the other three → `"derived"`. |
| `weight` | number | no | Type-specific (§5.1); default `1.0`. |
| `metadata` | object | no | Type-specific provenance (§5.1): e.g. `{corroboration, merged_authors}` for `MERGED_FROM`, `{winner_tier, loser_tier, similarity}` for `CONFLICTS_WITH`, `{similarity}` for `LINKS_TO` if available, `{}` otherwise. `additionalProperties:true` allowed ONLY inside `metadata`. |

**Directionality (frozen):** `SUPERSEDES` is `from=newer → to=older` (the superseding crystal points at the one it replaced — matches `temporal.superseded_by` semantics where the OLD row names the NEW one, so the edge is the inverse of the stored pointer). `MERGED_FROM` is `from=surviving crystal → to=each contributing author/source proxy node` (see §5.1 MERGED rule for endpoint resolution). `CONFLICTS_WITH` is emitted **bidirectionally as a single canonical edge** `from=winner_id → to=loser_id` with `metadata.direction:"winner_to_loser"`. `LINKS_TO` keeps the kuzu-stored direction verbatim.

---

## §5. Edge-derivation contract (D1 / GAP-1 — load-bearing)

One **testable** rule per edge type. Each rule names: the source anchor, the derivation, the `source` tag, the weight/metadata, and the dangling/dedup/self-loop handling (the global hygiene rules in §5.5 apply to all).

### 5.1 Per-type rules

#### LINKS-1 / LINKS-2 — `LINKS_TO` (and `CITES` pass-through) — `source:"kuzu"`
- **Anchor:** written by `_link_cooccurrence` (`episodic.py:91-105`, `semantic.py:144-155`) via `graph_store.add_edge(crystal_id, other, "LINKS_TO")`; enabled by `link_cooccurrence=config.recall_completion` (`server.py:430,443`; default `True`, `config.py:199`).
- **Rule LINKS-1:** Read every `LINKS_TO` edge from kuzu via the new `GraphStore.all_edges(rel_filter="LINKS_TO")` (§6.2). Emit `{from, to, type:"LINKS_TO", source:"kuzu", weight:1.0, metadata:{}}`, preserving kuzu's stored direction.
- **Rule LINKS-2:** Pass through any `CITES` edge found in kuzu identically (`type:"CITES", source:"kuzu"`). Derive **zero** `CITES` edges (FINDING: `CITES` has no writers).
- **Backfill (cold DB / no-Kuzu):** see §5.4.

#### SUP-1 — `SUPERSEDES` — `source:"derived"`
- **Anchor:** supersession is stored relationally as `temporal.superseded_by` + `temporal.t_valid_to` on the OLD crystal (`relational.py:557-559`); FINDING-003 confirms `SUPERSEDES` has **zero `add_edge` callers**, so it MUST be derived, not read from kuzu.
- **Rule SUP-1 (testable):** For every crystal row `old` where `temporal.superseded_by IS NOT NULL`, emit exactly one edge `{from: old.temporal.superseded_by (the newer crystal), to: old.id, type:"SUPERSEDES", source:"derived", weight:1.0, metadata:{t_valid_to: old.temporal.t_valid_to}}`. Direction = newer→older (§4.3).
- **Default visibility interaction:** because the default filter excludes superseded nodes (§5.3), a default export will typically **drop** SUPERSEDES edges as dangling (their `to` endpoint is excluded) — this is intentional. To see supersession lineage, callers pass `--include-superseded`; then both endpoints survive and the edge is emitted. G-GE2's fixture for SUPERSEDES therefore runs with `--include-superseded` (documented in the test).

#### MERGED-1 — `MERGED_FROM` — `source:"derived"`
- **Anchor:** dedup-merge unions contributing provenance INTO a surviving crystal in place (`merge_provenance`, `relational.py:593-626`): `provenance.merged_authors`, `provenance.merged_sources`, `provenance.corroboration` (`relational.py:619-621`). FINDING-006: consolidation lineage is derived from these fields, NOT from any graph write.
- **Rule MERGED-1 (testable):** For every crystal `c` where `provenance.corroboration > 1` OR `provenance.merged_authors`/`merged_sources` is non-empty, emit one `MERGED_FROM` edge per contributing **author** that resolves to an in-scope crystal id, AND/OR per contributing **source** that resolves to an in-scope crystal id. Endpoint resolution: a contributing author/source maps to a node only if a crystal with that `provenance.author_agent` (resp. source) exists in the emitted `nodes[]` set; unresolvable contributors are recorded as `counts.edges_dropped_dangling` and dropped (§5.5), NOT emitted as synthetic author-proxy nodes in v0.1.
  - Emit `{from: c.id, to: <contributing crystal id>, type:"MERGED_FROM", source:"derived", weight: corroboration, metadata:{corroboration, merged_authors, merged_sources}}`.
- **Rationale note:** `merged_authors`/`merged_sources` are agent/source *names*, not crystal ids; v0.1 resolves them to crystal ids only when an in-scope crystal carries that exact author/source. This keeps the graph a pure crystal-to-crystal graph (no synthetic nodes). A future `v2` MAY add author-proxy nodes — `[GAP-MERGE-RESOLUTION]` (see §13).

#### CONFLICT-1 — `CONFLICTS_WITH` — `source:"derived"`
- **Anchor:** the append-only `conflicts` ledger (`relational.py:151-162`, read via `list_conflicts()` `relational.py:769-782`) records `{winner_id, loser_id, winner_tier, loser_tier, similarity, scope, ts}`. Optionally the `drift_audit` ledger (`relational.py:125-133`, `list_drift_audit()` `relational.py:716-723`) records `{crystal_id, prior_id, similarity, ...}`.
- **Rule CONFLICT-1 (testable):** For every row in `list_conflicts()` whose `scope.project` matches the export scope (or whose `winner_id` is in the emitted node set when the ledger `scope` is null), emit one edge `{from: winner_id, to: loser_id, type:"CONFLICTS_WITH", source:"derived", weight: similarity OR 1.0, metadata:{winner_tier, loser_tier, similarity, direction:"winner_to_loser", origin:"conflicts"}}`.
- **Rule CONFLICT-2 (drift, opt-in):** When `--include-drift` is set, also emit one `CONFLICTS_WITH` edge per `list_drift_audit()` row `{from: crystal_id, to: prior_id, ..., metadata:{similarity, candidate_tier, prior_tier, origin:"drift_audit"}}`. **Default OFF** — drift is a *flag*, not a hard conflict, so it is opt-in to avoid over-connecting the default graph.

### 5.2 Source-tag invariant (D1)
Every emitted edge MUST carry `source`. The mapping is closed: `{LINKS_TO, CITES} → "kuzu"`; `{SUPERSEDES, MERGED_FROM, CONFLICTS_WITH} → "derived"`. No edge may be emitted without a `source` (enforced by the schema `required` + G-GE2 SC-3).

### 5.3 Default node filter (drives dangling resolution)
Default emitted node set = crystals where ALL hold (GAP-3, §7): `status == 'active'` AND `validation_state != 'quarantined'` AND `temporal.superseded_by IS NULL` (i.e. not superseded) AND `temporal.t_valid_to IS NULL` AND visible to the requested `agent_class_visibility`. This mirrors the recall default `recall_active_only=True` (`config.py:215`, `relational.py:list_by_validation_state` excludes deprecated). Overrides per flag (§6.4).

### 5.4 LINKS_TO backfill path (cold DB / no-Kuzu)
For databases written while `recall_completion` was OFF, or deployments without KuzuDB, `LINKS_TO` may be empty. Two sanctioned paths (operator chooses; spec'd, not auto-run):
- **(a) Re-link backfill (preferred when Kuzu present):** a maintenance routine `GraphExporter.backfill_links(scope, limit)` iterates active crystals oldest-first and calls the EXISTING `_link_cooccurrence` logic (re-using `relational.recent_crystal_ids` `relational.py:628` + `graph_store.add_edge(...,"LINKS_TO")`). This is a **write** path → it MUST route a WARN + be invoked only via an explicit `crystalium export --backfill-links` operator flag (never implicit in a read export). It does NOT pass through the tier chokepoint as a memory commit (it writes only graph adjacency, not crystals) but logs every backfill batch (telemetry, mirroring `episodic.py:104`).
- **(b) Derived co-occurrence fallback (read-only, no-Kuzu):** when `graph_store` is `_NullGraphStore` (`server.py:539-552`, `all_edges()` returns `[]`), the exporter MAY synthesize `LINKS_TO` edges read-only from `recent_crystal_ids` adjacency at export time, tagged `source:"derived"` and `metadata:{origin:"cooccurrence_fallback"}`. **Default OFF**, enabled by `--synthesize-links`. This keeps the no-Kuzu export non-empty without mutating storage.

### 5.5 Global hygiene rules (apply to ALL edge types — resolves GAP-1 tail)
- **HYG-1 Dangling endpoints:** an edge is emitted ONLY if BOTH `from` and `to` are in the final emitted `nodes[]` set. Default `dangling_policy="drop"` → drop and increment `counts.edges_dropped_dangling`. Override `--dangling-policy=keep` keeps the edge but the export is then NOT guaranteed node-closed (documented; viewers may show phantom endpoints). Default is `drop`.
- **HYG-2 De-duplication:** collapse identical `(from, to, type)` tuples to ONE edge; on collapse, keep the max `weight` and union `metadata`. Increment `counts.edges_deduped`. (LINKS_TO can legitimately produce dup `(a,b,LINKS_TO)` across episodic+semantic linking.)
- **HYG-3 Self-loops:** drop any edge where `from == to` (no crystal supersedes/merges/conflicts with itself). Self-loops are never emitted.
- **HYG-4 Ordering (determinism, for parity G-GE6):** `nodes[]` sorted by `id` ascending; `edges[]` sorted by `(type, from, to)` ascending. Guarantees byte-stable output across CLI/MCP and across runs.

---

## §6. New read-API contracts (GAP-2)

Two new bounded enumerators. Both honor the 10K guard (FINDING-010). Signatures are the implementation contract for APIVR-Δ.

### 6.1 `RelationalStore.list_for_export(...)` — node enumerator
```python
def list_for_export(
    self,
    project: str,
    *,
    agent_class_visibility: str | None = None,   # None = no visibility filter (all)
    layers: list[str] | None = None,             # None = all four layers
    include_quarantined: bool = False,
    include_deprecated: bool = False,
    include_superseded: bool = False,
    limit: int = 5000,                           # default; hard-clamped to MAX_EXPORT_NODES=10000
    offset: int = 0,                             # pagination cursor
) -> list[dict[str, Any]]:
    """Bounded, scope-filtered crystal enumerator for graph export (GAP-2).

    Ordering: created_at DESC, id ASC (stable tiebreak). Returns _row_to_dict()
    rows (relational.py:360). Applies the §5.3 default filter unless the
    include_* flags relax it. NEVER an unbounded scan: limit is clamped to
    MAX_EXPORT_NODES (FINDING-010, graph.py:27). The caller paginates via offset
    until len(batch) < limit OR the node cap is reached.
    """
```
- **Semantics:** SQL `WHERE json_extract(scope,'$.project')=?` (mirrors `recent_crystal_ids` `relational.py:638`) AND the §5.3 predicates, relaxed per `include_*`. Visibility: `agent_class_visibility IS NULL OR json_extract(scope,'$.agent_class_visibility') IS NULL OR json_extract(scope,'$.agent_class_visibility')=?` (a null on the crystal = visible to all, mirroring `crystal.v1.json` "null = all").
- **Bound:** `limit = min(max(0, limit), MAX_EXPORT_NODES)`. `MAX_EXPORT_NODES = 10000` (module constant, traced to FINDING-010).
- **Count probe:** a sibling `count_for_export(project, **filters) -> int` returns the pre-truncation count for `counts.nodes_total_estimate` / `truncated` (G-GE5). A single bounded `SELECT count(*)` with the same WHERE — cheap, no row hydration.
- **Why new:** FINDING-005 — "NO unbounded `list_all`/`list_by_scope` — exporter needs a new bounded enumerator." `list_by_validation_state` (`relational.py:658`) is the closest existing read but filters on a single state and caps at 200; `recent_crystal_ids` returns only ids. Neither fits.

### 6.2 `GraphStore.all_edges(...)` — edge enumerator
```python
def all_edges(
    self,
    *,
    rel_filter: str | None = None,   # None = all VALID_RELATIONS; else one of them
    limit: int = 50000,              # bounded; edges can exceed node count
    offset: int = 0,
) -> list[tuple[str, str, str]]:
    """Enumerate (from_id, to_id, rel_type) edges from KuzuDB (GAP-2).

    Cypher: MATCH (a:Crystal)-[r]->(b:Crystal) RETURN a.id, b.id, label(r)
    (one query per rel type when rel_filter is None, since kuzu REL tables are
    typed separately — graph.py:93-99). Paginated via SKIP/LIMIT. Returns [] on
    any kuzu error (mirrors neighbor_expand's defensive try/except, graph.py:253).
    _NullGraphStore.all_edges(...) returns [] (server.py:539 stub extension).
    """
```
- **Semantics:** because kuzu stores each relation as a separate REL table (`graph.py:93-99`), `rel_filter=None` runs one `MATCH (a:Crystal)-[:LINKS_TO]->(b:Crystal)` (and `:CITES`, `:SUPERSEDES`) query per type and concatenates, tagging each tuple with its type. `SUPERSEDES`/`CITES` tables exist but are empty in practice (FINDING-003) — `all_edges` returns whatever is physically present (forward-compatible).
- **Bound:** `limit` default 50000 (edges > nodes); paginated. A WARN logs if total edges scanned exceeds `10 × MAX_EXPORT_NODES` (consistency with the node guard).
- **`_NullGraphStore` extension:** add `def all_edges(self, **kwargs) -> list: return []` to the stub (`server.py:539-552`) so the no-Kuzu path is a clean empty-edge set (then §5.4(b) fallback applies if `--synthesize-links`).

### 6.3 Exporter core
```python
class GraphExporter:
    def export(
        self, *, scope: Scope, layers: list[str] | None,
        limit: int, include_flags: ExportFlags, redactor: Redactor,
    ) -> dict:  # the canonical graph dict (validates against graph-export.v1.json)
        ...
```
- One core used by BOTH surfaces (G-GE6 parity). Lives at `mcp-server/src/crystalium/export/graph_export.py` (new `export/` package, role-named per conventions). Steps: (1) `list_for_export` + `count_for_export` → nodes (redact each `summary` via the injected `Redactor`, `aetheryte/redact.py`); (2) build node-id set; (3) `all_edges` → LINKS_TO/CITES; (4) derive SUPERSEDES/MERGED_FROM/CONFLICTS_WITH from relational; (5) apply §5.5 hygiene against the node-id set; (6) assemble top-level + counts + `truncated`.

### 6.4 Flag object
```python
@dataclass(frozen=True)
class ExportFlags:
    include_quarantined: bool = False
    include_deprecated: bool = False
    include_superseded: bool = False
    all_visibility: bool = False          # ignore agent_class_visibility filter
    include_content_ref: bool = False     # emit opaque sha256 hash (never blob)
    include_drift: bool = False           # CONFLICT-2 drift_audit edges
    synthesize_links: bool = False        # §5.4(b) read-only co-occurrence fallback
    backfill_links: bool = False          # §5.4(a) WRITE path; CLI-only, operator-gated
    dangling_policy: str = "drop"         # "drop" | "keep"
```

---

## §7. Visibility / redaction policy (GAP-3) — defaults & overrides

| Policy | Default | Override flag | Anchor / rationale |
|---|---|---|---|
| `agent_class_visibility` respected | **YES** — only crystals visible to the requested class (or `null`=all) are emitted | `--all-visibility` | `crystal.v1.json:98-102` "null = all"; recall isolation parity (CAN-5 in house spec). |
| Quarantined excluded | **YES** | `--include-quarantined` | `validation_state == 'quarantined'` dropped; mirrors recall not surfacing quarantine by default. |
| Deprecated excluded | **YES** | `--include-deprecated` | `status == 'deprecated'`; mirrors `recall_active_only` (`config.py:215`). |
| Superseded excluded | **YES** | `--include-superseded` | `temporal.superseded_by IS NOT NULL`; mirrors bi-temporal recall. |
| Raw blob redacted | **YES — always summary-only** | `--include-content-ref` (hash only) | GAP-3: export `summary` (already in SQLite, FTS5), NEVER resolve `content_ref` blob. Even with the flag, only the opaque 64-hex hash is emitted, never blob bytes. The `summary` is passed through the existing `Redactor` (`aetheryte/redact.py`) so redaction parity with recall holds at the cross-agent boundary (P0-12, conventions §9). |
| `protected` honored | **YES — surfaced, never special-dropped** | n/a | `protected` is emitted as a node field so viewers can mark it; it does NOT change inclusion (protection concerns Dream eviction, not export visibility). |
| Sensitivity tag | Redactor judges `summary` per `scope.sensitivity_tag` | (inherits recall behaviour) | Reuse, no new redaction code. |

**Hard invariant (P0):** the exporter NEVER emits resolved blob content. The `content_ref` field, when present, is the SHA-256 *address* only. This is the single most security-relevant default — it keeps export from becoming an exfiltration path around the redactor.

---

## §8. CLI `export` subcommand + MCP `graph_export` tool contracts (D3)

### 8.1 CLI — `crystalium export` (mirrors `recall`, `__main__.py:246`)
```
crystalium export
  --scope-project TEXT        [required]  scope.project (min_length 1)
  --scope-visibility TEXT     [default: None → all]  agent_class_visibility
  --format [json|graphml|cytoscape]       [default: json]
  --layers TEXT               CSV subset of episodic,semantic,procedural,execution (default: all)
  --limit INTEGER             [default: 5000]  node cap (clamped to 10000)
  --include-quarantined       flag (default off)
  --include-deprecated        flag (default off)
  --include-superseded        flag (default off)
  --all-visibility            flag (default off)
  --include-content-ref       flag (default off; hash only)
  --include-drift             flag (default off)
  --synthesize-links          flag (default off; read-only co-occurrence fallback)
  --backfill-links            flag (default off; WRITE path, operator-gated, WARN-logged)
  --dangling-policy [drop|keep]  [default: drop]
  --output PATH               write to file (default: stdout)
  --config PATH               crystalium.yaml (default: env vars)
```
- **Mirror discipline (FINDING-005/008):** same skeleton as `recall` (`__main__.py:200-362`) — module-level patchable imports, lazy heavy imports inside the body, structlog routed to `sys.__stderr__` so stdout is pure JSON/XML, fast-path defaults that avoid pulling torch/lance/kuzu unless the format/flags need the graph store. Registered on the `cli` click group (`__main__.py:51`).
- **Behaviour:** READ-ONLY by default (never writes the store). The SOLE exception is `--backfill-links` (§5.4(a)), which prints a WARN and a confirmation prompt (like `forget`, `__main__.py:404`) before writing graph adjacency. Exit 0 success, exit 1 on error (stderr message), mirroring `recall` (`__main__.py:353`).
- **`--output`:** when set, write the artefact to the path and print the path; otherwise `click.echo` the artefact to stdout.

### 8.2 MCP tool — `crystalium.graph_export`
- **Manifest entry** (added to `build_tool_manifest()` `server.py:169-174`, eighth-style tool):
  ```json
  {
    "name": "crystalium.graph_export",
    "description": "Export the scoped memory graph as nodes[]+edges[] (JSON canonical, or graphml/cytoscape adapter). Nodes = redacted crystal summaries (never raw blob); edges = LINKS_TO (kuzu) + derived SUPERSEDES/MERGED_FROM/CONFLICTS_WITH. Read-only; bounded (limit<=10000); universally allowed (read op). Rate-limited (200 calls/min).",
    "inputSchema": {
      "type": "object",
      "required": ["scope"],
      "properties": {
        "scope":  {"type":"object","description":"{project, agent_class_visibility, sensitivity_tag}"},
        "format": {"type":"string","enum":["json","graphml","cytoscape"],"default":"json"},
        "layers": {"type":"array","items":{"type":"string","enum":["episodic","semantic","procedural","execution"]}},
        "limit":  {"type":"integer","default":5000,"description":"Node cap (clamped to 10000)"},
        "include": {"type":"object","description":"Optional override flags (§6.4): include_quarantined, include_deprecated, include_superseded, all_visibility, include_content_ref, include_drift, synthesize_links, dangling_policy"}
      }
    }
  }
  ```
- **Dispatch branch** (added in the `_call_tool` `elif` chain, `server.py:638-697`, before the `else` UNKNOWN_TOOL fallback `server.py:706`):
  ```python
  elif name == "crystalium.graph_export":
      result = _handle_graph_export(arguments, exporter, caller_tier)
      result_bytes = json.dumps(result, sort_keys=True, default=str).encode()
      artifact_kind = "graph-export"
  ```
  Then the EXISTING `_emit_ecl_sidecar(name, result_bytes, artifact_kind, run_dir, caller, performative="INFORM")` call (`server.py:713`) fires unchanged — auto-inheriting the ECL envelope (G-GE7, FINDING-009) with zero new integrity code.
- **Tier / enforcement:** `graph_export` is a **read** operation → universally allowed (house matrix "any/recall" row, `crystalium-v0.1.0-spec.md §4`). It inherits `enforcement.assert_rate_limit()` (`server.py:630`) and the per-call telemetry span (`server.py:636`). It does NOT call `assert_tier_allowed` for a commit (it never writes crystals). `--backfill-links` is **not** exposed on the MCP surface (write path is CLI-operator-only); the MCP `include` object has no `backfill_links` key.
- **`_handle_graph_export`** mirrors `_handle_recall` (`server.py:835-864`): parse `scope`, clamp `limit` to `[0,10000]`, build `ExportFlags` from `arguments.get("include",{})`, call `exporter.export(...)`, return the canonical dict (or the adapted artefact when `format != "json"` — adapters run inside the handler so the ECL payload is the actually-returned bytes).
- **`format != json` over MCP:** when `format` is `graphml`/`cytoscape`, the returned `result_bytes` is the adapter output (XML / Cytoscape-JSON). The ECL `artifact.kind` stays `"graph-export"`; integrity hashes the actual returned bytes (FINDING-009 — `integrity.value == sha256(payload_bytes)` regardless of format).

---

## §9. GraphML / Cytoscape adapter mapping (D2)

Both adapters are **pure functions of the canonical JSON** (no store access) — mechanical transforms, in `mcp-server/src/crystalium/export/adapters.py`. Mirrors the existing `test_adapter_mapping.py` test convention.

### 9.1 GraphML (`to_graphml(canonical: dict) -> str`)
- Emit a `<graphml>` document, one `<graph edgedefault="directed">`.
- **`<key>` declarations** (GraphML typed attribute keys): node keys `layer`(string), `summary`(string), `trust_tier`(string), `validation_state`(string), `status`(string), `importance`(double); edge keys `type`(string), `source`(string), `weight`(double).
- **Per node:** `<node id="{id}">` with one `<data key="...">` per node attribute above.
- **Per edge:** `<edge source="{from}" target="{to}">` with `<data key="type">`, `<data key="source">`, `<data key="weight">`. (Note the GraphML term collision: GraphML's `source`/`target` are the edge endpoints; CRYSTALIUM's `source` tag becomes the `<data key="source">` attribute — documented to avoid confusion.)
- **Count fidelity (G-GE8):** `m` `<node>` + `n` `<edge>` elements exactly.

### 9.2 Cytoscape (`to_cytoscape(canonical: dict) -> dict`)
- Emit Cytoscape.js elements JSON: `{"elements": {"nodes":[...], "edges":[...]}}`.
- **Per node:** `{"data": {"id": id, "layer":..., "summary":..., "trust_tier":..., "validation_state":..., "status":..., "importance":...}}`.
- **Per edge:** `{"data": {"id": "{type}:{from}->{to}", "source": from, "target": to, "type": type, "edge_source": source, "weight": weight}}` — Cytoscape reserves `data.source`/`data.target` for endpoints, so the CRYSTALIUM `source` tag is remapped to `edge_source` (documented; G-GE8 asserts `edge_source` survives on every edge).
- **Count fidelity (G-GE8):** `m` node objects + `n` edge objects.

### 9.3 Adapter contract
- Adapters MUST NOT add or drop nodes/edges (count-preserving). They MAY rename attribute keys per the target format's reserved-word constraints (documented above).
- The canonical JSON is the single source of truth; adapters never re-derive edges or re-read the store.

---

## §10. Wave decomposition (W-GE1 … W-GE6)

Sequential. Each wave's `container_test` runs INSIDE `docker compose run --rm crystalium ...` (P0-13, container-first). Each wave has an acceptance gate. Commit subjects use Conventional Commits with the project's scope vocabulary (conventions §Commit scope: `storage, server, ecl, schemas, test` + a new `export` scope is in-keeping).

### W-GE1 — Read APIs + enumerators (GAP-2 foundation)
- **Scope:** `RelationalStore.list_for_export` + `count_for_export` (`storage/relational.py`); `GraphStore.all_edges` (`storage/graph.py`) + `_NullGraphStore.all_edges` stub (`server.py:539`). `MAX_EXPORT_NODES=10000` constant.
- **Files:** `storage/relational.py`, `storage/graph.py`, `server.py` (stub only), `tests/test_storage_relational.py`, `tests/test_storage_graph.py` (extend existing).
- **Acceptance gate:** new enumerator tests green; bounded (limit clamp asserted); pagination correct; visibility/`include_*` predicates correct. No regression in `test_storage_*`.
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_storage_relational.py mcp-server/tests/test_storage_graph.py -v`
- **commit_subject:** `feat(storage): add bounded list_for_export/count_for_export + GraphStore.all_edges (GAP-2)`

### W-GE2 — Edge-derivation core (D1 / GAP-1)
- **Scope:** `export/graph_export.py` `GraphExporter` — node hydration + redaction wiring + the four derivation rules (§5.1) + hygiene (§5.5) + counts/truncation. No CLI/MCP/adapters yet.
- **Files:** `export/__init__.py`, `export/graph_export.py`, `tests/test_graph_export.py` (G-GE2, G-GE4 anchors).
- **Acceptance gate:** G-GE2 (rich source-tagged edges), G-GE4 (dangling/dedup/self-loop hygiene) green against fixtures.
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_graph_export.py -k "ge2 or ge4" -v`
- **commit_subject:** `feat(export): derive SUPERSEDES/MERGED_FROM/CONFLICTS_WITH + LINKS_TO read + edge hygiene (D1)`

### W-GE3 — JSON serialization + schema (D2 canonical)
- **Scope:** `schemas/graph-export.v1.json` (mirrors `crystal.v1.json` house style); top-level assembly (`generated_from`, `counts`, `truncated`); Pydantic mirror if the codebase uses one (`schemas.py`).
- **Files:** `schemas/graph-export.v1.json`, `export/graph_export.py` (assembly), `tests/test_graph_export.py` (G-GE1, G-GE5), `tests/test_schemas.py` (schema validity).
- **Acceptance gate:** G-GE1 (JSON validates), G-GE5 (truncation flag + bounded). `make schema` green.
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_graph_export.py -k "ge1 or ge5" mcp-server/tests/test_schemas.py -v`
- **commit_subject:** `feat(schemas,export): land graph-export.v1.json + canonical assembly + truncation guard (D2)`

### W-GE4 — CLI `export` subcommand (D3a)
- **Scope:** `crystalium export` click command mirroring `recall` (`__main__.py:246`), `--output`, all flags (§8.1), READ-ONLY default + `--backfill-links` operator-gated write path (§5.4(a)).
- **Files:** `__main__.py`, `tests/test_cli.py` (or extend), `tests/test_graph_export.py` (CLI half of G-GE6).
- **Acceptance gate:** CLI emits valid canonical JSON; flags wired; `--output` writes file; structlog→stderr (stdout pure); exit codes correct.
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_cli.py mcp-server/tests/test_graph_export.py -k "cli or ge6" -v`
- **commit_subject:** `feat(server): add crystalium export CLI subcommand mirroring recall (D3a)`

### W-GE5 — MCP tool + ECL auto-wrap (D3b)
- **Scope:** `crystalium.graph_export` manifest entry (`server.py:169`) + `_handle_graph_export` + dispatch branch (`server.py:638-697`); `exporter` wired into `_build_server` (`server.py:380-490`); rate-limit + ECL auto-inherited.
- **Files:** `server.py`, `tests/test_server.py` (or extend), `tests/test_graph_export.py` (G-GE6 MCP half, G-GE7).
- **Acceptance gate:** G-GE6 (CLI⇆MCP byte parity), G-GE7 (ECL sidecar valid + SHA-256 match). Manifest lists the tool; unknown-tool fallback untouched.
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_server.py mcp-server/tests/test_graph_export.py -k "ge6 or ge7" mcp-server/tests/test_ecl_envelope.py -v`
- **commit_subject:** `feat(server,ecl): add crystalium.graph_export MCP tool + auto ECL sidecar (D3b)`

### W-GE6 — Adapters + full suite + canary
- **Scope:** `export/adapters.py` (`to_graphml`, `to_cytoscape`); CLI/MCP `format` plumbing; full `test_graph_export.py` (all G-GE1…G-GE8); feature canary (§12).
- **Files:** `export/adapters.py`, `__main__.py` + `server.py` (format plumbing), `tests/test_graph_export.py` (G-GE8 + canary), `tests/test_adapter_mapping.py` (extend).
- **Acceptance gate:** **G-GE1…G-GE8 all green** + full house suite (`make test`) green (no regression) + ruff/mypy/schema clean (`make lint`, `make typecheck`, `make schema`) + feature canary passes (§12).
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/ -v && docker compose run --rm crystalium make lint typecheck schema`
- **commit_subject:** `feat(export): GraphML/Cytoscape adapters + full graph-export suite + canary (D2)`

---

## §11. Acceptance stories (GIVEN/WHEN/THEN)

### STORY-1 — Rich-edge synthesis (covers SC-2, G-GE2)
> **As an** operator auditing memory lineage, **I want** the export to contain meaningful typed edges (not a near-empty kuzu set) **so that** an Obsidian-style viewer shows real relationships.
- **GIVEN** project `p` with: crystals `a`,`b` co-occurring (`LINKS_TO a→b` in kuzu); `c_old` superseded by `c_new` (`temporal.superseded_by=c_new`); `d` dedup-merged from author `agent-x` whose crystal `e` is in-scope (`provenance.corroboration=2`, `merged_authors=["agent-x"]`); a `conflicts` row `{winner: f, loser: g}`,
- **WHEN** `crystalium export --scope-project p --include-superseded` runs,
- **THEN** `edges[]` contains `{a,b,LINKS_TO,kuzu}`, `{c_new,c_old,SUPERSEDES,derived}`, `{d,e,MERGED_FROM,derived,weight:2}`, `{f,g,CONFLICTS_WITH,derived}`; every edge carries `source`; `counts.edges == 4`.

### STORY-2 — Visibility & redaction filtering (covers SC-5, G-GE3, GAP-3)
> **As a** security-conscious operator, **I want** export defaults to exclude non-active/non-visible crystals and never leak raw blobs **so that** the artefact is safe to share.
- **GIVEN** project `p` with a quarantined crystal `q`, a deprecated crystal `dep`, a superseded crystal `sup`, a `forge`-only crystal `fv`, and an episodic crystal `ep` with `content_ref=<64hex>`,
- **WHEN** `crystalium.graph_export(scope={project:p, agent_class_visibility:"spectra"})` runs with default flags,
- **THEN** `nodes[]` excludes `q`, `dep`, `sup`, `fv`; includes `ep` with `summary` set and NO resolved blob (and no `content_ref` field at all by default); AND re-running with `include={include_quarantined:true, include_deprecated:true, include_superseded:true, all_visibility:true, include_content_ref:true}` includes all five, with `ep.content_ref` present as the bare 64-hex hash only.

### STORY-3 — 10K truncation guard (covers SC-4, G-GE5, FINDING-010)
> **As an** operator of a large workspace, **I want** export to stay bounded **so that** it never does an unbounded scan that degrades KuzuDB past the 10K-node acceptance threshold.
- **GIVEN** project `p` with 12,000 active crystals,
- **WHEN** `crystalium export --scope-project p` runs (default `--limit 5000`),
- **THEN** `len(nodes) == 5000`, `truncated == true`, `counts.nodes_total_estimate == 12000`, a WARN is logged (mirroring `graph.py:143`), and re-running with `--limit 10000` emits 10000 nodes still `truncated:true` (cap is the hard `MAX_EXPORT_NODES`); `--limit 20000` is clamped to 10000.

### STORY-4 — Format adapters (covers SC-8, G-GE8, D2)
> **As a** downstream tool author, **I want** GraphML and Cytoscape outputs **so that** I can load the memory graph into existing graph software.
- **GIVEN** a canonical export with 3 nodes and 4 edges,
- **WHEN** `crystalium export --scope-project p --format graphml` and `--format cytoscape` run,
- **THEN** GraphML output has exactly 3 `<node>` + 4 `<edge>` elements with `type`/`source`/`weight` `<data>` keys; Cytoscape output has 3 node + 4 edge `{data:...}` objects with `edge_source` preserved on each edge; and the MCP `graph_export(format="graphml")` result emits an ECL sidecar whose `integrity.value` hashes the GraphML XML bytes (G-GE7).

### STORY-5 — CLI ⇆ MCP parity (covers SC-6, G-GE6)
> **As a** maintainer, **I want** the CLI and MCP surfaces to share one core **so that** there is exactly one edge-derivation truth.
- **GIVEN** identical scope/format/layers/limit/flags,
- **WHEN** the export runs via CLI and via the MCP tool,
- **THEN** `json.dumps(cli_out, sort_keys=True) == json.dumps(mcp_out, sort_keys=True)` (both call `GraphExporter.export`; HYG-4 ordering guarantees byte-stability).

---

## §12. Validation gates / feature canary

**Feature gate set:** G-GE1 … G-GE8 (§3), all green in-container, plus zero regression on the house G1–G8 suite.

**Feature canary (CAN-GE1, single A/B-style oracle):**

| ID | Name | Scenario | Oracle | Pass criterion |
|---|---|---|---|---|
| CAN-GE1 | rich_edges_beat_kuzu_only | Build a fixture project with all four edge sources populated; export (a) edges from kuzu `all_edges` ONLY vs (b) full synthesized export. | Kuzu-only arm yields only `LINKS_TO` edges; full arm yields all four types, source-tagged. | Full-export edge-type cardinality strictly ≥ kuzu-only (≥3 additional types: SUPERSEDES, MERGED_FROM, CONFLICTS_WITH all present); 100% of edges `source`-tagged. |

This is the feature's analogue of the house "memory-on beats memory-off" headline (`crystalium-v0.1.0-spec.md §13`): **rich-synthesis beats kuzu-only.** It directly proves D1's load-bearing claim that the export is meaningful, not near-empty.

**Quality bars (no regression):** `make test` exit 0; `make lint`/`make typecheck`/`make schema` clean; `agent.md` token count unchanged (this feature touches no install-target file — EIIS whitelist intact, conventions §11); Composer ≤3500 untouched (G6 unaffected — export bypasses the composer entirely).

---

## §13. Complexity assessment + residual assumptions / [GAP] markers

### Complexity score (SPECTRA 4-dimension matrix)
- **Component spread:** 4/4 modules touched (`storage/`, new `export/`, `server.py`, `__main__.py`) → **HIGH**.
- **Coupling/risk:** MODERATE — additive only; no chokepoint mutation, no bi-temporal write path, read-only except the explicitly-gated `--backfill-links`. The riskiest seam is edge-endpoint resolution (MERGED_FROM author→crystal mapping).
- **Ambiguity:** LOW — D1/D2/D3 locked; GAP-2/GAP-3/GAP-1 resolved in §5/§6/§7.
- **Novelty:** MODERATE — new schema + new enumerators + new tool, but every integration seam has a working exemplar (`recall` CLI, `recall` MCP dispatch, `crystal.v1.json` schema, `_emit_ecl_sidecar`).
- **Aggregate:** **8/12** → extended-thinking tier; single-pass cycle sufficient (no TRANCE — stakes are additive-feature, not multi-service architecture). Routed standard with extended depth.

### Confidence report
- **Pattern match:** 0.90 — `recall` CLI + MCP dispatch + `crystal.v1.json` are near-perfect templates.
- **Requirement clarity:** 0.95 — decisions locked; gaps resolved.
- **Decomposition stability:** 0.88 — six waves, clean inputs/outputs, each independently testable.
- **Constraint compliance:** 0.92 — container-first, ECL auto-inherit, EIIS whitelist untouched, read-only-by-default.
- **Aggregate confidence: ~91% → AUTO_PROCEED** (≥85% gate). Deliver to APIVR-Δ for wave-by-wave execution.

### Residual assumptions / [GAP] markers
- **[GAP-MERGE-RESOLUTION]** (§5.1 MERGED-1): `provenance.merged_authors`/`merged_sources` are agent/source *names*, not crystal ids. v0.1 resolves them to crystal ids only when an in-scope crystal carries that exact `author_agent`/`source`; otherwise the contributor is dropped (counted as dangling). **Unverified whether** this captures the operator's intended merge lineage in all cases. A `v2` MAY introduce synthetic author-proxy nodes. APIVR-Δ should confirm the fixture for G-GE2's MERGED_FROM case reflects a realistic merge before freezing the rule. *Risk if wrong:* MERGED_FROM under-populates in single-author-per-fact workspaces; LOW (the other three edge types carry the "rich" guarantee).
- **[GAP-CONFLICT-SCOPE]** (§5.1 CONFLICT-1): the `conflicts` ledger stores `scope` as JSON but it MAY be null for older rows. The rule falls back to "winner_id in node set" when ledger scope is null. **Unverified** how many historical conflict rows have null scope. *Risk:* a null-scope conflict from project B could surface in project A's export if both endpoints happen to be in A — mitigated by the node-set membership check (both endpoints must be in-scope). LOW.
- **[GAP-KUZU-DIRECTION]** (§5.1 LINKS-1): `_link_cooccurrence` writes `add_edge(crystal_id, other, "LINKS_TO")` where `other` is a *recent* crystal (`recent_crystal_ids`, `relational.py:628`). The stored direction is new→old. The exporter preserves it verbatim (no semantic claim about LINKS_TO directionality is load-bearing for viewers, which usually treat co-occurrence as undirected). *Risk:* a viewer that treats LINKS_TO as directed shows arrows new→old; cosmetic. NEGLIGIBLE.
- **[GAP-NULLGRAPH-PARITY]** (§5.4(b), G-GE6): when KuzuDB is absent, `all_edges` returns `[]` and (without `--synthesize-links`) the CLI fast-path and the MCP path both yield zero LINKS_TO — parity holds trivially. With `--synthesize-links`, the read-only co-occurrence fallback is `source:"derived"`; APIVR-Δ MUST ensure the CLI and MCP both apply the fallback identically (it is in the shared `GraphExporter.export` core, so parity is structural). *Risk:* LOW.
- **Assumption (errata, §0):** LINKS_TO is populated by default on current `main` (`config.py:199` `recall_completion=True`). If a future config flip turns it OFF, the `--backfill-links` / `--synthesize-links` paths (§5.4) are the documented remedy. APIVR-Δ should re-read `config.py:199` at W-GE1 to confirm the default has not changed.

---

## §14. Spec scorecard

APIVR-Δ scores 1–5 (5 = fully satisfied). Pass bar: ≥4 on every criterion; any ≤3 triggers a SPECTRA re-run before W-GE1.

| # | Criterion | How to score |
|---|---|---|
| 1 | Edge-derivation rules testable | §5.1 has one GIVEN-derivable rule per type with file:line anchor + source tag (LINKS-1, SUP-1, MERGED-1, CONFLICT-1). |
| 2 | Read-API signatures complete | §6.1/§6.2 give full Python signatures + pagination + bound + the 10K clamp. |
| 3 | Canonical schema fields enumerated | §4 lists every node + edge + top-level field with type/required/source. |
| 4 | Visibility/redaction defaulted + overridable | §7 table: 7 policies, each with default + override flag + anchor; blob-redaction is a hard P0 invariant. |
| 5 | Both surfaces specified | §8.1 CLI flags mirror `recall`; §8.2 MCP manifest + dispatch + ECL auto-inherit, with the exact `elif` branch. |
| 6 | Container-first compliance | Every §10 `container_test` starts with `docker compose run --rm crystalium`. One host-shell command = score 1. |
| 7 | Wave plan executable | §10 W-GE1…W-GE6 each have clean inputs, outputs, acceptance gate, container_test, commit subject. |
| 8 | Gaps surfaced, not buried | §13 lists 4 `[GAP-*]` markers + 1 errata-assumption with risk-if-wrong, plus the §0 errata correcting FINDING-003. |

---

**End of spec.** Next consumer: APIVR-Δ / Vivi for wave-by-wave implementation per §10. Suggested branch `feat/graph-export-v0.1.0`; no push, no PR until W-GE6 gate green. This feature touches NO install-target file — EIIS v1.4 whitelist and `agent.md ≤1000 tokens` are unaffected.

---

```yaml
# ── Agent-executable contract block (dual-format house style) ──────────────
# Mirrors .spectra/crystalium-v0.1.0-spec.yaml conventions. APIVR-Δ/Vivi read this.
spec:
  feature: graph-export
  version: v0.1.0
  intent: REQUEST
  complexity: 8/12
  confidence: 0.91
  decision: AUTO_PROCEED
  read_only_except: ["cli flag --backfill-links (operator-gated graph adjacency write)"]
  install_target_impact: none   # no file in ./.eidolons/crystalium/* changes; EIIS whitelist intact

locked_decisions:
  D1_edge_strategy: synthesize-rich-edges
  D2_output_format: json-canonical + graphml/cytoscape-adapters   # NOT obsidian-markdown
  D3_surface: [cli-export-subcommand, mcp-graph_export-tool]

schemas:
  new:
    - path: schemas/graph-export.v1.json
      mirrors: schemas/crystal.v1.json
      top_level_required: [schema_version, generated_from, counts, truncated, nodes, edges]
      node_required: [id, layer, summary, trust_tier, validation_state, status, importance]
      edge_required: [from, to, type, source]
      edge_type_enum: [LINKS_TO, SUPERSEDES, MERGED_FROM, CONFLICTS_WITH, CITES]
      edge_source_enum: [kuzu, derived]

edge_derivation:
  LINKS_TO:
    source_tag: kuzu
    anchor: "graph_store.add_edge(...,'LINKS_TO') via _link_cooccurrence (episodic.py:91-105, semantic.py:144-155); enabled config.recall_completion=True (config.py:199; server.py:430,443)"
    rule: "read all kuzu LINKS_TO via GraphStore.all_edges(rel_filter='LINKS_TO'); preserve direction; weight 1.0"
  CITES:
    source_tag: kuzu
    rule: "pass-through any kuzu CITES verbatim; derive zero (no writers)"
  SUPERSEDES:
    source_tag: derived
    anchor: "temporal.superseded_by + t_valid_to (relational.py:557-559); FINDING-003 zero add_edge callers"
    rule: "for each crystal old with temporal.superseded_by!=null: emit {from: superseded_by(newer), to: old.id, type:SUPERSEDES, source:derived, weight:1.0, metadata:{t_valid_to}}; direction newer->older"
  MERGED_FROM:
    source_tag: derived
    anchor: "provenance.merged_authors/merged_sources/corroboration (relational.py:619-621, merge_provenance:593-626)"
    rule: "for crystal c with corroboration>1 or merged_* nonempty: emit one edge per contributing author/source that resolves to an in-scope crystal id; weight=corroboration; metadata={corroboration,merged_authors,merged_sources}; unresolvable contributors dropped as dangling"
  CONFLICTS_WITH:
    source_tag: derived
    anchor: "conflicts ledger (relational.py:151-162, list_conflicts:769-782); optional drift_audit (relational.py:125-133, list_drift_audit:716-723)"
    rule: "for each conflicts row in-scope: emit {from:winner_id, to:loser_id, type:CONFLICTS_WITH, source:derived, weight:similarity||1.0, metadata:{winner_tier,loser_tier,similarity,direction:winner_to_loser,origin:conflicts}}; drift rows ONLY when include_drift flag set (origin:drift_audit)"

edge_hygiene:
  HYG-1_dangling: "emit edge iff both endpoints in nodes[]; default policy=drop; count edges_dropped_dangling; override --dangling-policy=keep"
  HYG-2_dedup: "collapse identical (from,to,type) to one; max weight, union metadata; count edges_deduped"
  HYG-3_self_loop: "drop from==to always"
  HYG-4_ordering: "nodes sorted by id asc; edges sorted by (type,from,to) asc — guarantees CLI/MCP byte parity"

read_apis:
  - target: storage/relational.py
    add:
      - "list_for_export(project, *, agent_class_visibility=None, layers=None, include_quarantined=False, include_deprecated=False, include_superseded=False, limit=5000, offset=0) -> list[dict]"
      - "count_for_export(project, **filters) -> int"
    bound: "limit clamped to MAX_EXPORT_NODES=10000 (FINDING-010, graph.py:27)"
    default_filter: "status=active AND validation_state!=quarantined AND temporal.superseded_by IS NULL AND t_valid_to IS NULL AND visible(agent_class_visibility); ordering created_at DESC, id ASC"
  - target: storage/graph.py
    add:
      - "all_edges(*, rel_filter=None, limit=50000, offset=0) -> list[tuple[str,str,str]]"
    note: "one Cypher MATCH per REL type (kuzu typed tables, graph.py:93-99); returns [] on error; _NullGraphStore.all_edges returns []"

visibility_redaction:   # GAP-3
  defaults:
    agent_class_visibility_respected: true   # override --all-visibility
    exclude_quarantined: true                # override --include-quarantined
    exclude_deprecated: true                 # override --include-deprecated
    exclude_superseded: true                 # override --include-superseded
    blob_redacted_summary_only: true         # HARD P0; --include-content-ref emits hash only, never blob
    protected_honored: true                  # surfaced as node field; never changes inclusion
  redactor: "reuse aetheryte/redact.py on summary (P0-12 parity at cross-agent boundary)"

cli_surface:   # D3a; mirrors recall (__main__.py:246)
  command: "crystalium export"
  flags: [--scope-project*, --scope-visibility, "--format[json|graphml|cytoscape]", --layers, "--limit(5000,clamp 10000)", --include-quarantined, --include-deprecated, --include-superseded, --all-visibility, --include-content-ref, --include-drift, --synthesize-links, "--backfill-links(WRITE,operator-gated)", "--dangling-policy[drop|keep]", --output, --config]
  discipline: "module-level patchable imports; lazy heavy imports in body; structlog->sys.__stderr__ (stdout pure); exit 0/1"

mcp_surface:   # D3b
  tool: crystalium.graph_export
  manifest_at: server.py:169   # build_tool_manifest
  dispatch_at: server.py:638-697   # add elif before UNKNOWN_TOOL else (server.py:706)
  handler: "_handle_graph_export(arguments, exporter, caller_tier) mirroring _handle_recall (server.py:835)"
  args: {scope: required, format: "json|graphml|cytoscape (default json)", layers: optional, limit: "int default 5000 clamp 10000", include: "optional override-flags object"}
  tier: "read op — universally allowed (any/recall row); inherits assert_rate_limit (server.py:630); NO assert_tier_allowed commit"
  ecl: "auto via _emit_ecl_sidecar (server.py:560,713); artifact.kind=graph-export; performative=INFORM; integrity.value==sha256(payload_bytes) (FINDING-009; no new code)"
  backfill_links_exposed: false   # write path CLI-only

adapters:   # D2; pure functions of canonical JSON; export/adapters.py
  graphml: "to_graphml(canonical)->str; <key> typed attrs; m <node> + n <edge>; CRYSTALIUM edge source -> <data key='source'>"
  cytoscape: "to_cytoscape(canonical)->dict; elements.nodes/edges; edge data.source/target=endpoints, CRYSTALIUM source tag remapped to edge_source"
  contract: "count-preserving; never re-derive or re-read store; type+source survive on every edge"

gates:
  - {id: G-GE1, name: json_validates, severity: P0, anchor: "test_graph_export.py::test_g_ge1_json_validates"}
  - {id: G-GE2, name: rich_source_tagged_edges, severity: P0, anchor: "test_graph_export.py::test_g_ge2_rich_edges"}
  - {id: G-GE3, name: visibility_redaction_defaults, severity: P0, anchor: "test_graph_export.py::test_g_ge3_visibility_defaults"}
  - {id: G-GE4, name: edge_hygiene, severity: P0, anchor: "test_graph_export.py::test_g_ge4_edge_hygiene"}
  - {id: G-GE5, name: truncation_guard, severity: P1, anchor: "test_graph_export.py::test_g_ge5_truncation_flag"}
  - {id: G-GE6, name: cli_mcp_parity, severity: P1, anchor: "test_graph_export.py::test_g_ge6_cli_mcp_parity"}
  - {id: G-GE7, name: ecl_sidecar, severity: P0, anchor: "test_graph_export.py::test_g_ge7_ecl_sidecar"}
  - {id: G-GE8, name: adapter_counts, severity: P1, anchor: "test_graph_export.py::test_g_ge8_adapter_counts"}

canary:
  - {id: CAN-GE1, name: rich_edges_beat_kuzu_only, oracle: "full export edge-type cardinality >= kuzu-only (>=3 extra types); 100% edges source-tagged", pass: "all four edge types present + source-tagged"}

waves:
  - id: W-GE1
    scope: "bounded read APIs (list_for_export/count_for_export, GraphStore.all_edges + null stub)"
    files: [storage/relational.py, storage/graph.py, server.py, tests/test_storage_relational.py, tests/test_storage_graph.py]
    gate: "enumerator tests green; bounded+paginated; predicates correct"
    container_test: "docker compose run --rm crystalium pytest mcp-server/tests/test_storage_relational.py mcp-server/tests/test_storage_graph.py -v"
    commit_subject: "feat(storage): add bounded list_for_export/count_for_export + GraphStore.all_edges (GAP-2)"
  - id: W-GE2
    scope: "GraphExporter edge derivation (D1) + hygiene"
    files: [export/__init__.py, export/graph_export.py, tests/test_graph_export.py]
    gate: "G-GE2 + G-GE4 green"
    container_test: "docker compose run --rm crystalium pytest mcp-server/tests/test_graph_export.py -k 'ge2 or ge4' -v"
    commit_subject: "feat(export): derive SUPERSEDES/MERGED_FROM/CONFLICTS_WITH + LINKS_TO read + edge hygiene (D1)"
  - id: W-GE3
    scope: "canonical JSON + schema (D2) + truncation"
    files: [schemas/graph-export.v1.json, export/graph_export.py, tests/test_graph_export.py, tests/test_schemas.py]
    gate: "G-GE1 + G-GE5 green; make schema clean"
    container_test: "docker compose run --rm crystalium pytest mcp-server/tests/test_graph_export.py -k 'ge1 or ge5' mcp-server/tests/test_schemas.py -v"
    commit_subject: "feat(schemas,export): land graph-export.v1.json + canonical assembly + truncation guard (D2)"
  - id: W-GE4
    scope: "CLI export subcommand (D3a)"
    files: [__main__.py, tests/test_cli.py, tests/test_graph_export.py]
    gate: "CLI valid JSON; flags wired; --output; stdout pure; exit codes"
    container_test: "docker compose run --rm crystalium pytest mcp-server/tests/test_cli.py mcp-server/tests/test_graph_export.py -k 'cli or ge6' -v"
    commit_subject: "feat(server): add crystalium export CLI subcommand mirroring recall (D3a)"
  - id: W-GE5
    scope: "MCP graph_export tool + ECL auto-wrap (D3b)"
    files: [server.py, tests/test_server.py, tests/test_graph_export.py]
    gate: "G-GE6 + G-GE7 green; manifest lists tool; unknown-tool fallback intact"
    container_test: "docker compose run --rm crystalium pytest mcp-server/tests/test_server.py mcp-server/tests/test_graph_export.py -k 'ge6 or ge7' mcp-server/tests/test_ecl_envelope.py -v"
    commit_subject: "feat(server,ecl): add crystalium.graph_export MCP tool + auto ECL sidecar (D3b)"
  - id: W-GE6
    scope: "GraphML/Cytoscape adapters + full suite + canary"
    files: [export/adapters.py, __main__.py, server.py, tests/test_graph_export.py, tests/test_adapter_mapping.py]
    gate: "G-GE1..G-GE8 green + make test/lint/typecheck/schema clean + CAN-GE1 pass + no regression"
    container_test: "docker compose run --rm crystalium pytest mcp-server/tests/ -v && docker compose run --rm crystalium make lint typecheck schema"
    commit_subject: "feat(export): GraphML/Cytoscape adapters + full graph-export suite + canary (D2)"

residual_gaps:
  - {id: GAP-MERGE-RESOLUTION, where: "§5.1 MERGED-1", risk: LOW, note: "merged_authors are names not ids; resolved only to in-scope crystals; v2 may add author-proxy nodes"}
  - {id: GAP-CONFLICT-SCOPE, where: "§5.1 CONFLICT-1", risk: LOW, note: "older conflicts rows may have null scope; node-set membership check mitigates cross-project leak"}
  - {id: GAP-KUZU-DIRECTION, where: "§5.1 LINKS-1", risk: NEGLIGIBLE, note: "LINKS_TO stored new->old; preserved verbatim; cosmetic for directed viewers"}
  - {id: GAP-NULLGRAPH-PARITY, where: "§5.4(b)", risk: LOW, note: "no-Kuzu fallback shared in GraphExporter.export core => structural parity"}
errata:
  - {against: FINDING-003, note: "LINKS_TO is populated by DEFAULT on current main (config.recall_completion=True, config.py:199), not default-OFF; D1 unchanged (enable-or-backfill)"}
```
