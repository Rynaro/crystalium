# CRYSTALIUM

Portable memory harness for the Eidolons. Stores, gates, retrieves, consolidates, and forgets memory. Does not reason, plan, or write code.

## Status

**v1.0.0 — roster member** of the [Eidolons](https://github.com/Rynaro/eidolons) roster (capability class: `memory`). The shared memory substrate the team writes handoff artifacts into and recalls from. EIIS v1.4 + ECL v2.0 conformant; `pytest -m conformance` green (a blocking CI gate). Container-first (all Python toolchain runs inside Docker; host runs only `docker compose`, `git`, `make`).

---

## Architecture (10-second version)

```
HOST (operator only: docker, git, make)
    │
    ├─→ docker compose run --rm crystalium
    │
    ├─→ MCP stdio server (JSON-RPC 2.0) — 8 tools
    │
    ├─→ enforcement.py (CHOKEPOINT)
    │   • assert_tier_allowed (tier × layer × op matrix)
    │   • assert_no_path_escape (symlink + traversal guard)
    │   • assert_rate_limit (200 calls/min sliding window)
    │   • record telemetry (structlog JSONL + OpenTelemetry)
    │
    ├─→ Four memory layers
    │   • Episodic (pointers, T3 → quarantine-by-default)
    │   • Semantic (gated promotion, indefinite)
    │   • Procedural (verifier-gated, skills)
    │   • Execution (ephemeral, TTL)
    │
    ├─→ Aetheryte (hybrid recall, RRF-fused)
    │   • BM25 (SQLite + FTS5, sparse)
    │   • Vector (LanceDB, dense)
    │   • Graph (KuzuDB, structured facts)
    │
    └─→ Dream worker (async, idle-triggered)
        • Orient → Gather → Consolidate → (drift-check) → Prune
        • Runs outside MCP request context
        • Proposes (never force-writes)

Storage:
  • Indices + metadata in SQLite/LanceDB/KuzuDB (queryable, cacheable)
  • Episodic payloads on filesystem (content-addressed, immutable)
  • All under ~/.crystalium/<project>/ (portable, local-first)

Every tool result emits an ECL v2.0 envelope sidecar (11 fields + SHA-256 integrity).
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

The server listens on stdin/stdout. Wire it to Claude Code, Cursor, Copilot, or opencode via the host's MCP config:

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

**No host `pip`, `uv`, `python`, or `pytest` invocations.** All dev commands run inside the container via `docker compose run --rm crystalium <cmd>` or `make` targets. See `hosts/*.md` for per-host setup.

---

## What it gives the Eidolons

- **Memory across sessions.** Episodic recall of past missions, PRs, and outcomes.
- **Consolidated facts.** Semantic layer curates API signatures, project conventions, design decisions.
- **Verified skills.** Procedural layer stores tested recipes and proven patterns; a verifier gates admission.
- **Handoff ingestion.** `ingest` accepts upstream roster ECL handoff envelopes (v1.x **and** v2.x), maps them to canonical `crystal.v1`, and preserves provenance + the MIN trust tier — a T3/tool-origin artifact lands episodic-quarantined, never laundered to a higher tier.
- **Bounded working set.** The composer enforces a ≤3,500-token budget with deterministic eviction, freeing the agent to reason.
- **Trust propagation.** Multi-agent consolidation takes the MIN trust tier of inputs, blocking poison laundering.

---

## What it doesn't do

- **Reason.** No inference loop, no planning, no goal-seeking — reasoning lives in the consuming Eidolon.
- **Provide raw autonomy.** Constrained interfaces: agents call `recall`; Dream proposes, never force-writes.
- **Require external services.** Fully self-hosted (SQLite, LanceDB, KuzuDB embedded); optional local Ollama for redaction.

---

## Tool surface (8 MCP tools)

| Tool | Purpose | Gated by |
|---|---|---|
| `recall(scope, query, k, layers)` | Hybrid BM25+vector+graph retrieval, RRF-fused, slot-budgeted | Rate limit only |
| `commit(layer, payload, provenance)` | Write with tier enforcement + bi-temporal tracking | Tier matrix (G1–G4) |
| `ingest(envelope, payload)` | Ingest a roster ECL handoff (v1.x/v2.x) → crystal, preserving provenance + MIN tier | Tier matrix (through commit) |
| `update(id, patch, reason)` | Field edits with invalidate-old; never hard-delete | Tier matrix (G1, G4) |
| `skill_invoke(name, args)` | Sandbox verifier for procedural admission | Sandbox contract (G3) |
| `plan_checkpoint(state)` | Execution layer checkpoint (TTL-bound) | Tier matrix (G1) |
| `plan_replan(diff)` | Execution layer replan diff | Tier matrix (G1) |
| `session_end(reason)` | Enqueue Dream immediately (else idle-polled every 60s) | Rate limit only |

All results emit ECL v2.0 envelope sidecars with 11 required fields and SHA-256 integrity.

---

## The capability arc (W1–W8)

CRYSTALIUM reached v1.0.0 over eight waves. Every algorithmic augment ships **behind a default-OFF config flag** and flips ON **only** on a confound-free A/B win (ablation-or-revert). The neuroscience/philosophy anchor is the *generative hypothesis*; the **A/B is the arbiter**.

| Wave | Version | What it added |
|---|---|---|
| W1 | 0.2.0 | Foundations + eval spine, container-first hook, four-layer model |
| W2 | 0.3.0 | EVB importance (Gain×Need; Mattar & Daw 2018) + `memory_dynamics` |
| W3 | 0.4.0 | Dream consolidation — prioritized replay, CLS interleave, synaptic-tagging |
| W4 | 0.5.0 | Forgetting faculty — FSRS/DSR decay, value-aware eviction, Ricoeur-protected class, right-to-be-forgotten |
| W5 | 0.6.0 | Retrieval "Aetheryte II" — pattern completion, encoding-specificity, **dedup-merge**, predictive prefetch |
| W6 | 0.7.0 | Security hardening — belief-drift detection, quarantine triage, write-conflict detection, **active-only recall**, poisoning resistance |
| W7 | 0.8.0 | Eidolons integration — `crystalium.ingest` + EIIS finalization |
| W8 | 1.0.0 | Conformance freeze + roster publication |

**Ablation record — 2 augments earned their default-on flip; 6 stayed off as honest nulls.** Full table in `DESIGN-RATIONALE.md` §D6.7; per-wave A/B verdicts in `evals/BENCH-NOTES.md`.

| Default ON (A/B win) | Default OFF (documented nulls) |
|---|---|
| `write_dedup_merge` — near-duplicate writes merge; write amplification 1.0→0.667, precision held (W5) | EVB, the three Dream augments, FSRS forgetting, pattern completion + context-match, predictive prefetch, drift detection, write-conflict detection |
| `recall_active_only` — recall excludes deprecated/superseded; poisoning ASR 1.00→0.00, tier wall 8/8, and a correctness fix (W6) | — inconclusive or confounded on the synthetic fixtures; reported honestly, kept off, fully tested |

**Honest 1.0 caveats** (tracked in `ROADMAP-POST-1.0.md`):

- **Canary:** after an honest harness repair, memory-on beats memory-off **+0.75** (3/4 vs 0/4 missions) — a full reversal of the prior −0.75 — but lands **below the 0.80 bar by one mission** (a recall-after-bi-temporal-update re-index `[GAP]`). Reported as-is, not massaged.
- **Availability SLO:** recall availability **100%** (≥99% target met); recall p95 **~205 ms**, marginally over the 200 ms target (embedder-bound). Both `[PROXY]` on a synthetic harness.

---

## Configuration

Set via `CRYSTALIUM_*` environment variables or a YAML config. Defaults reflect the v1.0 ablation outcome.

**Transport / storage:**

- `CRYSTALIUM_TRANSPORT` — `stdio` (default) or `http`
- `CRYSTALIUM_DATA_DIR` — local store path (default `~/.crystalium/`)
- `CRYSTALIUM_RATE_LIMIT_PER_MINUTE` — sliding-window cap (default 200)

**Augment flags** (each behind ablation-or-revert):

- **Default ON:** `CRYSTALIUM_WRITE_DEDUP_MERGE` (W5), `CRYSTALIUM_RECALL_ACTIVE_ONLY` (W6)
- **Default OFF:** `CRYSTALIUM_EVB_ENABLED` (W2); `CRYSTALIUM_DREAM_REPLAY_EVB` / `CRYSTALIUM_DREAM_INTERLEAVE` / `CRYSTALIUM_DREAM_STC` (W3); `CRYSTALIUM_FORGETTING_FSRS` (W4); `CRYSTALIUM_RECALL_COMPLETION` / `CRYSTALIUM_RECALL_CONTEXT_MATCH` / `CRYSTALIUM_RECALL_PREFETCH` (W5); `CRYSTALIUM_DRIFT_DETECT` / `CRYSTALIUM_WRITE_CONFLICT_DETECT` (W6)

**Promotion / consolidation knobs:** `k_corroboration: 3` (independent T1+ witnesses for Semantic promotion), `idle_threshold_s: 300`, `min_dream_gap_s: 1800`, `skill_invoke.timeout_s: 30`, working-set slot caps (executive 300 / procedural 600 / semantic 800 / episodic 800 / execution 1000 / buffer 300, total ≤3,500). See `MIGRATION.md` for the full per-wave config-key delta.

---

## Roles & operator surface

- **Operator (T0, human):** installs CRYSTALIUM, reviews promotions, runs triage + the audited erase path, sets policy.
- **Eidolon (T1–T3):** calls `recall` / `commit` / `ingest` / `skill_invoke` via MCP; the trust tier gates writes. Eidolons hand off memory to each other through ECL envelopes.
- **Environment/tool (T3):** facts land in Episodic-quarantine; promotion requires operator or T1 review.

Operator CLI:

```bash
crystalium forget <id> --reason "<text>"                          # audited hard-delete (RTBF; T0-only — the one exception to never-hard-delete)
crystalium quarantine list                                        # triage queue over quarantined crystals
crystalium quarantine review <id> --accept|--reject --reason "…"  # accept = clear; reject = soft-deprecate
crystalium promote list / review <id> --accept|--reject           # the human-confirm promotion queue

./install.sh --version | --manifest-only | --hosts auto | --members "atlas,crystalium"   # EIIS install
```

---

## Design principles

1. **One chokepoint.** Every write funnels through `enforcement.py` before any store mutation. No distributed guards.
2. **Constrained interfaces.** Pull-based (`recall`), not push-based; Dream proposes, never force-writes.
3. **Pointer-indexed.** Indices hold metadata; payloads live on the cheap blob tier (content-addressed, immutable).
4. **Bi-temporal, never-hard-delete** — except the one audited, operator-gated right-to-be-forgotten path.
5. **Trust tiers propagate.** Consolidated fact takes the MIN trust tier of inputs; blocks multi-agent poisoning.
6. **Local-first, container-first.** Fully self-hosted; all toolchain inside Docker; host runs only docker/git/make.
7. **Ablation-or-revert.** Every augment ships behind a default-off flag; flips on only on a confound-free A/B win; nulls reported honestly.

---

## Conformance & quality bars

`pytest -m conformance` is the single "green is conformant" target — a **blocking** CI gate covering every mechanical invariant:

- **G1/G2** tier × layer × op matrix (write-gating) · **G3** skill sandbox · **G4** trust-tier MIN propagation · **G5** promotion gate · **G6** working-set ≤3,500 composer · **G7** ECL 11-field + SHA-256 integrity · **G8** Dream dedup
- path-escape guard · rate limit · never-hard-delete (P0-5) + the audited RTBF exception · working-set cap pinned at 3,500

Plus: `agent.md` ≤1,000 tokens (tiktoken, enforced), `install.sh` idempotent (CI second-run-no-diff), `DESIGN-RATIONALE.md` ≥10 cited decisions with `[verified]`/`[UNVERIFIED]`/`[PROXY]` markers.

---

## Research foundation

- **Dual-process memory:** Teyler & DiScenna 1986; Teyler & Rudy 2007. Index → pointer → content is the hippocampal pattern.
- **Consolidation (CLS):** McClelland/McNaughton/O'Reilly 1995; Tononi & Cirelli 2014. Offline consolidation is cheaper than online learning.
- **Importance as Expected Value of Backup:** Mattar & Daw 2018 (Gain × Need).
- **Spaced forgetting:** FSRS-DSR; pattern separation/completion (Yassa & Stark 2011; Marr/Rolls); encoding specificity (Tulving & Thomson 1973).
- **Bounded working set:** Baddeley & Hitch 1974. Slot allocation with deterministic eviction.
- **Write-gating:** O'Reilly & Frank 2006. Mechanical enforcement of access control.
- **Bi-temporal edits:** Zep/Graphiti. Invalidate-old, write-new, never hard-delete.
- **Poisoning defense:** OWASP ASI06; PoisonedRAG; MINJA; A-MemGuard.
- **The Extended Mind:** Clark & Chalmers 1998 — a reliably-available, endorsed store is constitutive of the team's cognition (the W7 integration anchor).

See `DESIGN-RATIONALE.md` for full citations and the marker legend. **Neuroscience = generative hypothesis; the ablation A/B is the arbiter.**

---

## Repository hierarchy

This repo is a roster member (capability class `memory`); it also runs standalone.

| Path | What it is |
|---|---|
| `agent.md` | always-loaded profile (≤1,000 tokens) |
| `AGENTS.md` | developer standard (EIIS v1.4; YAML frontmatter with `handoffs.upstream/downstream`) |
| `CRYSTALIUM.md` | methodology — four-layer model, Dream, trust propagation |
| `DESIGN-RATIONALE.md` | D1–D10 decisions + §D6.7 the 8-result ablation summary table |
| `CHANGELOG.md` · `MIGRATION.md` · `ROADMAP-POST-1.0.md` | release notes · upgrade notes · the post-1.0 gap ledger |
| `.spectra/crystalium-v0.1.0-spec.md` | the frozen v0.1 decision-ready spec |
| `mcp-server/` | MCP server, enforcement chokepoint, layers, storage, tests |
| `evals/` | ablation harness + `BENCH-NOTES.md` (per-wave A/B verdicts) |
| `schemas/` · `skills/` · `hosts/` · `docs/` | crystal/ECL/manifest schemas · verifier skills · host wiring · roster-PR draft |

Depends on: **Rynaro/eidolons** (nexus — CRYSTALIUM is a published member), **Rynaro/eidolons-eiis** (install contract, EIIS v1.4), **Rynaro/eidolons-ecl** (ECL v2.0), **Rynaro/atlas-aci** (chokepoint reference pattern).

---

## Hosts

End-to-end setup guides in `hosts/`:

- **Claude Code** — `hosts/claude-code.md` (project `.mcp.json`)
- **Cursor** — `hosts/cursor.md` (`.cursor/mcp.json`)
- **GitHub Copilot** — `hosts/copilot.md` (`.vscode/mcp.json`)
- **opencode** — `hosts/opencode.md`

The Docker stdio launch command is `docker compose run --rm -i crystalium python -m crystalium serve`. `install.sh --hosts auto` writes/merges the host MCP config idempotently.

---

## Development

All commands run inside the container. The `Makefile` is the host-visible wrapper:

```bash
make test                  # full suite
make lint                  # ruff
make schema                # validate schemas
docker compose run --rm crystalium pytest -m conformance   # the conformance gate
```

See `AGENTS.md` for the full developer standard and `CLAUDE.md` for Claude Code integration. **Do not** invoke host `python`/`pip`/`uv`/`pytest` — a PreToolUse hook blocks it.

---

## License

Apache-2.0. See [LICENSE](LICENSE).

---

## References

- **Roster / nexus:** [Rynaro/eidolons](https://github.com/Rynaro/eidolons) — CRYSTALIUM is the `memory`-class member
- **Methodology:** `CRYSTALIUM.md`
- **Design rationale + ablation table:** `DESIGN-RATIONALE.md` (§D6.7)
- **Upgrade + migration:** `MIGRATION.md` · **Changelog:** `CHANGELOG.md` · **Forward roadmap / gaps:** `ROADMAP-POST-1.0.md`
- **Frozen v0.1 spec:** `.spectra/crystalium-v0.1.0-spec.md`
- **Bench results:** `evals/BENCH-NOTES.md`
- **EIIS v1.4 / ECL v2.0:** `Rynaro/eidolons-eiis` · `Rynaro/eidolons-ecl`
