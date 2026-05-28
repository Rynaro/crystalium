# DESIGN-RATIONALE.md

This document traces every non-obvious decision in CRYSTALIUM v0.1.0 to its source. Every claim not verifiable from the source workspace is marked `[UNVERIFIED]`.

---

## FORGE Decisions D1–D10

### D1. Enforcement tier-mapping logic

**Decision:** Define `assert_tier_allowed(tool, layer, tier, op)` with a (layer × op) matrix keyed by trust tier, rejecting any (tier, layer, op) before any store mutation.

**Rationale:** The four trust tiers (T0=human, T1=verified-agent, T2=unverified-agent, T3=environment/tool) have different write capabilities. T3 cannot commit above Episodic; T2 cannot force-promote; only T0 can force-promote directly. A single matrix lookup is faster and more auditable than scattered per-tool guards.

**Source:** `.atlas/scout-report.md` FINDING-001 (atlas-aci enforcement.py pattern); `.forge/reasoning-report.md` D1.

**Trade-off accepted:** Matrix is static in v0.1. Future tiers (T1.5 = "partially-verified"?) would require a matrix rebuild. Operator cannot tune per-tool rules; all tools are subject to the same matrix.

**Reversibility:** A v0.2 refactor can split the matrix (per-tool rules, matrix-backed defaults) without breaking the public tool surface. The chokepoint location (before any store code) is locked; the rule shape is not.

---

### D2. Streamable-HTTP scope for v0.1

**Decision:** Stdio-only. HTTP transport raises `NotImplementedError("v0.2")` behind an env var flag.

**Rationale:** Hosts in scope (Claude Code, Cursor, Junction) all support stdio. HTTP adds auth/CORS/bind-address surface without unlocking a covered host. v0.1's risk is chokepoint correctness; multiplying test combinations (each tool × each transport × each tier) delays stability.

**Source:** `.forge/reasoning-report.md` D2 (risk analysis); FINDING-004 (Junction stdio-only).

**Trade-off accepted:** HTTP deferred to v0.2. Users cannot run CRYSTALIUM server on a port; they must use stdio + composer wrapping if multi-client access is needed (future).

**Reversibility:** Feature flag keeps the door open. Adding HTTP in v0.2 requires only the flag branch implementation (MCP SDK provides the `streamable_http_server` context manager).

---

### D3. Dream idle-trigger

**Decision:** Both. Explicit `session_end` tool + idle-poll every 60s (fire if idle ≥300s AND last Dream ≥1800s). Single enqueue path; dedup by `dream_run_id`.

**Rationale:** Explicit tool alone breaks if the host crashes or doesn't call it. Idle-poll alone has latency floor and wastes cycles. Both together: poll is the safety net, explicit is the fast path. Dedup ensures exactly one run enqueues.

**Source:** `.forge/reasoning-report.md` D3 (belt-and-suspenders analysis); `.atlas/scout-report.md` GAP-002 (no MCP session_end notification in spec).

**Trade-off accepted:** Two enqueue paths to manage. Dedup logic must be atomic (use a lock).

**Reversibility:** Can drop idle-poll in v0.2 if the host ecosystem standardizes on session_end callbacks. The explicit tool is the permanent API.

---

### D4. ECL v0.1 opt-in scope

**Decision:** Declare `ECL_VERSION = 2.0` in v0.1. Every tool result emits envelope sidecar with 11 required fields.

**Rationale:** CRYSTALIUM's entire value is multi-agent trust propagation. Without envelopes, trust_tier rides as ad-hoc JSON — exactly what ECL exists to standardize. Deferring creates v0.2 retrofit (touches every tool result builder). Declaring now: install.sh copies one line; envelope construction is ~30 LOC using stdlib hashlib.

**Source:** `.forge/reasoning-report.md` D4 (value proposition); FINDING-002 (ECL v2.0 shape); FINDING-003 (EIIS §3.7.1 obligation).

**Trade-off accepted:** All tool results now carry envelope sidecars. Operators must handle the extra files (mirrored ECL archives alongside payloads). Envelope validation is a new test burden.

**Reversibility:** Cannot opt-out in v0.2 without breaking downstream Eidolons that expect envelopes. The choice is permanent.

---

### D5. skill_invoke sandbox v0.1 contract

**Decision:** Subprocess inside crystalium container (already the trust boundary). Mount skill blobs read-only; enforce cwd under `/sandbox/<skill_id>`; 30s timeout; 8 KiB output cap. Operator warning (soft sandbox; OS isolation is operator's responsibility).

**Rationale:** Container-first rule (host runs only docker/git) makes the container the sandbox boundary. subprocess-inside-container ≈ 10× faster than docker-run-per-invocation and no socket exposure. Read-only mounts + cwd guard + output cap are the mechanical constraints v0.1 enforces.

**Source:** `.forge/reasoning-report.md` D5 (isolation model); `.atlas/scout-report.md` GAP-005 (atlas-aci test_dry_run caveat).

**Trade-off accepted:** Verifier can still probe its own container (consume CPU, fill stdout, read other crystals' blobs). OS-level isolation (DevContainer, microVM) is the operator's responsibility. Operator must read the warning and decide.

**Reversibility:** Can tighten to DinD (docker-run per invocation) in v0.2 if operators complain. Would require host docker socket access, widening attack surface.

---

### D6. Importance function signature lock

**Decision:** `importance_score(record, *, now) -> float` with four inputs (access_frequency, recency, outcome_success, novelty); weights tuple externally tunable; return [0, 1].

**Rationale:** Same function powers write-gate criterion and forget-weight. Signature is frozen at v0.1; only `WEIGHTS` tuple is the swap point for D11 (adaptive learning, deferred). Pure function + module-level constant = deterministic, testable, replaceable.

**Source:** `.forge/reasoning-report.md` D6 (swap point design).

**Trade-off accepted:** Callers cannot pass custom weight functions in v0.1. Signature change breaks callers; future versions must provide a compatibility shim.

**Reversibility:** v0.2 can introduce `PluggableImportance` class while keeping backward-compat alias pointing to the default function.

---

### D7. Cross-cutting trust-tier propagation

**Decision:** Consolidated tier = MIN(inputs.tier). Admission checks `consolidated.tier ≤ layer.ceiling`. T3 input → Semantic admission denied. Error message: structured advice "exclude T3 inputs or commit to Episodic instead."

**Rationale:** Downgrading the recorded tier while admitting would re-launder T3 content through Semantic (defeating the chokepoint). Blocking preserves "one chokepoint" invariant. Practical mitigation: summarizer explicitly excludes T3 inputs if needed, making the choice knowingly.

**Source:** `.forge/reasoning-report.md` D7 (poison-laundering defense); MISSION.md §P0 (trust tier carries through consolidation).

**Trade-off accepted:** Summarizers cannot mix T3 + T1+ inputs in Semantic. They must either pre-filter or commit to Episodic (quarantined). Adds a decision point for the agent.

**Reversibility:** Cannot weaken (would be a security regression). Can add "T3-aware" Semantic layer (different ceiling) in v0.2 if use cases arise.

---

### D8. ≥k corroboration default + human-confirm UX

**Decision:** k=3; human-confirm default ON for first 30 days post-install. Promotion candidates land in `pending_promotions` table; CLI `crystalium promote review <id> [--accept|--reject]` for operator review.

**Rationale:** k=3 matches the human-confirm-on default (both signal "be cautious"). Inbox queue (not blocking prompt) is decoupled, batchable, audit-friendly. 30-day grace period balances security (cautious-by-default) with usability (new projects don't stay in review purgatory forever).

**Source:** `.forge/reasoning-report.md` D8 (UX design); MISSION.md §P0 (configurable k, human-confirm first month).

**Trade-off accepted:** New installs have slower Semantic promotion for 30 days. Operators must actively review. After 30d, default flips to OFF (configurable).

**Reversibility:** Operator can override `human_confirm: false` in `crystalium.yaml` immediately. Or increase `k_corroboration: 2` to lower the bar. Both are per-deployment tuning.

---

### D9. Working-set eviction rule

**Decision:** Highest-importance-first kept. Pop lowest tuple `(importance, last_access, record_id)` ascending. Deterministic; identical to `forget_weight = 1 - importance`.

**Rationale:** Determinism is required for test stability (test_working_set_budget_invariant must be reproducible). Importance is already the write-gate metric (D6), so reusing it is consistent. Tie-breaking by `last_access` then `record_id` gives stable ordering.

**Source:** `.forge/reasoning-report.md` D9 (eviction trade-offs); SPEC.md §6 (composer contract).

**Trade-off accepted:** MMR (max-margin relevance) diversification is deferred; v0.1 favours determinism over diversity. Some working sets may be less balanced than a learned eviction.

**Reversibility:** v0.2 can introduce optional MMR eviction flag (non-deterministic, test-wise, but higher quality). Default remains importance-first for stability.

---

### D10. Open questions (post-v0.1 review)

Nine open questions are surfaced in SPEC.md §17 (OQ-1 through OQ-9):

- **OQ-1:** Does `force_promote` (T0) audit through `pending_promotions` or straight-write?
- **OQ-2:** Does Dream's prune step call `assert_tier_allowed` as synthetic T0?
- **OQ-3:** For T2/T3 callers without identity headers, ship `to.eidolon = "unknown"` or refuse?
- **OQ-4:** Should `RECENCY_HALFLIFE_DAYS = 14` be operator-tunable?
- **OQ-5:** Should k=3 auto-relax to k=2 in single-operator workflows?
- **OQ-6:** Does Junction need a shutdown hook to call `session_end`?
- **OQ-7:** Path-traversal guard: `/sandbox/<skill_id>` only, or block symlink escapes?
- **OQ-8:** ECL `trace.tier`: caller's tier or crystalium's (T1) service tier?
- **OQ-9:** Should importance `novelty_at_write` be recomputed as neighbourhood shifts?

**Source:** `.forge/reasoning-report.md` D10 (open-questions audit).

These are explicitly **not** resolved in v0.1. They surface for human review post-launch and may drive v0.2 design.

---

## Research anchors

Cited in MISSION.md §Quality bars:

1. **Index → pointer → content (Hippocampal model)**
   - [UNVERIFIED]: Teyler & DiScenna 1986 "The role of the hippocampus in memory: a hypothesis"
   - [UNVERIFIED]: Teyler & Rudy 2007 "The hippocampal indexing theory and episodic memory"
   - **Application:** CRYSTALIUM uses cheap blob tier (content-addressed) + queryable indices (pointers). Scales to large episodic archives.

2. **Dual-speed + consolidation (Sleep-like offline processing)**
   - [UNVERIFIED]: McClelland, McNaughton, O'Reilly 1995 "Why there are complementary learning systems in the hippocampus and neocortex"
   - [UNVERIFIED]: Tononi & Cirelli 2014 "Sleep and the price of plasticity"
   - arXiv:2504.16891 "Sleep-time Compute for LLMs"
   - arXiv:2604.20943 "SCM (Sleep Consolidation Model)"
   - **Application:** Dream cycle runs asynchronously, off hot path, triggered by idle + event count. Async consolidation is cheaper than synchronous online learning.

3. **Slot-bounded working set (Baddeley working memory)**
   - [UNVERIFIED]: Baddeley & Hitch 1974 "Working memory"
   - **Application:** Composer enforces ≤3,500 tokens across six typed slots with hard caps. Deterministic eviction (importance-first) maintains reproducibility.

4. **Write-gating (Prefrontal-hippocampal control)**
   - [UNVERIFIED]: O'Reilly & Frank 2006 "Making working memory work: a computational model of learning in the prefrontal cortex and striatum"
   - **Application:** enforcement.py is the single chokepoint. Every write funnels through `assert_tier_allowed` before any store code.

5. **Bi-temporal edits (Append-only + pointers)**
   - [UNVERIFIED]: arXiv:2501.13956 "Zep/Graphiti: Bi-temporal memory for LLM agents"
   - **Application:** Updates invalidate-old (`t_valid_to = now`, `superseded_by = <new_id>`), write-new. Never hard-delete. Enables rollback and audit trails.

6. **Edit primitives (Structured memory updates)**
   - [UNVERIFIED]: arXiv:2504.19413 "Mem0: Structured memory for agents"
   - **Application:** `crystalium.update` accepts `patch` (field-level diff) and `reason`, mirroring Mem0's structured edit model.

7. **Verifier-gated skills (Procedural verification)**
   - arXiv:2305.16291 "Voyager: An Open-Ended Embodied Agent"
   - [UNVERIFIED]: arXiv:2602.01869 "ProcMEM: Procedural skill memory"
   - [UNVERIFIED]: arXiv:2605.10999 "SkillGen: Verified skill generation"
   - **Application:** Procedural layer requires `skill_invoke` verifier-pass before admission. Subprocess sandbox with timeout + output cap.

8. **Poisoning defense (Trust propagation)**
   - [UNVERIFIED]: arXiv:2604.16548 "LTM Security Survey: Attacks on long-term memory"
   - [UNVERIFIED]: OWASP ASI06 "Agent Skill Injection"
   - **Application:** Trust tier carries through consolidation (MIN rule). T3 input → Semantic admission denied. Blocks multi-agent poison laundering.

9. **Constrained interfaces (Bounded autonomy)**
   - arXiv:2405.15793 "SWE-agent: Bounded ACI for code agents"
   - [UNVERIFIED]: arXiv:2501.?????? "Agentless: Fixed tools, no autonomy"
   - **Application:** CRYSTALIUM has no inferred actions (recall only on demand, Dream only proposes). Constrained interfaces are more trustworthy than autonomous inference.

---

## Security surface

### Per-layer security table

**Episodic layer**

- **Context consumed:** Past missions, session logs, PR references, outcomes.
- **Persists where:** `~/.crystalium/<project>/episodic/` (SQLite indices + blob tier).
- **External calls:** None (fully local).
- **Failure mode:** Caller spoofs T0 trust tier; stores T3 content with false claim of verification.
- **Mitigation:** G1 chokepoint test + telemetry log of every tier assertion. Operator audits logs.

**Semantic layer**

- **Context consumed:** Curated facts (API signatures, conventions, design decisions).
- **Persists where:** `~/.crystalium/<project>/semantic/` (LanceDB + KuzuDB + SQLite).
- **External calls:** None (fully local).
- **Failure mode:** T3 input launders through summarizer into Semantic (defeats chokepoint).
- **Mitigation:** G4 (D7 MIN-trust propagation); admission denied with structured advice "exclude T3 inputs or commit to Episodic instead."

**Procedural layer**

- **Context consumed:** Verifier scripts + skill metadata.
- **Persists where:** `~/.crystalium/<project>/procedural/` (blob tier + SQLite).
- **External calls:** Subprocess executes verifier in container (`skill_invoke`).
- **Failure mode:** Malicious verifier consumes CPU, fills stdout, probes container filesystem, reads other crystals' secrets.
- **Mitigation:** G3 sandbox (cwd under `/sandbox/<skill_id>`, `Path.resolve(strict=True)`, 30s timeout, 8 KiB output cap, operator-warning log). OS isolation is operator's responsibility (DevContainer/microVM).

**Execution layer**

- **Context consumed:** In-flight plan state, replan diffs, task checkpoints.
- **Persists where:** `~/.crystalium/<project>/execution/` (SQLite, TTL).
- **External calls:** None (fully local).
- **Failure mode:** Plan persists past task end, consuming storage + revealing agent strategy.
- **Mitigation:** TTL enforced at recall + nightly Dream prune. Operator configures TTL (default: task lifetime).

### Cross-cutting controls (all P0)

- **Path-traversal guard:** `Path.resolve(strict=True)` then `relative_to(repo_or_sandbox_root)`. Raises `PathEscape` on escape attempt.
- **Per-process rate limit:** Sliding 60-second window, default 200 calls/min. Enforced in `assert_rate_limit` before any tool code.
- **Telemetry on every call:** `structlog` JSONL + OpenTelemetry. Redacted args (never log raw `content`). Operator connects sink to SIEM.
- **Redaction at recall:** Regex pre-pass + local LLM judge on sensitivity-tagged content. Re-applied at cross-agent handoff (ECL envelope).

### MCP server as trust boundary

For untrusted models, the operator MUST sandbox the crystalium container at OS level (DevContainer, Firecracker microVM, or equivalent). CRYSTALIUM enforces what it can mechanically (G1–G8). It cannot enforce OS isolation by itself.

---

## Provenance

This document was composed during the **IDG Normalize phase** for CRYSTALIUM v0.1.0 bootstrap. The upstream artefacts are:

1. **MISSION.md** — frozen P0 brief (immutable until v0.2.0), source of truth for identity, four layers, P0 invariants, container-first rule, stack choices, quality bars.
2. **.atlas/scout-report.md** — ATLAS v1.8.0 triage phase, 5 findings (enforcement pattern, ECL shape, EIIS conformance, Junction wiring, roster entry), 5 gaps (Streamable-HTTP, Dream idle-trigger, ECL opt-in, capability_class, sandbox OS isolation).
3. **.forge/reasoning-report.md** — FORGE v1.0 reasoning phase, 10 decisions D1–D10, 8 gate candidates G1–G8, 9 open questions (OQ-1 through OQ-9), full config defaults.
4. **.spectra/crystalium-v0.1.0-spec.md** — SPECTRA v1.0 alignment phase, 8 gates with test_anchors + failure_classes + severity, tier × layer matrix, tool surface contract, composer contract, file layout, build waves W1–W6, canary suite, quality scorecard.

Every decision is traceable to one of these sources via `path:line` citation or `[UNVERIFIED]` tag for external research.
