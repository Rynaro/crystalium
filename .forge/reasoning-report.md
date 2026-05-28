# CRYSTALIUM v0.1.0 — FORGE Reasoning Report

**Phase:** Reasoning (Frame → Observe → Reason → Gate → Emit)
**Inputs:** ATLAS scout report + embedded MISSION P0 set
**Out-of-scope (per parent):** GAP-004 capability_class (resolved); roster publication (v0.2+)

---

## D1. Enforcement tier-mapping logic

**Frame.** Define `assert_tier_allowed(tool_name, layer, trust_tier, operation)` so that one call rejects every (tier, layer, operation) combination not on the matrix below before any store mutation runs.

**Observe.**
- FINDING-001: enforcement is a pre-check chain; raising any AssertionError short-circuits the handler.
- P0: T3 → Episodic-quarantine only; T0 may force-promote; T1 may commit + propose-promote; T2 may commit Episodic + Procedural-candidate.
- P0: Procedural admission requires verifier-pass (separate gate, see D5).
- P0: Semantic promotion requires ≥k corroboration OR human-confirm (D8).

**Reason.** Two viable shapes: (i) per-tool allowlist frozenset like atlas-aci's read-only set, or (ii) a (layer × operation) matrix keyed by tier. Tools cross-cut layers (e.g. `crystalium.commit` accepts a `layer` arg), so (ii) is the right granularity. Operations collapse cleanly to four verbs: `commit`, `propose_promote`, `force_promote`, `recall`. Recall is universally allowed (read-side trust enforced via D7 propagation, not gating). Quarantine state is an implicit attribute of commits made by T3, not a separate operation.

**Gate.** Matrix must (a) deny every T3 write outside Episodic, (b) deny T2 commits to Semantic/Execution, (c) reserve `force_promote` to T0. Cross-checked below — passes.

**Emit.**

| Layer       | Op                 | T0 (human) | T1 (verified) | T2 (unverified) | T3 (env/tool) |
| ----------- | ------------------ | ---------- | ------------- | --------------- | ------------- |
| Episodic    | commit             | allow      | allow         | allow           | allow (quarantine) |
| Episodic    | propose_promote    | allow      | allow         | deny            | deny          |
| Episodic    | force_promote      | allow      | deny          | deny            | deny          |
| Semantic    | commit             | allow      | allow         | deny            | deny          |
| Semantic    | propose_promote    | allow      | allow         | deny            | deny          |
| Semantic    | force_promote      | allow      | deny          | deny            | deny          |
| Procedural  | commit (candidate) | allow      | allow         | allow           | deny          |
| Procedural  | propose_promote    | allow      | allow         | deny            | deny          |
| Procedural  | force_promote      | allow      | deny          | deny            | deny          |
| Execution   | commit             | allow      | allow         | deny            | deny          |
| Execution   | propose_promote    | n/a        | n/a           | n/a             | n/a           |
| any         | recall             | allow      | allow         | allow           | allow         |

```python
def assert_tier_allowed(tool: str, layer: Layer, tier: Tier, op: Op) -> None:
    if op == "recall": return
    rule = _MATRIX[(layer, op)].get(tier, "deny")
    if rule == "deny":
        raise TierViolation(tool, layer, tier, op)
    if rule == "allow_quarantine":
        _MARK_QUARANTINE.set(True)   # consumed by commit handler
```

Quarantine is an enforcement-side flag, not a layer-side argument — keeps T3 callers unable to spoof status.

---

## D2. Streamable-HTTP scope for v0.1

**Frame.** Ship stdio-only, or stdio + Streamable-HTTP?

**Observe.** FINDING-004: Junction is stdio JSON-RPC. GAP-001: `[UNVERIFIED]` mcp SDK exposes `streamable_http_server`, derived from SDK docs but not confirmed against pinned SDK version. Hosts in scope: Claude Code, Cursor, Junction — all stdio.

**Reason.** HTTP doubles auth/CORS/bind-address surface area without unlocking a covered host. v0.1's risk is correctness of chokepoint + tier matrix; adding a transport multiplies test combinations (each tool × each transport × each tier). Feature-flag stub keeps the door open without paying integration cost.

**Gate.** P0 doesn't mandate HTTP. Junction wiring uses stdio. Skip risk: zero.

**Emit.** **stdio-only in v0.1.** Wire HTTP behind `CRYSTALIUM_TRANSPORT=stdio|http` env var, default `stdio`; HTTP branch raises `NotImplementedError("v0.2")`. Document in SPEC §Transports as deferred.

---

## D3. Dream idle-trigger

**Frame.** When does Dream fire?

**Observe.** P0: Dream async, off hot path. FINDING-004: dream MUST run outside MCP request context. GAP-002: no MCP session_end notification; two options (poll, explicit tool).

**Reason.** (a) poll alone: works without host cooperation, but uses cycles + has a latency floor. (b) explicit tool alone: instant when honoured, never fires if host disconnects abruptly. (c) both: poll as floor, explicit as fast-path. Resource cost of an idle-check tick is trivial (DB query of `last_commit_at`); failure cost of not running Dream is unbounded crystal staleness. Belt-and-suspenders is cheap.

**Gate.** Must not run inside MCP request context — both branches enqueue to scheduler, never inline. Pass.

**Emit.** **Both.** Defaults:
- `crystalium.session_end` MCP tool: host calls, enqueues Dream immediately.
- Poll-scheduler tick: every `60s`, fires if `now - last_activity ≥ IDLE_THRESHOLD` (default `300s`) AND `now - last_dream ≥ MIN_DREAM_GAP` (default `1800s`).
- Both routes share one enqueue path; scheduler dedups by `dream_run_id`.

---

## D4. ECL v0.1 opt-in scope

**Frame.** Declare `ECL_VERSION=2.0` in v0.1, or defer to v0.2?

**Observe.** FINDING-002: ECL v2.0, 11 required fields, SHA-256 via stdlib. FINDING-003 / EIIS §3.7.1: declaring ECL_VERSION in source triggers verbatim copy obligation. Memory `[ECL repo]`: ECL is opt-in. Crystalium is a lateral service — every MCP tool result is potentially an inter-Eidolon hand-off.

**Reason.** Deferring keeps v0.1 install lean but creates a v0.2 retrofit that touches every tool result builder. Declaring now: install.sh copies ECL_VERSION (one-line operation per FINDING-003); each tool result wraps payload in envelope + sidecar (one helper, ~30 LOC using hashlib). Crystalium's *whole value* is multi-Eidolon trust propagation (D7) — envelopes ARE the trust-bearer. Shipping without them means trust_tier rides as ad-hoc JSON, which is exactly what ECL exists to standardize.

**Gate.** SHA-256 is stdlib (no new dep). Envelope construction is mechanical. P0 telemetry already exists for sidecar writing.

**Emit.** **Declare ECL_VERSION=2.0 in v0.1 source.** Every `crystalium.*` MCP tool result emits ECL envelope sidecar. `from.eidolon="crystalium"`, `to` inferred from caller-identity header (T1+) or `"unknown"` (T2/T3, conservative). Use uuidv7 for `message_id`. Sidecar path: `<run_dir>/ecl-envelope.<message_id>.json`.

---

## D5. skill_invoke sandbox v0.1 contract

**Frame.** Signature, defaults, isolation model for procedural verifier execution.

**Observe.** GAP-005: atlas-aci's test_dry_run is subprocess + 30s timeout + 8 KiB cap + operator warning. MISSION P0: container-first; host runs only docker compose + git. P0: Procedural admission requires verifier-pass in sandbox.

**Reason.** "Container-first" is the host-deployment rule (operator side), not necessarily the per-invocation rule. Two implementations:
- **subprocess inside crystalium container** — already containerized at the harness level; verifier inherits the crystalium image's environment (Python + git only). Fast, simple. Risk: verifier shares process namespace with the harness, can read crystalium's filesystem.
- **`docker run` per invocation** — strongest isolation, but requires DinD or host docker socket mount, which violates "host runs only docker compose + git" *and* widens attack surface (socket = root-equivalent).

Subprocess-inside-container is the consistent choice: the crystalium container IS the sandbox boundary. Mount procedural blobs read-only into a dedicated workdir; restrict cwd; cap output; cap time. Operator warning remains because a malicious procedural artefact can still consume CPU / fill stdout / probe its own container.

**Gate.** No socket exposure. No host process spawn. Matches atlas-aci precedent. Pass.

**Emit.**

```python
def skill_invoke(
    skill_id: str,
    args: dict,
    *,
    timeout_s: int = 30,
    output_cap_bytes: int = 8192,
    workdir: Path,           # caller-supplied, MUST be under /sandbox/<skill_id>
) -> SkillResult:
    """
    Run a procedural-layer verifier in-process-isolated subprocess.
    WARNING: this is a soft sandbox; the crystalium container is the trust
    boundary. Do not invoke skills from untrusted (T2/T3) sources without
    additional review. Procedural commits from T2 stay in 'candidate' state
    until a T0/T1 caller runs skill_invoke + admission gate.
    """
```

Defaults: `timeout_s=30`, `output_cap_bytes=8192`, workdir under `/sandbox/<skill_id>` (path-guard via FINDING-001 pattern). Output overflow flag mirrored from atlas-aci.

---

## D6. Importance function signature lock

**Frame.** Stable shape that D11 (adaptive learning, out-of-scope) can swap weights into without breaking callers.

**Observe.** P0: same function for write-gate and forget-weight. Inputs: access_frequency, recency, outcome_success, novelty.

**Reason.** Pure-function + module-level weight tuple is the swap point. Returning bounded `[0, 1]` requires normalization per input. Recency wants exponential decay; novelty wants 1 - cosine-similarity to nearest neighbour (computed at write-time, cached). Outcome_success in `{0, 1}` or `None` for unscored. Access_frequency log-normalized to dampen heavy-hit dominance.

**Gate.** Single signature, single return type, weights externally addressable. Pass.

**Emit.**

```python
# importance.py — D11 swap point; do NOT change signature.
WEIGHTS: tuple[float, float, float, float] = (0.25, 0.30, 0.25, 0.20)
# (access_frequency, recency, outcome_success, novelty)
RECENCY_HALFLIFE_DAYS: float = 14.0

def importance_score(record: MemoryRecord, *, now: datetime) -> float:
    af = math.log1p(record.access_count) / math.log1p(100)        # cap at ~100 hits
    rc = 0.5 ** ((now - record.last_access).days / RECENCY_HALFLIFE_DAYS)
    os_ = record.outcome_success if record.outcome_success is not None else 0.5
    nv = record.novelty_at_write
    w_af, w_rc, w_os, w_nv = WEIGHTS
    raw = w_af*af + w_rc*rc + w_os*os_ + w_nv*nv
    return max(0.0, min(1.0, raw))
```

Defaults sum to 1.0; D11 can mutate `WEIGHTS` only.

---

## D7. Cross-cutting trust-tier propagation

**Frame.** Summarizer reads {T1, T2, T3} → emits one Semantic candidate. Inherited tier = MIN = T3. Semantic ceiling is T2. Block or downgrade?

**Observe.** P0: trust tier carries through cross-agent reads + summarization (MIN, not reset to T1). P0: T3 may ONLY Episodic-quarantine. Semantic ceiling is implicit in D1 matrix (T3 cannot commit Semantic).

**Reason.** Downgrading the *crystal's* recorded provenance tier while admitting it into Semantic would silently re-launder T3 content into a layer T3 cannot otherwise write. That defeats the chokepoint. Blocking is consistent: if any input is tier-N, output is tier-N, and tier-N must satisfy the destination layer's admission rule. Practical mitigation: the summarizer can explicitly *exclude* T3 inputs before summarizing (an upstream choice it makes knowingly) — then the MIN is T2 and Semantic admission succeeds.

**Gate.** Blocking preserves "one chokepoint" invariant. Downgrading would create a covert promotion path. Pass.

**Emit.** **Block.** Rule: `consolidated.tier = min(inputs.tier)`; admission checks `consolidated.tier ≤ layer.ceiling`. T3 input → Semantic admission denied at the commit gate. Error class: `TierCeilingViolation`. Callers receive structured advice: "exclude T3 inputs or commit to Episodic instead."

---

## D8. ≥k corroboration default + human-confirm UX

**Frame.** k value, confirmation surface, "first month" interpretation.

**Observe.** P0: ≥k OR human-confirm; human-confirm DEFAULT ON for first month; k configurable.

**Reason.** k=2 is the smallest meaningful corroboration (one independent witness). k=3 is the standard "rule of three." For v0.1 with verification-favouring posture, **k=3** matches the human-confirm-on default — both signal "be cautious." Human-confirm UX: blocking CLI prompt risks deadlock when the operator isn't at the terminal (e.g. agentic loops). Inbox queue with `crystalium promote review` CLI subcommand is decoupled, batchable, and audit-friendly. Auto-confirm env var (`CRYSTALIUM_AUTO_CONFIRM=1`) for tests only — log every auto-confirm at WARN. "First month" = 30 calendar days from `install_ts` file written by install.sh; after expiry, default flips to OFF but operator may keep it on via config.

**Gate.** Default-ON for 30d guarantees the cautious-by-default property. Inbox doesn't block hot path. Pass.

**Emit.**
- `k_corroboration: int = 3` (configurable in `crystalium.yaml`).
- `human_confirm: bool` — default computed: `(now - install_ts) < 30d` → True else config-default.
- UX: candidates land in `pending_promotions` table; `crystalium promote list / review <id> [--accept|--reject]` CLI. Inbox surfaced in Dream summary.
- Test bypass: `CRYSTALIUM_AUTO_CONFIRM=1` (logged WARN every use).

---

## D9. Working-set eviction rule

**Frame.** Slot cap exceeded — which records survive?

**Observe.** P0: bounded slotted working set ≤3,500 tokens. Slots fixed.

**Reason.** Three candidates:
- **Highest-importance-first kept** — deterministic, aligns with D6, matches "forget-weight = 1 - importance."
- **Most-recent-first** — biases toward thrash; loses durable knowledge.
- **MMR diversified** — better quality, but non-deterministic across embedding versions; test assertion becomes brittle.

Determinism trumps quality for v0.1 (test_working_set_budget_invariant must be stable). Importance is already the chokepoint metric.

**Gate.** Deterministic. Same function as forget-weight. Pass.

**Emit.** **Highest-importance-first kept.** Tie-break: most-recent `last_access`. Final tie-break: lexicographic `record_id`. Tokens counted by tiktoken (or harness's existing tokenizer) per record's serialized form. Eviction loop: while slot.tokens > slot.cap, pop lowest tuple `(importance, last_access, record_id)`.

---

## D10. Open-question audit (post-v0.1 review)

- **OQ-1.** Should `force_promote` (T0 only) still require an entry in `pending_promotions` for audit, or write straight through? (Currently: straight-through; audit lives in telemetry record.)
- **OQ-2.** Dream's prune step — does it call `assert_tier_allowed` against itself (running as a synthetic T0)? Recommend yes; needs explicit T0 service identity.
- **OQ-3.** Envelope `to.eidolon` for T2/T3 callers without identity headers: ship as `"unknown"` (current D4) or refuse the call?
- **OQ-4.** RECENCY_HALFLIFE_DAYS = 14 — appropriate for short-running projects but may overweight stale data in long-lived workspaces. Operator-tunable?
- **OQ-5.** k=3 with three independent T1+ witnesses may be hard to accumulate organically in single-operator workflows. Should k auto-relax to 2 if only one Eidolon is installed?
- **OQ-6.** `crystalium.session_end` tool exists per D3 — but Junction is sync-only (FINDING-004); does Junction need a wrapper to call it on its own shutdown path?
- **OQ-7.** Path-traversal guard radius: `/sandbox/<skill_id>` only, or also block symlink escapes (`Path.resolve()` follows them per FINDING-001)? Recommend `resolve(strict=True)` + reject if outside.
- **OQ-8.** ECL `trace.tier` field — does it reflect *caller's* tier or *crystalium's* (always T1 service)? Suggest caller's.
- **OQ-9.** Importance `novelty_at_write` is frozen at write time; should Dream recompute it as the neighbourhood shifts? Current design says no; flag for D11.

---

## HANDOFF → SPECTRA

**Spec gates (GIVEN/WHEN/THEN candidates):**
- **G1 (from D1).** GIVEN a T3 caller, WHEN it invokes `commit(layer=Semantic|Procedural|Execution)`, THEN enforcement raises `TierViolation` before any store write.
- **G2 (from D1).** GIVEN a T2 caller, WHEN it invokes `commit(layer=Procedural)`, THEN record is admitted in `candidate` state; promotion requires T1+ verifier-pass.
- **G3 (from D5).** GIVEN a procedural candidate, WHEN `skill_invoke` is called, THEN subprocess runs with `timeout_s≤30`, `output_cap_bytes≤8192`, cwd under `/sandbox/<skill_id>`, and operator-warning is logged.
- **G4 (from D7).** GIVEN inputs `{T1, T2, T3}`, WHEN summarizer commits to Semantic, THEN admission is denied with `TierCeilingViolation` (MIN=T3, ceiling=T2).
- **G5 (from D8).** GIVEN install_ts < 30d ago AND no `human_confirm: false` override, WHEN promotion proposed, THEN record lands in `pending_promotions` not the target layer.
- **G6 (from D9).** GIVEN a slot's serialized tokens exceed cap, WHEN composer runs, THEN eviction is deterministic by `(importance↑, last_access↑, record_id↑)` and total tokens ≤ slot.cap.
- **G7 (from D4).** GIVEN any `crystalium.*` tool result, WHEN written to disk, THEN a sibling `ecl-envelope.<message_id>.json` exists with all 11 required ECL v2.0 fields and `integrity.value` matches `sha256(payload)`.
- **G8 (from D3).** GIVEN no commits/recalls for `IDLE_THRESHOLD` seconds AND `now - last_dream ≥ MIN_DREAM_GAP`, WHEN scheduler ticks, THEN exactly one Dream run is enqueued (dedup by `dream_run_id`).

**Config defaults (not gates — `crystalium.yaml`):**
- `transport: stdio` (D2)
- `idle_threshold_s: 300`, `min_dream_gap_s: 1800`, `dream_tick_s: 60` (D3)
- `ecl_version: "2.0"` (D4)
- `skill_invoke.timeout_s: 30`, `skill_invoke.output_cap_bytes: 8192` (D5)
- `importance.weights: [0.25, 0.30, 0.25, 0.20]`, `importance.recency_halflife_days: 14.0` (D6)
- `k_corroboration: 3`, `human_confirm_default_window_days: 30` (D8)
- Slots: `exec=300, procedural=600, semantic=800, episodic=800, execution=1000, buffer=300` (P0, restated for SPECTRA)

**Open questions surfaced (OQ-1 … OQ-9 above):** carry to SPECTRA spec appendix as `oqs:` block; do not block APIVR-Δ.
