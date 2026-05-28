# CRYSTALIUM v0.1.0 — Mission Brief (frozen at bootstrap)

> Source: user-provided bootstrap prompt, approved 2026-05-28. This file is the
> authoritative starting input for every Eidolon working on this repo. It is
> immutable until v0.2.0 — corrections go into `DESIGN-RATIONALE.md`.

## Identity

CRYSTALIUM is the **portable memory harness for the Eidolons** — a self-hosted,
vendor-agnostic, MCP-compatible memory substrate shared by a personal team of
single-purpose AI coding agents.

- **Crystal**: an admitted memory or skill in the lattice (record).
- **Aetheryte**: the recall/index network (hybrid retrieval surface).
- **Dream**: async consolidation worker (orient → gather → consolidate → prune).

CRYSTALIUM is **infrastructure, not an agent.** It stores, gates, retrieves,
consolidates, and forgets memory. It does not reason, plan, or write code.
Constrained interfaces beat raw autonomy (SWE-agent ACI; Agentless).

## Non-negotiables (P0 — do not re-litigate)

### Four layers

| Layer | Holds | Write path | Lifetime |
|---|---|---|---|
| Episodic | Past missions/sessions/PRs as **pointers** | Fast, ungated → `quarantined` | Long-term, Dream-pruned |
| Semantic | Project conventions, sigs, API facts | **Gated promotion** only | Indefinite, bi-temporal |
| Procedural | Verified, executable, reusable skills (crystals) | **Verifier-gated** admission only | Indefinite, utility-scored |
| Execution | In-flight plan, state, replan history | Every step, ephemeral | TTL, expires at task end |

### Keystone — one mechanical write/promote chokepoint

Model on `atlas-aci/enforcement.py`. Every `commit` / `update` / `promote`
funnels through one enforcement module before any store code runs.

Hard rules enforced at the chokepoint:

1. **Capture is ungated** into raw Episodic buffer, always
   `validation_state: quarantined`. Gate promotion, not capture.
2. **T3 (environment/tool-ingested) content writes ONLY Episodic-quarantined.**
   Never Semantic/Procedural directly. Promotion is the only path, and gated.
3. **Procedural admission requires the skill's `verifier` to pass in a
   sandbox** (reuse `atlas-aci` `test_dry_run` subprocess pattern).
4. **Semantic promotion requires ≥k independent corroborating sources OR
   human confirmation** (k configurable; human-confirm default ON for the
   first deployment month).
5. **Updates are bi-temporal**: invalidate-old (`t_valid_to = now`,
   `superseded_by = <new id>`), write-new with provenance.
   **Never hard-delete.** Rollback precondition.
6. **Trust tier carries through cross-agent reads and through summarization**
   — a consolidated crystal takes the **minimum** trust tier of its inputs,
   never resets to T1. Breaks multi-agent poison laundering.
7. **Path-traversal guard + per-process rate limit + telemetry on every
   call**, exactly as `atlas-aci` does.

### Index → pointer → content

Expensive stores (vector + graph) hold **only indices, metadata, and
structured facts** — the Aetheryte. Full episodic payloads live in a cheap
immutable blob/artifact tier on filesystem, content-addressed. Retrieval
scores on index, then resolves pointers to fetch content.

### Bounded, slotted working set (Baddeley)

Composer assembles **≤3,500 tokens** from typed slots with hard caps and
deterministic eviction. Slots:

- Executive (~300)
- Procedural (~600)
- Semantic (~800)
- Episodic (~800)
- Execution (~1000)
- Buffer (~300)

### Consolidation (Dream)

Async, off hot path. Triggers in order: (a) idle / end-of-session, then (b)
event-count threshold, then (c) nightly cron. Cycle: orient → gather →
consolidate → prune. Each phase token-bounded. May only *propose* Semantic
upserts; admission goes through the gate. Forgetting is importance-weighted
and audit-logged (SCM pattern), never blind LRU.

`importance = f(access_frequency, recency, outcome_success, novelty)` — same
function as the write-gate criterion.

### Scope + redaction

Every crystal carries `scope = (project, agent_class_visibility,
sensitivity_tag)`. Redactor between retrieval and LLM: regex pre-pass + a
small local LLM judge on sensitivity-tagged content. Re-redact at every
cross-agent handoff.

## Stack (local-first MVP)

Match `atlas-aci` style.

- Python ≥3.11 via `uv`. Apache-2.0.
- Transport: official `mcp` Python SDK; stdio for IDEs, Streamable-HTTP for CLI.
- Relational + sparse: SQLite + FTS5 (BM25).
- Vector: LanceDB (embedded).
- Graph: KuzuDB (embedded). Watch >10K-node telemetry.
- Blob: filesystem under `~/.crystalium/<project>/`, content-addressed.
- Embeddings: BGE-m3 via local Ollama if present, else `sentence-transformers`.
- Reranker: `bge-reranker-v2-m3`, optional, only when top-k > 20.
- Async worker: `apscheduler` (or `arq`).
- Contracts: Pydantic models mirroring JSON Schemas.
- Observability: `structlog` JSONL + OpenTelemetry.
- Redaction judge: small local model via Ollama (optional; regex-only fallback).

No hard deps on LangGraph, Postgres, Neo4j, or Redis in core.

## Container-first

**Every** toolchain dep installs inside Docker. Host runs only
`docker compose` + `git`. No host-side `pip`, `uv`, `python -m`, `pytest`,
or model downloads.

## Schemas

Six JSON Schema Draft 2020-12 emitted now:

- `crystal.v1.json` (memory record / lattice node)
- `skill.v1.json` (procedural crystal frontmatter)
- `recall-request.v1.json` / `recall-result.v1.json`
- `commit-request.v1.json` / `commit-result.v1.json`

Full field sets per §3 of bootstrap prompt — even where v0.1 only reads a subset.

## MCP tool surface

On-demand loading (Anthropic "Code execution with MCP" pattern):

- `crystalium.recall(scope, query, k, layers=[...])`
- `crystalium.commit(layer, payload, provenance)`
- `crystalium.update(id, patch, reason)`
- `crystalium.skill_invoke(name, args)` — sandbox
- `crystalium.plan_checkpoint(state)` / `crystalium.plan_replan(diff)`

Resources: `project_conventions`, `agents_roster`, `sensitivity_policy`.
Prompts: `onboard_new_agent`, `audit_memory`, `redact`.

## Quality bars

- `agent.md` ≤ ~1,000 tokens; composer-proven ≤3,500.
- Every §2.2 invariant has a passing test in `test_enforcement.py`. Any
  failure marks server non-conformant.
- `install.sh` idempotent (CI second-run-no-diff job).
- DESIGN-RATIONALE.md cites every non-obvious decision. Anchor list:
  - Index→pointer→content: Teyler & DiScenna 1986; Teyler & Rudy 2007
  - Dual-speed + consolidation: McClelland/McNaughton/O'Reilly 1995;
    Tononi & Cirelli 2014; Sleep-time Compute (arXiv:2504.16891);
    SCM (arXiv:2604.20943)
  - Slot-bounded working set: Baddeley & Hitch 1974
  - Write-gating: O'Reilly & Frank 2006
  - Bi-temporal edits: Zep/Graphiti (arXiv:2501.13956)
  - Edit primitives: Mem0 (arXiv:2504.19413)
  - Verifier-gated skills: Voyager (arXiv:2305.16291); ProcMEM
    (arXiv:2602.01869); SkillGen (arXiv:2605.10999)
  - Poisoning defense: LTM Security Survey (arXiv:2604.16548); OWASP ASI06
  - Constrained-over-autonomous: SWE-agent (arXiv:2405.15793); Agentless
  - Mark any claim you cannot verify as `[UNVERIFIED]`.
- Memory-on/off A/B is the headline metric. If memory does not beat
  no-memory on canaries, say so plainly.
- CHANGELOG.md under v0.1.0, Keep-a-Changelog.
- Conventional Commits, branch `feat/crystalium-v0.1.0`, no push, no PR.

## Out of scope for v0.1 (hooks left, do NOT build)

- Polyglot skill abstraction (hook: `language`, `capability_class`).
- Learned/adaptive importance weights (hook: `importance_score()` signature).
- Belief-drift detection (hook: provenance + audit log).
- Quarantine review UI (hook: `validation_state: quarantined`).
- Server profile (Postgres/Qdrant/Neo4j) and any LangGraph adapter.
- In-weights / LoRA consolidation.
- Multi-agent CRDT/consensus — append-only + content-addressed +
  last-write-wins with `superseded_by` is sufficient for v0.1.
- REM-style associative linkage; optimal Dream cadence tuning.

## Security & privacy surface

For each layer, DESIGN-RATIONALE.md §Security states: what context it
consumes, where; what it persists, where, how long; what external calls it
makes (default: none — fully local); failure modes; mitigations.

Treat the MCP server as a trust boundary. For untrusted models the operator
must sandbox `skill_invoke` and Dream worker at OS level (DevContainer /
microVM). CRYSTALIUM enforces what it can mechanically; cannot enforce OS
isolation by itself.

## Repo hierarchy this bootstrap respects

- `Rynaro/eidolons-eiis` — install contract (EIIS v1.4)
- `Rynaro/eidolons-ecl` — runtime communication contract
- `Rynaro/atlas-aci` — reference for enforcement.py chokepoint pattern
- `Rynaro/Junction` — harness used to orchestrate the build
- `Rynaro/eidolons` — nexus that will publish a roster entry for CRYSTALIUM
  once stable (post v0.1.0)
