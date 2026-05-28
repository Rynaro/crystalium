# CRYSTALIUM Methodology

## Why CRYSTALIUM exists

AI agents solving coding tasks forget between sessions. Without continuity of episodic context (past PRs, session state, tried approaches), each agent starts cold. Without canonicalized semantic facts (API signatures, project conventions), each agent rediscovers the same patterns. Without verified procedural skills (tested refactoring recipes, proven patterns), each agent makes the same mistakes.

CRYSTALIUM is a portable memory substrate — self-hosted, vendor-agnostic, MCP-compatible — that lets a team of single-purpose agents share a stable working memory. It is **not** an agent itself. It stores (gated), retrieves (hybrid), consolidates (async), and forgets (weighted). The agents consume it; the operator controls it.

The research foundation rests on two observations. First, the hippocampal model of dual-process memory (index → pointer → content) is more efficient than full-content retrieval at scale. The Aetheryte indices (BM25, vector, graph) are pointers to the actual episodic payloads, stored cheaply on filesystem as content-addressed blobs. Second, sleep-like consolidation (offline processing during idle) is cheaper than synchronous online learning. Dream runs asynchronously, outside the hot path, proposing Semantic candidates that the operator (or a T1 verifier) admits.

---

## The four-layer model

CRYSTALIUM separates memory by **age** (Episodic ← recent, Semantic ← consolidated) and **trust** (Episodic/Semantic are facts; Procedural is executable). The matrix in SPEC.md §4 is the contract.

**Episodic layer** (pointer-indexed, quarantine-by-default): past missions, session logs, PR references, outcomes. Written fast and ungated, but marked `validation_state = "quarantined"` if sourced from T3 (environment/tool ingestion). Promotion to higher trust requires corroboration or human review. Dream periodically forgets old episodic records weighted by importance.

**Semantic layer** (vector+graph indexed, Promotion-gated): curated facts — API signatures, project structure, naming conventions, design decisions. Written only through promotion gate, which requires ≥k independent T1+ corroborations OR human-confirm. Once admitted, Semantic records persist indefinitely with bi-temporal edit tracking (old record marked superseded, new record linked back). Semantic ceiling is T2 trust — T3 input can never directly commit to this layer.

**Procedural layer** (bytecode indexed, Verifier-gated): reusable skills — tested refactoring recipes, proven lint fixes, reproducible pattern matches. T1/T2 can write as "candidate"; promotion to "admitted" requires the skill's verifier (a subprocess running in a sandbox) to pass. Once admitted, the skill is runnable. Forgotten per importance score; executors can query by `skill_id` and invoke the verifier in a subprocess (30s timeout, 8 KiB output cap).

**Execution layer** (transient, TTL-bound): in-flight plan state, replan diffs, task checkpoints. Written synchronously on every planning step. Expires at task end or after fixed TTL; not persisted across sessions. Intentionally ephemeral — Execution is the agent's working register, not a memory tier.

---

## The keystone — one chokepoint

Model on `atlas-aci/enforcement.py`. Every write, promotion, or update funnels through one `Enforcement` class before any store code runs. Three pre-checks:

1. **assert_tier_allowed(tool, layer, tier, op)** — the tier × layer × operation matrix (SPEC.md §4) is a lookup table. The table rejects (tier, layer, op) tuples that violate P0. For example, T3 attempting to commit to Semantic is denied at this point, before any SQLite write.

2. **assert_no_path_escape(target_dir)** — resolve symlinks, check `relative_to(repo_or_sandbox)`, raise if escape attempt. Blob tier and `/sandbox/<skill_id>` directories are separate path prefixes guarded independently.

3. **assert_rate_limit()** — sliding 60-second window, default 200 calls/minute. Per-process, in-memory deque; overflow raises soft error with Retry-After hint.

All three run before the tool impl sees the request. Every call is recorded in telemetry (`structlog` JSONL + OpenTelemetry). This pattern is identical to atlas-aci; reuse its code and tests as a template.

Why one chokepoint instead of distributed guards? One location = one place to audit = one place to lock down the whole access control surface. If a tool author forgets to call `assert_rate_limit`, the chokepoint catches it anyway.

---

## Index → pointer → content

The expensive stores (LanceDB for vectors, KuzuDB for graph, full-text indices in SQLite+FTS5) hold **indices, metadata, and structured facts only**. The episodic payloads themselves — past session logs, PR threads, past mission outputs — live in an immutable blob tier under `~/.crystalium/<project>/`, keyed by content SHA-256.

On recall, the Aetheryte queries the indices and returns scored metadata tuples. Each tuple carries a `content_pointer` (blob SHA-256 or artifact path). The LLM-facing response resolves those pointers to fetch the actual content, then redacts sensitive fields before returning.

This design scales: a thousand episodic records weigh ~100 MiB in SQLite indices + FTS5 sparse embeddings, but the actual payloads (session logs, PR diffs) weigh ~10 GiB on disk. Indices are fast and cacheable; blobs are cheap and immutable.

The dual-tier design also preserves audit trails. A record's pointer history is queryable (search_symbol pattern: "who has read this secret?"), but the secret itself is compartmentalized.

---

## Bounded working set (Baddeley model)

The composer assembles the LLM-visible working set from the four layers, subject to hard token caps per slot.

| Slot | Cap | Purpose |
|---|---|---|
| executive | 300 | top-level goal + immediate subgoals |
| procedural | 600 | skills (verifier code + metadata) |
| semantic | 800 | facts (conventions, signatures) |
| episodic | 800 | memory (past outcomes, session state) |
| execution | 1000 | plan state (current checkpoint + alternatives) |
| buffer | 300 | scratch (temp findings, notes) |
| **total** | **3500** | hard limit |

When a slot would exceed its cap, the composer evicts the lowest-importance records (importance = access_frequency + recency + outcome_success + novelty). The eviction is deterministic — same inputs → same kept set, identical order — so the working set is reproducible across agent invocations.

The importance function is frozen at the signature level (SPEC.md §15 hook); only the weights tuple can be tuned post-v0.1. This is the entry point for D11 (learned/adaptive weights, deferred).

---

## The Dream cycle

Consolidation runs asynchronously, off the hot path, triggered in order:

1. **Idle-poll** — every 60 seconds, the scheduler checks `now - last_activity ≥ 300s AND now - last_dream ≥ 1800s`. If both true, enqueue one Dream run.
2. **Explicit session_end** — when the host calls `crystalium.session_end`, enqueue immediately.
3. **Event-count threshold** — if N commits/recalls have accumulated since last Dream, enqueue.

All three paths share one enqueue mechanism keyed by `dream_run_id`; concurrent triggers dedup to a single execution.

Dream's cycle is: **orient → gather → consolidate → prune**.

- **Orient**: examine what happened in the last session (commits, recalls, skill invocations). Identify candidate facts worth promoting from Episodic → Semantic.
- **Gather**: read the candidate facts and nearby indexed neighbours (vector + graph similarity).
- **Consolidate**: call a local LLM (or rule-based distiller) to synthesize a new Semantic fact (e.g. "whenever pattern X appears in the test suite, fix with refactoring Y").
- **Prune**: forget low-importance episodic records, using the same `importance_score` function as the eviction rule.

Consolidation **proposes** (enqueues in `pending_promotions`); it does NOT write directly to Semantic. The proposal sits in an inbox until a T0 operator or T1 verifier calls `crystalium promote review <id> --accept`. This keeps Dream fast (no blocking on human confirmation) and auditable (every fact has a logged promotion step).

The operator can tune Dream's phase budgets (e.g., "consolidate phase ≤ 500 tokens"), preventing the worker from dominating CPU during long offline phases.

---

## Scope + redaction

Every crystal carries `scope = (project, agent_class_visibility, sensitivity_tag)`. The recall filter applies scope rules before the response reaches the LLM. Redaction has two passes:

1. **Regex pre-filter** — strip known patterns (API keys, auth tokens, internal IPs).
2. **Small-LLM judge** — if a record is tagged `sensitivity = "high"` or `"pii"`, run a local LLM classifier to flag potential leaks. Conservative: low false-negative risk; higher false-positive rate is acceptable.

Redaction re-applies at every cross-agent handoff (when CRYSTALIUM emits a result to another Eidolon via ECL envelope). This is a defense-in-depth layer: even if one agent is untrusted, the memory substrate polices what it sees.

---

## Constrained interfaces over autonomy

CRYSTALIUM is infrastructure, not an autonomous system. The tool surface is **pull, not push**. Agents call `recall` when they need memory; CRYSTALIUM does not proactively interrupt with "you might want to remember X." This mirrors the SWE-agent ACI thesis: a tool that does less, mechanically, is more trustworthy than a tool that tries to be smart.

Similarly, Procedural skills are **not auto-invoked**. An agent calls `skill_invoke(skill_id, args)` explicitly. The operator or a T1 verifier gates admission — CRYSTALIUM does not infer "this task looks like a refactoring; I'll invoke the refactoring skill." Constrained interfaces let the agent retain agency.

---

## What CRYSTALIUM is NOT

- **Not an agent.** It has no reasoning loop, no planning, no goal-seeking. It stores, retrieves, consolidates, and forgets.
- **Not a knowledge graph reasoner.** The graph tier (KuzuDB) is an index, not a constraint solver or inference engine.
- **Not a vector database.**  LanceDB is one index tier; BM25 and relational queries are equally valid.
- **Not a training system.** Importance weights are static in v0.1; no gradient-based adaptation. Dream proposes, does not auto-learn.
- **Not a language model.** Redactor uses a small local model (Ollama, optional); the harness itself is code, not neural.

---

## Operating envelope

**Local-first, container-first.** All toolchain (Python, uv, pytest, embeddings, storage engines) runs inside a Docker container. The host runs only `docker compose`, `git`, and `make`. No host `pip`, `uv`, `python`, or pytest invocations.

**Fully self-hosted.** CRYSTALIUM has no hard dependencies on Postgres, Qdrant, Neo4j, LangGraph, or any external service. SQLite + LanceDB + KuzuDB are embedded; embeddings come from local Ollama (optional) or sentence-transformers (fallback).

**Vendor-agnostic.** The MCP transport is stdio JSON-RPC (works with Claude Code, Copilot, Cursor); the wire format is ECL v2.0 (works with any Eidolon). Swapping the LLM or the Eidolons hosting the agent does not require modifying CRYSTALIUM.

**Portable.** The entire database lives under `~/.crystalium/<project>/`, a single directory portable across machines. A `tar.gz` of that directory is a complete project snapshot.
