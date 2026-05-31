# CRYSTALIUM

The shared four-layer memory substrate for the Eidolons roster. Stores, gates, retrieves, consolidates, and forgets memory. Does not reason, plan, or write code — reasoning lives in the consuming Eidolon.

## Status

**v1.0.0 — roster member** of the Eidolons roster (`Rynaro/eidolons`, `capability_class: memory`). Every roster Eidolon writes its handoffs into CRYSTALIUM and recalls them back out. EIIS v1.4 + ECL v2.0 conformant; `pytest -m conformance` green (G1–G8 + mechanical invariants, blocking CI job). Container-first — all Python toolchain runs inside Docker; the host runs only `docker compose`, `git`, and `make`.

CRYSTALIUM is **infrastructure, not an agent.** It is the bidirectional substrate the roster shares: it stores/gates/retrieves/consolidates/forgets; the reasoning, planning, and code-writing live in the Eidolon that calls `crystalium.recall`, `crystalium.commit`, `crystalium.ingest`, etc.

---

## Architecture (10-second version)

```
HOST (operator only: docker, git, make)
    │
    ├─→ docker compose run --rm crystalium
    │
    ├─→ MCP stdio server (JSON-RPC 2.0)
    │
    ├─→ enforcement.py (CHOKEPOINT)
    │   • assert_tier_allowed (tier × layer × op matrix)
    │   • assert_no_path_escape (symlink + traversal guard)
    │   • assert_rate_limit (200 calls/min sliding window)
    │   • record telemetry (structlog JSONL + OpenTelemetry)
    │
    ├─→ Four memory layers
    │   • Episodic (pointers, quarantine-by-default)
    │   • Semantic (gated promotion, indefinite)
    │   • Procedural (verifier-gated, skills)
    │   • Execution (ephemeral, TTL)
    │
    ├─→ Aetheryte II (hybrid recall)
    │   • BM25 (SQLite + FTS5, sparse)
    │   • Vector (LanceDB, dense)
    │   • Graph (KuzuDB, structured facts)
    │   • active-only recall (excludes deprecated/superseded — default ON)
    │
    └─→ Dream worker (async, idle-triggered)
        • Orient → Gather → Consolidate → Prune
        • Runs outside MCP request context
        • Proposes (never force-writes)

Storage:
  • Indices + metadata in SQLite/LanceDB/KuzuDB (queryable, cacheable)
  • Episodic payloads on filesystem (content-addressed, immutable)
  • All under ~/.crystalium/<project>/ (portable, local-first)

Every tool result emits an ECL v2.0 envelope sidecar (integrity via SHA-256).
```

---

## Quick start (container-first)

```bash
git clone https://github.com/Rynaro/crystalium
cd crystalium

# Build and smoke-test
docker compose build
docker compose run --rm crystalium make test

# Start the MCP server (host connects via stdio)
docker compose up crystalium
```

The server listens on stdin/stdout. Wire it to Claude Code, Cursor, Copilot, or opencode via `.mcp.json`:

```json
{
  "mcpServers": {
    "crystalium": {
      "command": "docker",
      "args": ["compose", "run", "--rm", "-i", "crystalium", "python", "-m", "crystalium", "serve"]
    }
  }
}
```

**No host `pip`, `uv`, `python`, or `pytest` invocations.** All dev commands run inside the container via `docker compose run --rm crystalium <cmd>` or `make` targets. Per-host setup guides live in `hosts/{claude-code,cursor,copilot,opencode}.md`.

---

## What it gives the Eidolons

- **Memory across sessions.** Episodic recall of past missions, PRs, and outcomes.
- **Consolidated facts.** Semantic layer curates API signatures, project conventions, design decisions — promoted only on ≥k independent witnesses.
- **Verified skills.** Procedural layer stores tested refactoring recipes and proven patterns; a sandboxed verifier gates admission.
- **A shared handoff bus.** `crystalium.ingest` absorbs every roster ECL handoff (ATLAS scout-reports, SPECTRA specs, VIGIL root-cause reports, …) preserving provenance and the MIN trust tier; T3/tool-origin artifacts land Episodic-quarantined, never laundered up.
- **Bounded working set.** The composer enforces a ≤3,500-token budget with deterministic eviction, freeing the agent to focus on reasoning.
- **Trust propagation.** Multi-agent consolidation takes the minimum trust tier of inputs, blocking poison laundering.

---

## What it doesn't do

- **Reason.** No inference loop, no planning, no goal-seeking.
- **Provide raw autonomy.** Constrained interfaces: agents call `recall`; Dream proposes (never force-writes).
- **Require external services.** Fully self-hosted (SQLite, LanceDB, KuzuDB embedded); optional Ollama for redaction.

---

## Tool surface (8 MCP tools)

| Tool | Purpose | Gated by |
|---|---|---|
| `recall(scope, query, k, layers)` | Hybrid BM25+vector+graph retrieval with reranking; active-only by default | Rate limit only |
| `commit(layer, payload, provenance)` | Write with tier enforcement + bi-temporal tracking | Tier matrix (G1–G4) |
| `ingest(envelope, payload)` | Ingest a roster ECL handoff (v1.x/v2.x) → `crystal.v1`; native artifact preserved verbatim in `encoding_context`; commits through the chokepoint (MIN trust tier; T3 → episodic-quarantine) | Tier matrix (G1) |
| `update(id, patch, reason)` | Field edits with invalidate-old; never hard-delete | Tier matrix (G1, G4) |
| `skill_invoke(name, args)` | Sandbox verifier for procedural admission | Sandbox contract (G3) |
| `plan_checkpoint(state)` | Execution layer checkpoint (TTL-bound) | Tier matrix (G1) |
| `plan_replan(diff)` | Execution layer replan diff | Tier matrix (G1) |
| `session_end(reason)` | Enqueue Dream immediately (else idle-polled every 60s) | Rate limit only |

All results emit ECL v2.0 envelope sidecars with 11 required fields and SHA-256 integrity.

---

## The capability arc (W1 → W8)

CRYSTALIUM reached 1.0 over eight waves. The discipline throughout: **neuroscience-as-hypothesis, ablation-as-arbiter** — every augment is motivated by a neuroscience/philosophy anchor but ships **behind a default-OFF flag**, and flips ON only on a confound-free A/B win on its named metric. The citation generates the design; the bench is the arbiter.

| Wave | Theme | What landed |
|---|---|---|
| **W1** | Foundations & eval spine | Four-layer harness, one enforcement chokepoint, MCP stdio server, hybrid Aetheryte recall, ECL emission, EIIS install, the canary + ablation bench |
| **W2** | Importance as EVB | EVB importance scorer (Gain×Need, Mattar & Daw 2018) + `memory_dynamics` persistence |
| **W3** | The Dream becomes intelligent | Prioritized replay, CLS interleaving, synaptic-tagging consolidation (STC) |
| **W4** | Forgetting as a faculty | FSRS/DSR decay, value-aware eviction, spaced re-surfacing, Ricoeur-protected class, right-to-be-forgotten |
| **W5** | Retrieval intelligence (Aetheryte II) | Pattern completion, encoding-specificity re-rank, pattern-separation dedup-merge, predictive prefetch |
| **W6** | Security & integrity hardening | Belief-drift detection, quarantine triage, write-conflict detection, active-only recall, poisoning resistance |
| **W7** | Eidolons integration | `crystalium.ingest` (8th tool) + EIIS v1.4 finalization (host wiring, AGENTS.md frontmatter, standalone + multi-member verified) |
| **W8** | Conformance freeze & roster publication | `conformance` suite + blocking CI, honest canary repair, availability SLO, roster entry published |

---

## Ablation record (the methodology, honestly)

Across the six algorithmic waves (W2–W6), **8 augments were A/B-tested**. Net: **2 earned their flip ON; 6 stayed OFF as documented honest nulls.** None was shipped green by lowering a bar. The two ON-by-default behaviors are the only runtime change from v0.1:

| Flag | Wave | Verdict | Default |
|---|---|---|---|
| `write_dedup_merge` | W5 | **PASS** — write amp 1.0→0.667, precision held; confound-free (real bge-m3 on genuine paraphrases) | **ON** |
| `recall_active_only` | W6 | **PASS** — poisoning ASR 1.00→0.00, tier wall 8/8; also a correctness fix | **ON** |
| `evb_enabled` | W2 | INCONCLUSIVE (ties on DoD; the real precision effect the DoD's recall metrics can't see) | OFF |
| `dream_replay_evb` / `dream_interleave` / `dream_stc` | W3 | INCONCLUSIVE (coarse single-cluster `_gather` can't exercise per-fact dynamics) | OFF |
| `forgetting_fsrs` | W4 | INCONCLUSIVE (neither arm plateaus at default params over 24 ticks) | OFF |
| `recall_completion` / `recall_context_match` | W5 | INCONCLUSIVE (7-crystal fixture too small to create a graph-reachable gap) | OFF |
| `recall_prefetch` | W5 | PASS-BUT-CONFOUNDED (harness fed the exact verbatim future query) | OFF |
| `drift_detect` | W6 | OFF (cosine is a [PROXY] for contradiction — a genuine contradiction scored 0.696 < 0.80 band) | OFF |
| `write_conflict_detect` | W6 | OFF (gate doesn't isolate a conflict-specific win; LWW trust-inversion risk) | OFF |

The six nulls are not a weakness — they are the arbiter working. Each faculty ships fully tested behind its flag; what the synthetic v0.1 fixtures could not yet *discriminate* is documented as a backlog item with an exit criterion. Full table, marker legend, and per-wave reasoning: `DESIGN-RATIONALE.md` §D6.7. Per-gate A/B verdicts: `evals/BENCH-NOTES.md`. The "earn the flags" re-run plan: `ROADMAP-POST-1.0.md` §T2.

---

## Conformance & quality

- **`pytest -m conformance` is the contract:** green is conformant. It covers G1–G8 plus the mechanical invariants — path-escape guard, rate limit, ECL 11-field + SHA-256 integrity, trust-tier MIN propagation, never-hard-delete (except audited RTBF), and the working-set ≤3500 cap. Run as a blocking CI job.
- **`agent.md` ≤1,000 tokens** (CI-verified via tiktoken).
- **Composer working set ≤3,500 tokens** (G6 invariant, pinned to the literal 3500).
- **`install.sh` idempotent** (CI "second-run-no-diff" job).
- **Never hard-delete** except the one sanctioned, audited right-to-be-forgotten op.

---

## Honest 1.0 caveats

Reported as-is, not massaged to clear a gate (both deferred post-1.0, see `ROADMAP-POST-1.0.md`):

- **Canary below the 0.80 bar by one mission.** After an honest harness repair (de-vacuumed off-arm, fixed bit-rot + the double-run, episodic + isolated missions), memory-on beats memory-off **+0.75** (0.75 vs 0.00) — a full reversal of the prior −0.75 — but lands below 0.80 by exactly one mission: CAN-4, a recall-after-bi-temporal-update re-index `[GAP]` (the updated revision isn't re-embedded into the dense index).
- **Recall p95 ~205 ms** (embedder-bound — essentially one bge-m3 forward pass on CPU) vs the 200 ms target; availability 100% (≥99% ✓).

Both numbers are **[PROXY]** — from a synthetic harness, not production traffic.

---

## Operator surface

- **CLI:**
  - `crystalium forget <id> --reason …` (W4) — the one sanctioned, audited hard-delete (RTBF, T0).
  - `crystalium quarantine list` / `review <id> --accept|--reject --reason …` (W6) — triage the quarantine queue (T0, audited; reject soft-deprecates).
  - `crystalium promote list` / `review <id> [--accept|--reject]` — Semantic promotion inbox.
- **Install flags (EIIS v1.4):** `install.sh --version` / `--manifest-only` / `--hosts auto` / `--members`.
- **Config flags:** two default ON (`write_dedup_merge`, `recall_active_only`); the rest OFF. Each augment is toggleable via its `CRYSTALIUM_*` env var — see `MIGRATION.md` for the full per-wave key table.
- **Host wiring:** `hosts/claude-code.md`, `hosts/cursor.md`, `hosts/copilot.md`, `hosts/opencode.md`.

Key `crystalium.yaml` runtime knobs (full list in the spec §8):

- `transport: stdio` (HTTP stub raises `NotImplementedError`)
- `idle_threshold_s: 300` / `min_dream_gap_s: 1800` (Dream scheduling)
- `k_corroboration: 3` (independent T1+ witnesses for Semantic promotion)
- `human_confirm_default_window_days: 30` (post-install default-ON grace period)
- `skill_invoke.timeout_s: 30` / `skill_invoke.output_cap_bytes: 8192` (verifier bounds)
- Working-set slot caps: executive 300, procedural 600, semantic 800, episodic 800, execution 1000, buffer 300

---

## Roles

- **Operator:** installs CRYSTALIUM, configures `crystalium.yaml`, runs `docker compose`, reviews promotions and quarantine triage, holds the RTBF key.
- **Agent (T1+):** calls `recall`, `commit`, `ingest`, `skill_invoke` via MCP. Eidolons hand memory to each other through ECL envelopes.
- **Environment/tool (T3):** ingests environment facts into Episodic-quarantine; promotion requires operator or T1 review.

---

## Design principles

1. **One chokepoint.** Every write funnels through `enforcement.py` before any store mutation. No distributed guards.
2. **Constrained interfaces.** Pull-based (`recall`), not push-based; Dream proposes, never force-writes.
3. **Pointer-indexed.** Indices hold metadata; payloads live on a cheap content-addressed, immutable blob tier.
4. **Bounded working set.** Composer enforces ≤3,500 tokens with deterministic, importance-first eviction.
5. **Trust tiers propagate.** A consolidated fact takes the MIN trust tier of its inputs; blocks multi-agent poisoning.
6. **Local-first, container-first.** Fully self-hosted; all toolchain inside Docker; host runs only docker/git/make.
7. **Ablation-as-arbiter.** Neuroscience motivates; the A/B decides. Flags flip ON only on a confound-free win.

---

## Research foundation

- **Dual-process memory:** Teyler & DiScenna 1986; Teyler & Rudy 2007. Index → pointer → content is the hippocampal pattern.
- **Consolidation:** McClelland/McNaughton/O'Reilly 1995 (CLS); Tononi & Cirelli 2014. Sleep-like offline consolidation is cheaper than online learning.
- **Importance as EVB:** Mattar & Daw 2018 (Gain×Need prioritized replay).
- **Forgetting:** FSRS/DSR spaced-repetition decay; value-aware eviction.
- **Pattern separation / completion:** Yassa & Stark 2011; CA3 attractor dynamics; Tulving & Thomson 1973 (encoding specificity).
- **Bounded working set:** Baddeley & Hitch 1974. Slot allocation with deterministic eviction.
- **Write-gating:** O'Reilly & Frank 2006. Mechanical enforcement of access control.
- **Bi-temporal edits:** Zep/Graphiti (arXiv:2501.13956). Invalidate-old, write-new, never hard-delete.
- **Verifier-gated skills:** Voyager (arXiv:2305.16291) and the procedural-memory line.
- **Poisoning defense:** LTM Security Survey; PoisonedRAG/MINJA; OWASP ASI06.
- **Extended Mind:** Clark & Chalmers 1998 — CRYSTALIUM as the roster's external memory.

See `DESIGN-RATIONALE.md` for full citations and the marker legend (`[verified]` / `[MEDIUM]` / `[CONTESTED]` / `[UNVERIFIED]` / `[PROXY]` / `[GAP]`).

---

## Repository hierarchy

This repo is a published Eidolons roster member (like ATLAS, SPECTRA, APIVR-Δ); it also runs standalone. It depends on:

- **Rynaro/eidolons-eiis** — install contract (EIIS v1.4 conformance).
- **Rynaro/eidolons-ecl** — runtime communication contract (ECL v2.0 envelopes).
- **Rynaro/atlas-aci** — reference for the `enforcement.py` chokepoint pattern.
- **Rynaro/Junction** — harness used to orchestrate the build.
- **Rynaro/eidolons** — the nexus (CRYSTALIUM is a member, `capability_class: memory`).

---

## Hosts

Tested against:
- **Claude Code** — via `docker compose` command in `.mcp.json`
- **Cursor** — via `.cursor/mcp.json` wiring
- **Copilot** — via GitHub Copilot custom agents
- **opencode** — via `hosts/opencode.md` integration

See `hosts/*.md` for per-host setup guides.

---

## License

Apache-2.0. See LICENSE file.

---

## Development

All commands run inside the container. The `Makefile` is the host-visible wrapper:

```bash
make test          # docker compose run --rm crystalium pytest
make lint          # docker compose run --rm crystalium ruff check
make schema        # docker compose run --rm crystalium validate schemas
```

See `AGENTS.md` for the full developer onboarding.

---

## References

- **Methodology:** `CRYSTALIUM.md` (four-layer model, Dream consolidation, trust propagation, research anchors)
- **Design rationale:** `DESIGN-RATIONALE.md` (D1–D10 decisions; §D6.7 the consolidated ablation table + marker legend)
- **Migration:** `MIGRATION.md` (v0.1.0 → v1.0.0 per-wave config deltas; schema-v1-stable; the one behavior change)
- **Changelog:** `CHANGELOG.md` (the [1.0.0] + per-wave entries)
- **Post-1.0 roadmap:** `ROADMAP-POST-1.0.md` (the honest gap ledger the ablation discipline produced)
- **Bench notes:** `evals/BENCH-NOTES.md` (per-gate A/B verdicts, canary headline, availability SLO)
- **Developer standard:** `AGENTS.md` (build/test/lint, container-first, commit conventions)
- **Agent profile:** `agent.md` (always-loaded entry point, ≤1000 tokens)
- **Spec:** `.spectra/crystalium-v0.1.0-spec.md` (the decision-ready spec the build implemented wave-by-wave; frozen, historical pointer)
