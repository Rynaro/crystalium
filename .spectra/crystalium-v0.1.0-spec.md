# CRYSTALIUM v0.1.0 — Specification (decision-ready)

**Spec version:** 1.0 (Alignment-phase deliverable)
**Target product version:** CRYSTALIUM v0.1.0
**Authors:** FORGE (reasoning) + SPECTRA (alignment)
**Phase:** S (SPECTRA Alignment) — downstream consumer is APIVR-Δ (wave-by-wave implementation)
**Date:** 2026-05-28

## Input artefacts

All three are quoted (not paraphrased) where load-bearing. Citations use `path:line` form.

- `MISSION.md` — frozen P0 brief (immutable until v0.2.0). Path: `/Users/henrique/workspace/oss/agents/crystalium/MISSION.md`.
- `.atlas/scout-report.md` — 5 findings + 5 gaps (GAP-004 pre-resolved). Path: `/Users/henrique/workspace/oss/agents/crystalium/.atlas/scout-report.md`.
- `.forge/reasoning-report.md` — 10 decisions D1–D10, 8 gate candidates G1–G8, 9 OQs, full config defaults. Path: `/Users/henrique/workspace/oss/agents/crystalium/.forge/reasoning-report.md`.

Reference-only (read; not modified):
- `atlas-aci/mcp-server/src/atlas_aci/enforcement.py` — chokepoint pattern crystalium mirrors.
- `eidolons-ecl/schemas/envelope.v2.json` — wire format, 11 required fields.
- `eidolons-eiis/spec/eiis-1.4.md` §1.8–§1.9 — install whitelist + cleanup sweep.

---

## §1. Identity & Non-Goals

CRYSTALIUM is the **portable memory harness for the Eidolons**: a self-hosted, vendor-agnostic, MCP-compatible memory substrate. It is infrastructure (`MISSION.md:17-19`), not an agent. It stores, gates, retrieves, consolidates, and forgets. It does not reason, plan, or write code. The core principle is constrained interfaces beat raw autonomy — every write/promote funnels through one mechanical chokepoint (SWE-agent ACI / Agentless pattern, restated `MISSION.md:32`).

**Vocabulary** (frozen): **Crystal** = an admitted record. **Aetheryte** = the recall/index network. **Dream** = the async consolidation worker.

**Non-goals for v0.1.0** (cite `MISSION.md:168-178`, restated §15 below): polyglot skill abstraction, learned/adaptive importance weights, belief-drift detection, quarantine review UI, server-profile (Postgres/Qdrant/Neo4j) + LangGraph, in-weights/LoRA consolidation, multi-agent CRDT/consensus, REM-style associative linkage.

**Also out of scope per FORGE D2:** Streamable-HTTP transport (stdio-only in v0.1; HTTP env-var stub raises `NotImplementedError("v0.2")`).

**Also out of scope for v0.1.0 (parent-resolved GAP-004):** roster publication. v0.1.0 ships as a working source repo with EIIS v1.4-conformant `install.sh`; the `Rynaro/crystalium` roster entry is deferred to v0.2+ once the canary suite is stable.

---

## §2. Architecture map

```
                    HOST (operator workstation)
                    only tools allowed: docker, git, make
                              |
                              v
        +-------------------------------------------------+
        |  docker compose run --rm crystalium ...         |
        |  (single container; Dockerfile + compose file)  |
        +-------------------------------------------------+
                              |
                  stdio JSON-RPC 2.0 (MCP 2025-03-26)
                              v
        +-------------------------------------------------+
        |  server.py  ( MCP server: @list_tools/@call_tool ) |
        |    |                                            |
        |    | every tools/call passes through:           |
        |    v                                            |
        |  +----------------------------------------+     |
        |  | enforcement.py  (CHOKEPOINT, P0)       |     |
        |  |   1. assert_tier_allowed(tool,layer,   |     |
        |  |      tier, op)                         |     |
        |  |   2. assert_no_path_escape(target)     |     |
        |  |   3. assert_rate_limit()               |     |
        |  |   4. record() telemetry                |     |
        |  +----------------------------------------+     |
        |    |              |              |              |
        |    v              v              v              |
        |  layers/      aetheryte/     dream/             |
        |  (Episodic,   (recall +      (orient →          |
        |   Semantic,    hybrid        gather →           |
        |   Procedural,  index)        consolidate →      |
        |   Execution)                 prune; runs        |
        |                              OUTSIDE MCP        |
        |                              request context)   |
        |    |              |              |              |
        |    +------+-------+              |              |
        |           v                      |              |
        |   +-------------------+          |              |
        |   | storage adapters  |<---------+              |
        |   |   SQLite + FTS5   |  index/metadata only    |
        |   |   LanceDB         |  (vector)               |
        |   |   KuzuDB          |  (graph)                |
        |   +-------------------+                         |
        |           |                                     |
        |           v                                     |
        |   +-------------------+                         |
        |   |  blob tier        |  content-addressed,     |
        |   |  ~/.crystalium/   |  immutable,             |
        |   |  <project>/       |  cheap                  |
        |   +-------------------+                         |
        +-------------------------------------------------+

  Allowed verbs on the arrows:
    server.py  →  enforcement.py    : assert_*, record
    enforcement.py → layers/*       : commit, propose_promote, force_promote, recall
    layers/*  → storage adapters    : write_index, write_pointer, fetch_pointer
    storage adapters → blob tier    : put(content) → returns sha256
    dream/    → enforcement.py      : synthetic T0 service identity (OQ-2),
                                      same gate path, no inlining
```

---

## §3. The 8 Validation Gates (G1–G8)

Each gate is GIVEN/WHEN/THEN plus three SPECTRA-added fields: `test_anchor`, `failure_class`, `tier_violated`. P0 = server is non-conformant on failure. P1 = correctness regression, but conformance survives.

### G1 — T3 cannot commit above Episodic
*From D1 / G1.*

- **GIVEN** a caller advertising `trust_tier = T3` (environment/tool-ingested),
- **WHEN** it invokes `crystalium.commit(layer ∈ {Semantic, Procedural, Execution}, payload, provenance)`,
- **THEN** `enforcement.assert_tier_allowed` raises `TierViolation` **before any store mutation runs**.
- **test_anchor:** `mcp-server/tests/test_enforcement.py::test_g1_t3_cannot_commit_above_episodic`
- **failure_class:** `TierViolation`
- **tier_violated:** T3
- **severity:** **P0**

### G2 — T2 procedural commits land as candidate
*From D1 / G2.*

- **GIVEN** a caller advertising `trust_tier = T2` (unverified),
- **WHEN** it invokes `crystalium.commit(layer=Procedural, payload, provenance)`,
- **THEN** the record is admitted with `validation_state = "candidate"`; promotion is rejected until a T0/T1 caller runs `crystalium.skill_invoke` and the verifier passes (G3 path).
- **test_anchor:** `mcp-server/tests/test_enforcement.py::test_g2_t2_procedural_candidate_only`
- **failure_class:** `PromotionGated` (on attempted promote without verifier)
- **tier_violated:** T2 (on attempted force-promote)
- **severity:** **P0**

### G3 — Procedural verifier-gated admission
*From D5 / G3.*

- **GIVEN** a procedural candidate `skill_id` exists in the candidate state,
- **WHEN** `crystalium.skill_invoke(skill_id, args)` is called,
- **THEN** a subprocess is spawned with `timeout_s ≤ 30`, `output_cap_bytes ≤ 8192`, cwd resolved (`Path.resolve(strict=True)`) under `/sandbox/<skill_id>`, and an operator-warning is emitted to telemetry. Verifier exit-code 0 + output-cap-not-exceeded promotes to `admitted`; anything else leaves the candidate untouched.
- **test_anchor:** `mcp-server/tests/test_skill_invoke.py::test_g3_skill_invoke_sandbox_contract`
- **failure_class:** `SkillVerifierFailed` (verifier non-zero) | `SkillTimeout` | `SkillOutputOverflow` | `SkillPathEscape`
- **tier_violated:** n/a (operation-level gate)
- **severity:** **P0**

### G4 — Trust-tier propagation blocks T3 laundering
*From D7 / G4.*

- **GIVEN** a summarizer reads inputs whose trust tiers are `{T1, T2, T3}`,
- **WHEN** it invokes `crystalium.commit(layer=Semantic, payload=<consolidated>)`,
- **THEN** `enforcement` computes `consolidated.tier = min(inputs.tier) = T3`; admission is rejected with `TierCeilingViolation` (Semantic ceiling is T2) and the structured advice `"exclude T3 inputs or commit to Episodic instead."` is returned.
- **test_anchor:** `mcp-server/tests/test_trust_propagation.py::test_g4_min_tier_blocks_semantic_laundering`
- **failure_class:** `TierCeilingViolation`
- **tier_violated:** T3 (effective)
- **severity:** **P0**

### G5 — Human-confirm default window
*From D8 / G5.*

- **GIVEN** `install_ts` is within the last 30 days AND the operator has not set `human_confirm: false` in `crystalium.yaml`,
- **WHEN** a promotion is proposed (any layer),
- **THEN** the record lands in the `pending_promotions` table; it does NOT enter the target layer until a `crystalium promote review <id> --accept` CLI call. `CRYSTALIUM_AUTO_CONFIRM=1` bypasses (test-only) and emits a WARN log per use.
- **test_anchor:** `mcp-server/tests/test_promotion_gate.py::test_g5_human_confirm_default_window`
- **failure_class:** `PromotionPending` (not an error; structured status)
- **tier_violated:** n/a
- **severity:** **P0**

### G6 — Working-set budget invariant
*From D9 / G6.*

- **GIVEN** the composer is asked to assemble a working set whose pre-eviction serialized tokens exceed any slot cap,
- **WHEN** `composer.compose(scope)` runs,
- **THEN** for every slot ∈ {executive, procedural, semantic, episodic, execution, buffer}, `slot.tokens ≤ slot.cap` AND total ≤ 3,500; eviction is deterministic — pop the lowest tuple `(importance, last_access, record_id)` (ascending) until under cap; tie-break by `last_access` then lexicographic `record_id`.
- **test_anchor:** `mcp-server/tests/test_composer.py::test_g6_working_set_budget_invariant`
- **failure_class:** `WorkingSetOverflow` (only raised if eviction loop fails to converge — should never trigger for finite inputs)
- **tier_violated:** n/a
- **severity:** **P0**

### G7 — ECL envelope conformance per tool result
*From D4 / G7.*

- **GIVEN** any `crystalium.*` MCP tool result that produces a payload artefact,
- **WHEN** the result is written to disk,
- **THEN** a sibling `ecl-envelope.<message_id>.json` file exists at the same directory, valid against `envelope.v2.json`, carrying all 11 required ECL v2.0 fields; `integrity.method = "sha256"`; `integrity.value == hashlib.sha256(payload_bytes).hexdigest()`; `artifact.sha256 == integrity.value`; `from.eidolon = "crystalium"`; `from.version` matches `__version__`.
- **test_anchor:** `mcp-server/tests/test_ecl_conformance.py::test_g7_every_tool_result_emits_valid_envelope`
- **failure_class:** `EnvelopeMissing` | `EnvelopeInvalid` | `EnvelopeIntegrityMismatch`
- **tier_violated:** n/a
- **severity:** **P0**

### G8 — Dream dedup on idle + event triggers
*From D3 / G8.*

- **GIVEN** no commits or recalls for `idle_threshold_s = 300` seconds AND `now - last_dream ≥ min_dream_gap_s = 1800`,
- **WHEN** the poll scheduler ticks at `dream_tick_s = 60`,
- **THEN** **exactly one** Dream run is enqueued; the `crystalium.session_end` tool, the idle-poll, and the event-count threshold all share a single enqueue path keyed by `dream_run_id` so concurrent triggers dedup to one execution.
- **test_anchor:** `mcp-server/tests/test_dream_scheduler.py::test_g8_dream_dedup_on_concurrent_triggers`
- **failure_class:** `DreamSchedulerInvariant` (raised in test if more than one run enqueues)
- **tier_violated:** n/a
- **severity:** **P1** (correctness-critical for forgetting behaviour; chokepoint stays conformant if Dream over-fires)

---

## §4. Tier × Layer × Operation Matrix

Restated from D1 verbatim. The four operations: `commit`, `propose_promote`, `force_promote`, `recall`.

| Layer       | Op                 | T0 (human) | T1 (verified) | T2 (unverified) | T3 (env/tool)      |
| ----------- | ------------------ | ---------- | ------------- | --------------- | ------------------ |
| Episodic    | commit             | allow      | allow         | allow           | allow_quarantine   |
| Episodic    | propose_promote    | allow      | allow         | deny            | deny               |
| Episodic    | force_promote      | allow      | deny          | deny            | deny               |
| Semantic    | commit             | allow      | allow         | deny            | deny               |
| Semantic    | propose_promote    | allow      | allow         | deny            | deny               |
| Semantic    | force_promote      | allow      | deny          | deny            | deny               |
| Procedural  | commit (candidate) | allow      | allow         | allow           | deny               |
| Procedural  | propose_promote    | allow      | allow         | deny            | deny               |
| Procedural  | force_promote      | allow      | deny          | deny            | deny               |
| Execution   | commit             | allow      | allow         | deny            | deny               |
| Execution   | propose_promote    | n/a        | n/a           | n/a             | n/a                |
| any         | recall             | allow      | allow         | allow           | allow              |

**Notes**

1. **`recall` is universally allowed.** Read-side trust is propagated via D7 (`consolidated.tier = min(inputs.tier)`), not gated at the recall call. Redaction (regex + small local LLM judge on sensitivity tags) runs between Aetheryte and the LLM-facing response, including at every cross-agent handoff (`MISSION.md:88-92`).
2. **`allow_quarantine` is an enforcement-side flag**, never a caller-supplied argument. Inside the chokepoint, `_MARK_QUARANTINE.set(True)` is consumed by the commit handler, which stamps `validation_state = "quarantined"` on the record. T3 callers cannot spoof `validation_state = "admitted"` because the parameter doesn't exist in the public tool surface.
3. **Procedural `commit (candidate)`** for T1/T2 lands in `validation_state = "candidate"`; promotion to `admitted` requires `crystalium.skill_invoke` verifier-pass (G3).
4. **`force_promote` is reserved to T0** and goes straight through (OQ-1 audit-vs-straight-through question is deferred); telemetry record is the audit trail.
5. **Execution layer has no `propose_promote`**: Execution is ephemeral, TTL-bound, expires at task end.

---

## §5. Tool Surface Contract

Seven tools. Five from `MISSION.md:130-138`, plus `crystalium.session_end` (FORGE D3, new). For each tool: typed signature, gates traversed, enforcement order, ECL envelope side-effect, failure classes.

### 5.1 `crystalium.recall`

```python
def recall(
    scope: Scope,                  # (project, agent_class_visibility, sensitivity_tag)
    query: str,                    # natural-language probe
    k: int = 10,                   # top-k after rerank
    layers: list[Layer] = [...],   # default: all four
) -> RecallResult
```

- **Gates traversed:** none of G1–G4 (recall is universally allowed; D1 row "any/recall").
- **Enforcement order:** `assert_rate_limit` → `assert_no_path_escape` (for any blob-tier resolves) → tool impl → redactor pass → `record(...)`.
- **ECL envelope:** YES — sidecar `ecl-envelope.<message_id>.json` with `performative = "INFORM"` and `artifact.kind = "recall-result"`. (G7.)
- **Failure classes:** `RateLimitExceeded`, `PathEscape`, `RedactionFailure`, `StoreUnavailable`.

### 5.2 `crystalium.commit`

```python
def commit(
    layer: Layer,                  # Episodic | Semantic | Procedural | Execution
    payload: CommitPayload,        # validates against commit-request.v1.json
    provenance: Provenance,        # tier, sources, scope, t_valid_from
) -> CommitResult
```

- **Gates traversed:** **G1** (T3 above Episodic → deny), **G2** (T2 Procedural → candidate), **G4** (consolidated.tier vs layer ceiling), **G5** (promotion if propose path), **G7** (envelope emit), implicitly **G3** (a commit to Procedural in admitted state requires G3 to have passed previously).
- **Enforcement order:** `assert_rate_limit` → `assert_tier_allowed(tool="crystalium.commit", layer, tier, op="commit")` → `assert_no_path_escape(blob_tier_target)` → bi-temporal write (invalidate-old per `MISSION.md:48-50`) → enqueue Dream hint → emit envelope sidecar → `record(...)`.
- **ECL envelope:** YES — `performative = "INFORM"` (commit of a new fact) or `"PROPOSE"` (if proposal enqueued in `pending_promotions`). `artifact.kind = "commit-result"`.
- **Failure classes:** `TierViolation`, `TierCeilingViolation`, `PromotionGated`, `PromotionPending`, `BiTemporalIntegrityFailure`, `PathEscape`, `RateLimitExceeded`.

### 5.3 `crystalium.update`

```python
def update(
    id: CrystalId,
    patch: UpdatePatch,            # field-level diff (Mem0 edit-primitives style)
    reason: str,
) -> UpdateResult
```

- **Gates traversed:** **G1**, **G4**, **G7**. Bi-temporal integrity is the dominant constraint (`MISSION.md:48-50`).
- **Enforcement order:** `assert_rate_limit` → `assert_tier_allowed(..., op="commit")` (treat as commit-of-new-revision) → fetch existing crystal → write old with `t_valid_to = now, superseded_by = <new_id>` → write new revision → emit envelope sidecar → `record(...)`. **Never hard-delete.**
- **ECL envelope:** YES — `performative = "INFORM"`, `artifact.kind = "commit-result"` (schema_version reflects the patched layer's schema).
- **Failure classes:** `TierViolation`, `BiTemporalIntegrityFailure` (e.g. attempted hard-delete), `CrystalNotFound`, `PathEscape`, `RateLimitExceeded`.

### 5.4 `crystalium.skill_invoke`

```python
def skill_invoke(
    skill_id: str,
    args: dict,
    *,
    timeout_s: int = 30,
    output_cap_bytes: int = 8192,
    workdir: Path,                 # MUST be under /sandbox/<skill_id>
) -> SkillResult
```

- **Gates traversed:** **G3** (sandbox contract is the gate). Implicitly G7 on result emission.
- **Enforcement order:** `assert_rate_limit` → `assert_no_path_escape(workdir, expected_prefix="/sandbox/<skill_id>")` with `Path.resolve(strict=True)` → log operator-warning (`SANDBOXING IS THE OPERATOR'S RESPONSIBILITY`, mirrored from atlas-aci) → `subprocess.run(..., timeout=timeout_s, ...)` → output cap check → emit envelope → `record(...)`.
- **ECL envelope:** YES — `performative = "INFORM"` on success; `"REFUSE"` on verifier failure with structured advice. `artifact.kind = "skill-result"`.
- **Failure classes:** `SkillVerifierFailed`, `SkillTimeout`, `SkillOutputOverflow`, `SkillPathEscape`, `RateLimitExceeded`.

### 5.5 `crystalium.plan_checkpoint`

```python
def plan_checkpoint(state: PlanState) -> CheckpointResult
```

- **Gates traversed:** **G1** (Execution layer commit; T3/T2 blocked per matrix), **G7**.
- **Enforcement order:** `assert_rate_limit` → `assert_tier_allowed(..., layer=Execution, op="commit")` → write to Execution layer (TTL-bound) → emit envelope → `record(...)`.
- **ECL envelope:** YES — `performative = "INFORM"`, `artifact.kind = "plan-checkpoint"`.
- **Failure classes:** `TierViolation`, `ExecutionTTLExpired`, `RateLimitExceeded`.

### 5.6 `crystalium.plan_replan`

```python
def plan_replan(diff: PlanDiff) -> ReplanResult
```

- **Gates traversed:** **G1**, **G7**. Bi-temporal write of replan history.
- **Enforcement order:** `assert_rate_limit` → `assert_tier_allowed(..., layer=Execution, op="commit")` → invalidate-old plan node → write new node with `superseded_by` → emit envelope → `record(...)`.
- **ECL envelope:** YES — `performative = "INFORM"`, `artifact.kind = "plan-replan"`.
- **Failure classes:** `TierViolation`, `ExecutionTTLExpired`, `PlanNotFound`, `RateLimitExceeded`.

### 5.7 `crystalium.session_end` *(new per FORGE D3)*

```python
def session_end(reason: str = "host_signaled") -> SessionEndResult
```

- **Gates traversed:** **G8** (dedup; this is one of the two enqueue paths). G7 on result.
- **Enforcement order:** `assert_rate_limit` → resolve `dream_run_id` from `(now, last_dream)` → enqueue Dream via shared scheduler entry point (the same path the 60s poll uses) → emit envelope → `record(...)`. **Never** runs Dream inline.
- **ECL envelope:** YES — `performative = "ACKNOWLEDGE"`, `artifact.kind = "session-end-receipt"`.
- **Failure classes:** `SchedulerUnavailable`, `RateLimitExceeded`.

### 5.8 Failure-class table (consolidated)

| Code | Severity | Raised by | Retry hint |
|---|---|---|---|
| `TierViolation` | hard | enforcement.assert_tier_allowed | none — caller must use lower-trust layer |
| `TierCeilingViolation` | hard | enforcement (D7 propagation) | exclude T3 inputs or commit to Episodic |
| `PromotionGated` | hard | enforcement (G2) | run skill_invoke first |
| `PromotionPending` | soft | promotion gate (G5) | await `crystalium promote review --accept` |
| `BiTemporalIntegrityFailure` | hard | layers/* | none — code bug |
| `SkillVerifierFailed` | soft | skill_invoke | fix verifier or amend skill |
| `SkillTimeout` | soft | skill_invoke | retry with smaller args |
| `SkillOutputOverflow` | soft | skill_invoke | reduce verifier verbosity |
| `SkillPathEscape` | hard | enforcement | none — caller bug |
| `PathEscape` | hard | enforcement.assert_no_path_escape | none |
| `RateLimitExceeded` | soft | enforcement.assert_rate_limit | wait `Retry-After` |
| `EnvelopeMissing` / `EnvelopeInvalid` / `EnvelopeIntegrityMismatch` | hard | envelope helper | none — server bug |
| `WorkingSetOverflow` | hard | composer | none — code bug |
| `DreamSchedulerInvariant` | hard (test-only) | dream scheduler | none |
| `RedactionFailure` | hard | redactor | none |
| `StoreUnavailable` | soft | storage adapters | retry with backoff |

---

## §6. Working-Set Composer Contract

**Slot allocations** (per `MISSION.md:67-74`, restated FORGE D9):

| Slot       | Cap (tokens) |
|------------|--------------|
| executive  | 300          |
| procedural | 600          |
| semantic   | 800          |
| episodic   | 800          |
| execution  | 1000         |
| buffer     | 300          |
| **total cap** | **3500** (hard) |

**Eviction rule (D9):** highest-importance-first kept. Pop the lowest tuple `(importance, last_access, record_id)` ascending — tie-break by `last_access` (oldest first), then lexicographic `record_id`. Deterministic and identical to the forget-weight metric (`forget_weight = 1 − importance_score`).

**Tokenizer:** prefer `tiktoken` if installed; fall back to the harness's existing tokenizer; final fallback is `len(text) // 4` (documented as `[UNVERIFIED]` approximation). Always count tokens against the **serialized form** the record will appear in inside the composed working set.

**Invariant assertion shape:**

```python
def test_g6_working_set_budget_invariant():
    composer = Composer(slots=DEFAULT_SLOTS)
    out = composer.compose(scope=fixture_scope, candidates=fixture_overload_set)
    for slot_name, cap in DEFAULT_SLOTS.items():
        assert out.slots[slot_name].tokens <= cap, (
            f"slot {slot_name}: {out.slots[slot_name].tokens} > {cap}"
        )
    assert sum(s.tokens for s in out.slots.values()) <= 3500
    # determinism: same inputs → same kept set, same order
    out2 = composer.compose(scope=fixture_scope, candidates=fixture_overload_set)
    assert [r.id for r in out.kept] == [r.id for r in out2.kept]
```

---

## §7. File Layout (frozen)

Restated from `MISSION.md` §6 (the bootstrap tree), with per-file SOURCE-OF-TRUTH attribution. Containerization files are first-class.

```
crystalium/
├── agent.md                                # crystalium agent profile (≤1000 tokens, EIIS §1.8.6)
├── SPEC.md                                 # this spec (frozen for v0.1)
├── MISSION.md                              # frozen bootstrap; NOT shipped to install target
├── DESIGN-RATIONALE.md                     # W6 deliverable; citations + [UNVERIFIED] markers
├── CHANGELOG.md                            # Keep-a-Changelog; v0.1.0 entry on W6
├── README.md                               # source-repo required by EIIS §1.1
├── AGENTS.md                               # source-repo required by EIIS §1.1
├── CLAUDE.md                               # source-repo required by EIIS §1.1
├── LICENSE                                 # Apache-2.0
├── EIIS_VERSION                            # contents: "1.4"  (EIIS §1.1)
├── ECL_VERSION                             # contents: "2.0"  (FORGE D4; triggers EIIS §3.7.1)
├── install.sh                              # EIIS v1.4 conformant; idempotent; bash 3.2 safe
├── Dockerfile                              # CONTAINER-FIRST. Python ≥3.11 base via uv.
├── docker-compose.yml                      # service: crystalium. mounts ~/.crystalium volume.
├── docker-compose.dev.yml                  # adds pytest + dev tools; never on host.
├── Makefile                                # only host-visible commands: test, lint, build
├── pyproject.toml                          # uv-managed; runs INSIDE container
├── schemas/
│   ├── crystal.v1.json                     # source-of-truth for crystal record
│   ├── skill.v1.json                       # procedural frontmatter
│   ├── recall-request.v1.json
│   ├── recall-result.v1.json
│   ├── commit-request.v1.json
│   ├── commit-result.v1.json
│   └── install.manifest.v1.json            # EIIS §1.8.1 (SHOULD)
├── mcp-server/
│   ├── pyproject.toml
│   └── src/crystalium/
│       ├── __init__.py                     # __version__ = "0.1.0"
│       ├── __main__.py                     # CLI entry: crystalium serve|promote|...
│       ├── server.py                       # MIRRORS atlas-aci/server.py wiring pattern
│       ├── enforcement.py                  # MIRRORS atlas-aci/enforcement.py chokepoint
│       ├── config.py                       # Pydantic; loads crystalium.yaml
│       ├── importance.py                   # FROZEN signature, FORGE D6
│       ├── composer.py                     # working-set composer (§6)
│       ├── ecl_envelope.py                 # envelope helper; hashlib.sha256
│       ├── layers/
│       │   ├── __init__.py
│       │   ├── episodic.py
│       │   ├── semantic.py
│       │   ├── procedural.py
│       │   └── execution.py
│       ├── aetheryte/
│       │   ├── __init__.py
│       │   ├── recall.py                   # hybrid: BM25 ⊕ vector ⊕ graph; rerank if k>20
│       │   └── redact.py                   # regex pre-pass + small-LLM judge
│       ├── dream/
│       │   ├── __init__.py
│       │   ├── scheduler.py                # apscheduler / arq; idle-poll + session_end
│       │   └── worker.py                   # orient → gather → consolidate → prune
│       ├── gate.py                         # promote-gate, pending_promotions table
│       ├── storage/
│       │   ├── sqlite.py                   # SQLite + FTS5 (relational + sparse)
│       │   ├── lance.py                    # LanceDB (vector)
│       │   ├── kuzu.py                     # KuzuDB (graph)
│       │   └── blob.py                     # filesystem, content-addressed
│       └── telemetry.py                    # structlog JSONL + OpenTelemetry
└── mcp-server/tests/
    ├── conftest.py
    ├── test_enforcement.py                 # G1, G2, G4 anchors
    ├── test_trust_propagation.py           # G4
    ├── test_skill_invoke.py                # G3
    ├── test_promotion_gate.py              # G5
    ├── test_composer.py                    # G6
    ├── test_ecl_conformance.py             # G7
    ├── test_dream_scheduler.py             # G8
    ├── test_schemas.py                     # JSON Schema validity + Pydantic round-trip
    ├── test_storage_sqlite.py
    ├── test_storage_lance.py
    ├── test_storage_kuzu.py
    └── test_storage_blob.py
```

**SOURCE-OF-TRUTH attribution**

| File | Mirrors |
|---|---|
| `mcp-server/src/crystalium/enforcement.py` | `atlas-aci/mcp-server/src/atlas_aci/enforcement.py` (FINDING-001) |
| `mcp-server/src/crystalium/server.py` | `atlas-aci/mcp-server/src/atlas_aci/server.py` (FINDING-001 wiring) |
| `mcp-server/src/crystalium/ecl_envelope.py` | `eidolons-ecl/schemas/envelope.v2.json` + `conformance/lib/integrity.sh` (FINDING-002) |
| `install.sh` | EIIS v1.4 Appendix A `cleanup_inventory_sweep` reference (FINDING-003) |
| `EIIS_VERSION` content `"1.4"` | EIIS §1.1 source-repo MUST |
| `ECL_VERSION` content `"2.0"` | FORGE D4; EIIS §3.7.1 install obligation |
| `schemas/install.manifest.v1.json` | EIIS §1.8 manifest schema |
| `mcp-server/src/crystalium/dream/scheduler.py` | NOT Junction's sync dispatch loop — `apscheduler`/`arq` outside MCP request context (FINDING-004 caveat) |
| `mcp-server/src/crystalium/importance.py` | FORGE D6 — signature FROZEN; only `WEIGHTS` tuple is the swap point for D11 (out of scope) |
| `Dockerfile` + `docker-compose*.yml` | Container-first rule (`MISSION.md:114-117`); the host runs nothing but `docker`, `git`, `make` |

---

## §8. Build Waves W1–W6

Sequential. No parallel waves. Each wave's `container_test` command runs INSIDE `docker compose run --rm crystalium ...`. The host's only tools are `docker`, `git`, `make`.

### W1 — Schemas + Pydantic + storage adapters

- **Scope:** All six JSON schemas + Pydantic mirrors + four storage adapters (SQLite/FTS5, LanceDB, KuzuDB, blob tier). No enforcement, no MCP, no Dream yet.
- **Files touched:**
  - `schemas/crystal.v1.json`, `schemas/skill.v1.json`
  - `schemas/recall-request.v1.json`, `schemas/recall-result.v1.json`
  - `schemas/commit-request.v1.json`, `schemas/commit-result.v1.json`
  - `schemas/install.manifest.v1.json`
  - `mcp-server/src/crystalium/__init__.py` (`__version__ = "0.1.0"`)
  - `mcp-server/src/crystalium/config.py` (Pydantic models)
  - `mcp-server/src/crystalium/storage/{sqlite,lance,kuzu,blob}.py`
  - `mcp-server/tests/test_schemas.py`, `test_storage_*.py`
  - `Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`, `pyproject.toml`, `Makefile`
- **Gates MUST pass:** none. W1 is the foundation; gate tests come in W2+.
- **Gates MAY defer:** all (G1–G8).
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_schemas.py mcp-server/tests/test_storage_sqlite.py mcp-server/tests/test_storage_lance.py mcp-server/tests/test_storage_kuzu.py mcp-server/tests/test_storage_blob.py -v`
- **commit_subject:** `feat(schemas,storage): land crystal/skill schemas + Pydantic + SQLite/LanceDB/Kuzu adapters`

### W2 — Enforcement chokepoint

- **Scope:** `enforcement.py` (assert_tier_allowed, assert_no_path_escape, assert_rate_limit, record), tier matrix, importance, trust propagation, telemetry, redactor.
- **Files touched:**
  - `mcp-server/src/crystalium/enforcement.py`
  - `mcp-server/src/crystalium/importance.py` (FROZEN signature per D6)
  - `mcp-server/src/crystalium/aetheryte/redact.py`
  - `mcp-server/src/crystalium/telemetry.py`
  - `mcp-server/tests/test_enforcement.py`, `test_trust_propagation.py`
- **Gates MUST pass before commit:** **G1**, **G2** (admission gate path), **G4** (D7 propagation).
- **Gates MAY defer:** G3, G5–G8 (depend on W3/W4/W5).
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_enforcement.py mcp-server/tests/test_trust_propagation.py -v`
- **commit_subject:** `feat(enforcement): land tier-matrix chokepoint + trust propagation + importance + redactor`

### W3 — Layers + gate + Aetheryte + Dream

- **Scope:** four layer modules; promotion gate + `pending_promotions` table; Aetheryte hybrid recall; Dream scheduler + worker (apscheduler).
- **Files touched:**
  - `mcp-server/src/crystalium/layers/{episodic,semantic,procedural,execution}.py`
  - `mcp-server/src/crystalium/gate.py`
  - `mcp-server/src/crystalium/aetheryte/recall.py`
  - `mcp-server/src/crystalium/dream/{scheduler,worker}.py`
  - `mcp-server/src/crystalium/composer.py`
  - `mcp-server/tests/test_promotion_gate.py`, `test_composer.py`, `test_dream_scheduler.py`
- **Gates MUST pass before commit:** **G3** (skill_invoke contract — verifier-pass wiring lands here even if `skill_invoke` itself wires fully in W4), **G5**, **G6**, **G8**.
- **Gates MAY defer:** G7 (envelope helper lands in W4).
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_promotion_gate.py mcp-server/tests/test_composer.py mcp-server/tests/test_dream_scheduler.py mcp-server/tests/test_skill_invoke.py -v`
- **commit_subject:** `feat(layers,dream): land four layers + promote-gate + Aetheryte recall + Dream scheduler/worker + composer`

### W4 — MCP wiring + CLI + ECL envelope

- **Scope:** `server.py` (atlas-aci-shaped `@server.list_tools()` / `@server.call_tool()` decorators); `__main__.py` CLI (`crystalium serve`, `crystalium promote list/review`); `ecl_envelope.py` helper.
- **Files touched:**
  - `mcp-server/src/crystalium/server.py`
  - `mcp-server/src/crystalium/__main__.py`
  - `mcp-server/src/crystalium/ecl_envelope.py`
  - `mcp-server/tests/test_ecl_conformance.py`
- **Gates MUST pass before commit:** **G7** (every tool result emits valid envelope); regression sweep keeps G1–G6, G8 green.
- **Gates MAY defer:** none.
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/test_ecl_conformance.py mcp-server/tests/ -v`
- **commit_subject:** `feat(server,cli,ecl): wire MCP stdio server + CLI + ECL v2.0 envelope sidecar emission`

### W5 — Full test suite + canary suite

- **Scope:** complete cross-cutting test pass; canary missions (§13); memory-on/off A/B harness.
- **Files touched:**
  - `mcp-server/tests/canary/` (new dir): `test_canary_*.py`, fixtures, A/B runner
  - `mcp-server/tests/test_e2e.py` (cross-cutting end-to-end)
- **Gates MUST pass before commit:** **G1–G8 all green** + canary pass rate ≥ 0.80 (memory-on beats memory-off on at least 80% of canaries — §13 headline metric).
- **Gates MAY defer:** none.
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/ mcp-server/tests/canary/ -v`
- **commit_subject:** `test(suite,canary): land full test suite + 10-mission canary + memory-on/off A/B harness`

### W6 — install.sh + Docker hardening + CI + DESIGN-RATIONALE

- **Scope:** EIIS v1.4 conformant `install.sh`; harden `Dockerfile` + compose files; CI workflows (`.github/workflows/` — schema CI, test CI, EIIS conformance CI); `DESIGN-RATIONALE.md` (citations + `[UNVERIFIED]` markers); `CHANGELOG.md` v0.1.0 entry.
- **Files touched:**
  - `install.sh`, `EIIS_VERSION`, `ECL_VERSION`
  - `Dockerfile` (final hardening — non-root user, read-only mounts where possible)
  - `.github/workflows/{schema,test,eiis-conformance}.yml`
  - `DESIGN-RATIONALE.md`, `CHANGELOG.md`, `README.md`
- **Gates MUST pass before commit:** **G1–G8 all green** + EIIS v1.4 conformance check passes (install-target whitelist + `agent.md ≤ 1000 tokens` + `install.sh` idempotent — CI second-run-no-diff job).
- **Gates MAY defer:** none.
- **container_test:** `docker compose run --rm crystalium pytest mcp-server/tests/ -v && docker compose run --rm crystalium bash install.sh --target /tmp/install1 && docker compose run --rm crystalium bash install.sh --target /tmp/install1 && docker compose run --rm crystalium diff -r /tmp/install1 /tmp/install1.snapshot`
- **commit_subject:** `feat(install,ci,docs): EIIS v1.4 install.sh + CI workflows + DESIGN-RATIONALE + CHANGELOG`

---

## §10. Container-First Build Protocol

**Hard rule.** Every wave's test command runs INSIDE `docker compose run --rm crystalium ...`. The host's allowed tools are exactly: `docker`, `git`, `make`. APIVR-Δ MUST NOT invoke host `python`, `pip`, `uv`, `pytest`, or `node` directly.

**Operational notes**

1. The `Makefile` is the only host-side test wrapper. Every target must shell out via `docker compose run --rm` — never `python -m pytest`. Example: `make test` ≡ `docker compose run --rm crystalium pytest mcp-server/tests/ -v`.
2. APIVR-Δ's Bash policy includes `pytest:*` — honour it by running pytest **through** Docker. The policy applies to the executing process, which is `docker compose`, not `pytest` directly.
3. `pyproject.toml` and dependency installs (`uv sync`) execute on `docker build`, never on the host. Cached layers handle dependency churn between waves.
4. The bind-mounted workspace is read-write for source edits; runtime artefacts (SQLite DBs, LanceDB indices, blob tier) live in a named volume (`crystalium_data`) mounted at `~/.crystalium/` inside the container.
5. CI mirrors the same pattern — `docker compose run --rm crystalium pytest` runs in GitHub Actions; no `pip install` step in the workflow file.

**Spec failure rule.** If any wave's `container_test` field in `crystalium-v0.1.0-spec.yaml` omits `docker compose run --rm`, the spec has failed validation. APIVR-Δ should reject such a spec and request a SPECTRA re-run.

---

## §11. EIIS v1.4 Conformance Plan

Per FINDING-003 (`eidolons-eiis/spec/eiis-1.4.md` §1.9). v0.1.0 does not publish to roster (GAP-004 pre-resolved by parent), but `install.sh` MUST be EIIS v1.4 conformant — the source repo will publish post-v0.1 without retrofit.

**Source-repo obligations** (§1.1):
- `agent.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `install.sh`, `EIIS_VERSION = "1.4"`.
- `ECL_VERSION = "2.0"` per FORGE D4 (triggers §3.7.1 verbatim-copy obligation).

**Install-target whitelist** (§1.9.1, FINDING-003) — `./.eidolons/crystalium/` MUST contain only:
- `agent.md` (MUST, role `agent-profile`)
- `SPEC.md` (MUST, role `spec`)
- `install.manifest.json` (MUST, role `manifest`)
- `ECL_VERSION` (MUST since source declares it — §3.7.1)
- `skills/<skill>.md` (MAY)
- `templates/<artifact>.md` (MAY)
- `schemas/install.manifest.v1.json` (SHOULD)
- `schemas/<aux>.json` (MAY)

**Explicitly forbidden** in the install target (§1.9.3):
- `CRYSTALIUM.md` (legacy slug-named spec)
- `AGENTS.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `DESIGN-RATIONALE.md`, `MISSION.md` (source-repo files)
- Root-level `SKILL.md` or `skills/<phase>/SKILL.md` (legacy layout)
- Directories: `hosts/`, `evals/`, `research/`, `tools/`, `commands/`, `mcp-server/`, `schemas/` (other than the EIIS-permitted manifest + aux schema files)

**v1.4 new obligations** (MUST-fail at conformance check):
- §1.8.6: `agent.md` recorded in `files_written[]` with `role: "agent-profile"` exactly once.
- §3.7.1: `ECL_VERSION` recorded in `files_written[]` with `role: "ecl-version"` and verbatim-equal SHA-256.
- §4.2.3: claude-code dispatch file MUST reference both `agent.md` AND `SPEC.md`.
- §6.X: `cleanup_inventory_sweep` after install removes any non-whitelisted files. Use the Appendix A reference implementation (bash 3.2 compatible — `install.sh` is bash 3.2 safe per Eidolons convention).
- §6.Y: every `skills/<skill>.md` reference in `agent.md` MUST resolve to a `files_written[]` entry.

---

## §12. ECL v2.0 Conformance Plan

Per FORGE D4 + FINDING-002. CRYSTALIUM declares `ECL_VERSION = "2.0"` in the source repo (verbatim copied to install target by §3.7.1) and emits an envelope sidecar for every MCP tool result.

**Required envelope fields** (`envelope.v2.json` top-level `required` — all 11):

1. `envelope_version` — `"2.0"`
2. `message_id` — UUIDv7 RECOMMENDED (`uuid6` Python lib, or hand-rolled — `[UNVERIFIED]` whether `uuid6` is the canonical choice; document in DESIGN-RATIONALE)
3. `thread_id` — UUID grouping all envelopes of one MCP session
4. `parent_id` — UUID or null (null only on first envelope of a thread)
5. `from` — `{eidolon: "crystalium", version: __version__}`
6. `to` — `{eidolon: <caller-identity-header>|"unknown", version: <semver>|"n/a"}` (per FORGE D4: "unknown" for T2/T3 callers without identity headers; OQ-3 reviews whether to refuse instead)
7. `performative` — one of the closed 10: `INFORM`, `PROPOSE`, `REFUSE`, `ACKNOWLEDGE`, `REQUEST`, `CRITIQUE`, `DECIDE`, `DELEGATE`, `ESCALATE`, `RESUME` (mapping per tool surface §5)
8. `objective` — 1–240 chars; one-sentence description of the tool call
9. `artifact` — `{kind, schema_version, path, sha256, size_bytes}` (all five sub-required)
10. `integrity` — `{method: "sha256", value: <64-char hex>}`. `value == hashlib.sha256(payload_bytes).hexdigest()`
11. `trace` — `{ts: RFC3339, host: "crystalium-mcp", model: "n/a", tier: "standard"}` (per OQ-8: ship caller's tier when known; default "standard")

**`edge_origin`:** `"implicit"` for v0.1.0 (no contracts/ entry yet — FINDING-002 guidance for pre-roster Eidolons).

**Integrity helper** (`ecl_envelope.py`):

```python
import hashlib, json, uuid
from pathlib import Path
from datetime import datetime, timezone

def emit_envelope(
    payload_path: Path,
    *,
    performative: str,
    artifact_kind: str,
    artifact_schema_version: str,
    to_eidolon: str = "unknown",
    to_version: str = "n/a",
    thread_id: str | None = None,
    parent_id: str | None = None,
    objective: str,
) -> Path:
    payload = payload_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    envelope = {
        "envelope_version": "2.0",
        "message_id": str(uuid.uuid4()),    # uuid7 preferred when stdlib gains support
        "thread_id": thread_id or str(uuid.uuid4()),
        "parent_id": parent_id,
        "from": {"eidolon": "crystalium", "version": __version__},
        "to": {"eidolon": to_eidolon, "version": to_version},
        "performative": performative,
        "edge_origin": "implicit",
        "objective": objective,
        "artifact": {
            "kind": artifact_kind,
            "schema_version": artifact_schema_version,
            "path": payload_path.name,
            "sha256": digest,
            "size_bytes": len(payload),
        },
        "integrity": {"method": "sha256", "value": digest},
        "trace": {
            "ts": datetime.now(timezone.utc).isoformat(),
            "host": "crystalium-mcp",
            "model": "n/a",
            "tier": "standard",
        },
    }
    out = payload_path.with_name(f"ecl-envelope.{envelope['message_id']}.json")
    out.write_text(json.dumps(envelope, sort_keys=True, indent=2))
    return out
```

**Test (G7):** validate every emitted envelope against `envelope.v2.json` via `jsonschema`; assert `integrity.value == artifact.sha256 == sha256(payload_bytes)`.

---

## §13. Canary Suite

Ten missions. Each has `scenario`, `oracle`, `pass criterion`. The headline A/B metric is **memory-on beats memory-off on ≥ 80% of canaries**. Failure on the headline is reported plainly (per `MISSION.md:163-164`).

| ID | Name | Scenario | Oracle | Pass criterion |
|---|---|---|---|---|
| CAN-1 | recall_hit_across_sessions | Commit a fact in session A; recall in fresh session B with the same scope. | Memory-off returns nothing relevant; memory-on returns the committed crystal in top-3. | Memory-on `mrr@3 > 0` AND memory-off `mrr@3 == 0` |
| CAN-2 | promote_gate_T3_blocked | T3 caller attempts `commit(layer=Semantic)`. | `TierViolation` raised; no store write. | G1 test_anchor passes + no row inserted in semantic store |
| CAN-3 | promote_gate_T2_procedural_candidate | T2 caller commits Procedural; later T1 caller runs `skill_invoke`; verifier passes. | Candidate → admitted. | G2 + G3 test_anchors pass end-to-end |
| CAN-4 | poisoning_resistance_T3_summarization | Summarizer ingests {T1, T2, T3} and tries to commit to Semantic. | Admission rejected with `TierCeilingViolation`; advice surfaced. | G4 test_anchor passes; structured advice present in error payload |
| CAN-5 | selective_forget_bi_temporal | Update a fact: invalidate-old + write-new with `superseded_by`. | Old record present with `t_valid_to`; new record visible; no hard-delete. | Bi-temporal integrity assertion in `test_layers_semantic.py` |
| CAN-6 | multi_agent_isolation | Two agent_class_visibility scopes commit overlapping facts; recall from scope A does not return scope B's crystal. | Scope isolation honoured. | Recall result for scope A contains zero scope-B crystals |
| CAN-7 | procedural_verifier_pass | Submit a Procedural skill with a failing verifier; admission denied. Resubmit with passing verifier; admission granted. | G3 path exercised. | Both branches' test_anchors pass |
| CAN-8 | working_set_budget_invariant | Compose with overload set 3× cap. | Composer enforces per-slot + 3500 total caps; eviction deterministic. | G6 test_anchor passes |
| CAN-9 | ecl_envelope_conformance | Recall + commit + update all emit valid envelopes. | All three envelopes validate against `envelope.v2.json`; SHA-256 matches. | G7 test_anchor passes; jsonschema validation green |
| CAN-10 | dream_dedup | Trigger session_end + idle-poll + event-count concurrently. | Exactly one Dream run enqueued. | G8 test_anchor passes |

**Headline A/B metric.** For each of CAN-1, CAN-3, CAN-5, CAN-6 (the four with a memory-on vs memory-off question), run both arms. Pass rate = (arms where memory-on strictly beats memory-off) / (arms tested). Target ≥ 0.80. If below, report as `[UNVERIFIED — memory does not measurably help on this canary set]` per `MISSION.md:163`.

---

## §14. Quality Bars

Restated from `MISSION.md:144-166` with measurable test targets.

| Bar | Measurable target | Test anchor |
|---|---|---|
| `agent.md` ≤ ~1,000 tokens | `wc -l agent.md` ≤ 60 lines (proxy) AND `tiktoken cl100k_base` count ≤ 1000 | `mcp-server/tests/test_meta.py::test_agent_md_token_cap` |
| Composer ≤ 3,500 tokens | G6 invariant | `test_composer.py::test_g6_working_set_budget_invariant` |
| Every §2.2 P0 invariant covered | G1–G8 all passing | `pytest mcp-server/tests/` exit 0 |
| `install.sh` idempotent | Second run produces identical install target | CI "second-run-no-diff" job (W6 container_test) |
| DESIGN-RATIONALE.md cites every non-obvious decision | Citation count ≥ 10 (per anchor list in `MISSION.md:149-162`) | `mcp-server/tests/test_meta.py::test_design_rationale_citations` |
| `[UNVERIFIED]` markers on unverifiable claims | grep for `[UNVERIFIED]` returns ≥ 1 (uuid7 lib + tiktoken fallback at minimum) | manual review at W6 commit |
| Memory-on/off A/B headline | ≥ 0.80 pass rate on §13 canaries | `mcp-server/tests/canary/test_ab_headline.py` |
| CHANGELOG.md Keep-a-Changelog under v0.1.0 | Section header `## [0.1.0] - 2026-XX-XX` present | manual W6 review |
| Branch naming | `feat/crystalium-v0.1.0`; no push, no PR | git branch check at W6 |

---

## §15. Out-of-Scope Hooks

Per `MISSION.md:168-178`. For each deferred feature, the spec leaves a named door open.

| Deferred feature | Hook (schema field / config knob / module) |
|---|---|
| Polyglot skill abstraction | `schemas/skill.v1.json` fields `language` (enum) + `capability_class` |
| Learned/adaptive importance weights | `importance.py::WEIGHTS` tuple — module-level constant; D11 mutates only this |
| Belief-drift detection | `provenance` field on every crystal + `telemetry.py` audit log sink |
| Quarantine review UI | `validation_state: quarantined` field on Episodic records; CLI `crystalium promote review` already enumerates |
| Server profile (Postgres/Qdrant/Neo4j) | `config.profile: "local" \| "server"` Pydantic field (raises `NotImplementedError("v0.2")` on `"server"`) |
| LangGraph adapter | n/a — explicitly excluded by `MISSION.md:111`; no hook |
| In-weights / LoRA consolidation | n/a — foreclosed by D9 (highest-importance-first eviction is the only consolidation primitive in v0.1) |
| Multi-agent CRDT/consensus | `superseded_by` field on every crystal + append-only blob tier (D5 caveat); LWW is the v0.1 conflict rule |
| REM-style associative linkage | n/a — no hook |
| Streamable-HTTP transport | `CRYSTALIUM_TRANSPORT={stdio,http}` env var; HTTP branch raises `NotImplementedError("v0.2")` (FORGE D2) |

---

## §16. Security Surface

Per `MISSION.md:181-189`. Per-layer table — context consumed, persistence, external calls, failure modes, mitigation.

| Layer | Context consumed | Persists where | External calls | Failure mode | Mitigation |
|---|---|---|---|---|---|
| Episodic | Past mission/session/PR pointers from caller | `~/.crystalium/<project>/episodic/` (SQLite indices + blob tier) | none (fully local) | Caller spoofs T0 to commit T3 content above quarantine | G1 chokepoint test + telemetry log of every tier assertion |
| Semantic | Curated facts; conventions; signatures | `~/.crystalium/<project>/semantic/` (LanceDB + KuzuDB + SQLite) | none | T3 launders content via summarizer | G4 (D7 MIN-trust propagation); admission denied with structured advice |
| Procedural | Verifier scripts + skill frontmatter | `~/.crystalium/<project>/procedural/` (blob + SQLite) | subprocess executes verifier in container | Malicious verifier consumes CPU / fills stdout / probes container | G3 sandbox: `/sandbox/<skill_id>` cwd, `Path.resolve(strict=True)`, 30s timeout, 8 KiB output cap, operator-warning log; OS isolation is operator's responsibility (mirrored from atlas-aci) |
| Execution | In-flight plan state, replan diffs | `~/.crystalium/<project>/execution/` (SQLite, TTL) | none | Plan persists past task end | TTL enforced at recall + nightly Dream prune |

**MCP server as trust boundary.** For untrusted hosting models, the operator MUST run the crystalium container under a DevContainer or microVM. CRYSTALIUM enforces what it can mechanically (G1–G8). It cannot enforce OS isolation by itself — DESIGN-RATIONALE.md §Security restates this verbatim.

**Cross-cutting controls** (all P0):
- Path-traversal guard: `Path.resolve(strict=True)` then `relative_to(repo_or_sandbox_root)` (atlas-aci pattern).
- Per-process rate limit: sliding 60-second window deque; default 200 calls/min (FORGE config).
- Telemetry on every call: `structlog` JSONL + OpenTelemetry; redacted args (never log raw `content`).
- Redaction: regex pre-pass + local LLM judge on sensitivity-tagged content; re-applied at every cross-agent handoff.

---

## §17. Open Questions Appendix (OQ-1 … OQ-9)

Copied verbatim from FORGE D10. **Not for re-decision in this spec — surfaced for human review post-v0.1.**

- **OQ-1.** Should `force_promote` (T0 only) still require an entry in `pending_promotions` for audit, or write straight through? (Currently: straight-through; audit lives in telemetry record.)
- **OQ-2.** Dream's prune step — does it call `assert_tier_allowed` against itself (running as a synthetic T0)? Recommend yes; needs explicit T0 service identity.
- **OQ-3.** Envelope `to.eidolon` for T2/T3 callers without identity headers: ship as `"unknown"` (current D4) or refuse the call?
- **OQ-4.** `RECENCY_HALFLIFE_DAYS = 14` — appropriate for short-running projects but may overweight stale data in long-lived workspaces. Operator-tunable?
- **OQ-5.** k=3 with three independent T1+ witnesses may be hard to accumulate organically in single-operator workflows. Should k auto-relax to 2 if only one Eidolon is installed?
- **OQ-6.** `crystalium.session_end` tool exists per D3 — but Junction is sync-only (FINDING-004); does Junction need a wrapper to call it on its own shutdown path?
- **OQ-7.** Path-traversal guard radius: `/sandbox/<skill_id>` only, or also block symlink escapes (`Path.resolve()` follows them per FINDING-001)? Recommend `resolve(strict=True)` + reject if outside.
- **OQ-8.** ECL `trace.tier` field — does it reflect *caller's* tier or *crystalium's* (always T1 service)? Suggest caller's.
- **OQ-9.** Importance `novelty_at_write` is frozen at write time; should Dream recompute it as the neighbourhood shifts? Current design says no; flag for D11.

**SPECTRA-surfaced new OQ (during alignment):**

- **OQ-10.** `objective` field on ECL envelopes for tool calls — should crystalium auto-generate from `(tool_name, layer, scope.project)`, or require callers to pass via an MCP extension field? Current spec auto-generates (no extension field defined); revisit if multi-Eidolon hand-offs need richer objective text.

---

## §18. Spec Scorecard

APIVR-Δ scores 1–5 on each (5 = fully satisfied; 1 = fundamentally absent).

| # | Criterion | How to score |
|---|---|---|
| 1 | Mechanical invariants present | All 8 gates have `test_anchor` pointing to a concrete file + test name (§3). |
| 2 | Wave plan executable | Each W1–W6 has clean inputs, clean outputs, container_test command (§8). |
| 3 | Container-first compliance | Every test command starts with `docker compose run --rm crystalium`. Grep §8 + spec.yaml `waves.*.container_test`; one host-shell-out command = score 1. |
| 4 | EIIS v1.4 + ECL v2.0 conformance encoded | §11 lists whitelist + sweep; §12 lists 11 envelope fields + helper. |
| 5 | Memory-on/off A/B canary ready | §13 has ≥ 4 canaries with explicit A/B oracle + headline metric. |
| 6 | Tier matrix unambiguous | §4 has 12 rows × 4 columns; no `?` or `tbd`. |
| 7 | Trust propagation rule unambiguous | §3 G4 + §4 note 1 + D7 quote: `consolidated.tier = min(inputs.tier)`. |
| 8 | Importance function frozen at signature level | §15 hook + §10 file layout pins `importance.py` per D6 (signature in spec.md §15 + spec.yaml `config_defaults`). |

**Pass bar:** score ≥ 4 on every criterion. Any criterion at ≤ 3 triggers a SPECTRA re-run before W1 starts.

---

**End of spec.** Next consumer: APIVR-Δ for wave-by-wave implementation per §8. Branch: `feat/crystalium-v0.1.0`. No push, no PR until W6 conformance gate green.
