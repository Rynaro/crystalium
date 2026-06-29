# Spec — Harness-agnostic `provenance.source` coercion in `commit`

| | |
|---|---|
| **Change ID** | `harness-agnostic-provenance-source-coercion` |
| **ESL tier** | `full` (right_size: files=3, rubric=7/12, tradeoff=true → route 0→1→2→3→4) |
| **Status at author time** | `proposed` → hand off at `in_progress` |
| **Maker / Checker** | `vivi` (implement) / `vigil` (drift-check, distinct identity — C4) |
| **Intent type** | `BUG_SPEC` (recurring real failure → fix spec) |
| **Author** | SPECTRA (planning-only; produces no code) |
| **Implementer** | APIVR-Δ / **Vivi** |
| **Spec date** | 2026-06-29 |
| **Conventions** | `.spectra/setup/spectra-conventions.md` loaded — vocabulary applied (MCP tool handler, Layer adapter, Trust tiers, Enforcement chokepoint, container-first P0-13). |

---

## 1. CLARIFY (intake)

**WHO** — The LLM driving any MCP harness (Claude Code, Cursor, Continue, a bare client) that calls `crystalium.commit`. The recurring real victim is a T1 roster Eidolon (SPECTRA) that supplies a *descriptive* provenance string.

**WHAT** — Make `crystalium.commit` (and, where the same raw-string→`Literal` pattern recurs, its siblings) stop hard-failing with a pydantic `literal_error` when the LLM supplies a descriptive / near-miss `provenance.source` (e.g. `source:"spectra-planning-session"`). The tool must accept the call, store a trust-class-correct enum value, and never force a manual retry.

**WHY** — The user's stated goal: *"ensure the MCP usage gets seamless in every situation on harness, and any harness."* Today an LLM that writes a human-meaningful source label gets a raw `ValidationError`, must re-read the schema, and retry with `verified_agent`. That retry loop is the bug. It is harness-independent (the model, not the harness, picks the string).

**CONSTRAINTS**
- C-1 Pure **server-side coercion**. Do **not** widen the frozen JSON schemas (`schemas/crystal.v1.json`, `schemas/commit-request.v1.json`) — they are v0.1-frozen and verbatim-copied by `install.sh:328-343` into `install.manifest.json` (a widen would break `install.sh` second-run-no-diff). Schema-widen is an **escalation**, never a default (RISK-1).
- C-2 Coercion must **never** emit `"human"` for a non-T0 caller (would set permanent forgetting-protection, `protection.py:54-58` — a W4 breach). Use `source_for_tier(caller_tier)` exactly (RISK-2).
- C-3 Already-valid `source` values must pass through **byte-for-byte (IDENTITY)** — witness-independence keying on `(author_agent, source)` (`gate.py:341-351`) must not shift for existing data (RISK-3, FINDING-009).
- C-4 **Container-first (P0-13).** No host `python`/`pip`/`uv`/`pytest`. All verification via `make` targets inside the `crystalium` compose service.
- C-5 CRYSTALIUM is **infrastructure, not an agent** — no reasoning, no inference. A deterministic, total mapping only.

**CLARIFY skip-justification** — Goal, victim, constraints and evidence are all explicit (ATLAS scout + user goal). No blocking ambiguity; the single genuine open policy (silent vs observable) is resolved below as a labeled, flippable **DECISION**, not a blocking question.

---

## 2. SCOPE

**Complexity: 7 / 12** (extended-thinking tier)

| Dimension | Score | Note |
|---|---|---|
| Scope breadth | 1 | One handler (`_handle_commit`) + its tests |
| Technical depth | 2 | pydantic `Literal` coercion + `datetime` tolerance + tier mapping reuse |
| Integration complexity | 2 | Touches downstream **trust / protection / promotion-gate** read-semantics; must preserve invariants |
| Uncertainty / Risk | 2 | never-human-non-T0, witness-independence narrowing, schema-freeze, absent-default behavior shift |

**In scope**
1. `provenance.source` coercion in `_handle_commit` (the bug).
2. `provenance.created_at` parse tolerance in `_handle_commit` (GAP-1 — mirror the ingest path).
3. The observable-advisory channel on the success result (the DECISION).
4. Positive + identity regression tests.

**Out of scope / Deferred**
- **GAP-2** `layer` near-miss normalization (`"episodic-memory"`, `"memory"` → `episodic`): a **separate, clearly-marked OPTIONAL / stretch story** (Story S5) so the core fix ships independently. Today it raises a *structured* `UNKNOWN_LAYER` error (not a raw `ValidationError`), so it is lower severity.
- `_handle_ingest` source construction (`server.py:1062-1067`): already enum-valid — the adapter recomputes `source` via `source_for_tier`. **No change.**
- `__main__.py:168-173` `source="human"`: not LLM-controlled (host CLI). **No change.**
- `update`: builds **no** `Provenance` (provenance is in `_UPDATE_PROTECTED_FIELDS`, `server.py:1100-1102`). **No change.**
- Widening any `schemas/*.v1.json` or the ECL envelope — **explicit non-goal** (escalation only).

**Assumptions (risk-if-wrong)**
- A-1 The four `Literal` members are the canonical trust classes and won't change in v0.1 (low risk — frozen schema).
- A-2 The default test `caller_tier` is T1/T2; the conftest fixture supplies an explicit valid `source` (verified at `conftest.py:129-130` → `"verified_agent"`), so the absent-default behavior shift does not break fixtures.
- A-3 The commit **result** dict shape is not schema-frozen (only the 6 crystal schemas are) — adding a *conditional* result field is not schema drift.

**Stakeholders** — User (goal owner); APIVR-Δ/Vivi (implementer); Vigil (drift-checker); every Eidolon that calls `commit` (SPECTRA is the observed victim).

---

## 3. Ground truth (ATLAS scout, re-verified against HEAD)

Every anchor below was independently re-opened during this spec pass.

| Ref | File:line | Fact |
|---|---|---|
| GT-1 | `schemas.py:23-31` | `Provenance` (pydantic v2, `ConfigDict(extra="forbid")` @ `:26`). `source: Literal["human","verified_agent","unverified_agent","environment"]` @ `:28`. `author_agent: Optional[str]` @ `:29`. `created_at: datetime` @ `:31`. Only `source`+`created_at` are strict/required. |
| GT-2 | `server.py:959-964` | **THE failure site.** `Provenance(source=raw_prov.get("source","unverified_agent"), …)` @ `:960` — raw LLM string flows straight into the `Literal` → `literal_error`. |
| GT-3 | `server.py:956-958` | **GAP-1.** `created_at_raw=raw_prov.get("created_at", now_iso)`; `if isinstance(str): _dt.datetime.fromisoformat(...)` with **no** try/except → `ValueError` on epoch ints / `Z`-suffixed / non-ISO. |
| GT-4 | `server.py:1055-1061` | The **ingest** path IS tolerant: `fromisoformat(created_raw.replace("Z","+00:00"))` wrapped in `try/except ValueError → now()`. The pattern to mirror into commit. |
| GT-5 | `ingest_adapter.py:125-127` + `:33-38` | The reusable primitive **already exists**: `source_for_tier(tier: Tier) -> str` over `_TIER_TO_SOURCE` — T0→`human`, T1→`verified_agent`, T2/T3→`unverified_agent`. For the observed T1 caller it yields `"verified_agent"` — exactly the LLM's successful manual retry. |
| GT-6 | `server.py:50` | `from crystalium.ingest_adapter import _HOST_EIDOLONS, _ROSTER_EIDOLONS` — already imports from this module; adding `source_for_tier` is a one-token extension of an existing line (no new import). `Provenance` imported @ `:55`. |
| GT-7 | `server.py:947` | `_handle_commit(..., caller_tier: Tier, ...)` — `caller_tier` is already a `Tier` in scope at the failure site. No plumbing needed. |
| GT-8 | `protection.py:54-58` | `provenance_source == "human"` → **permanently protected** from decay/eviction. The reason coercion must never emit `human` for non-T0 (C-2). |
| GT-9 | `gate.py:341-351` | Witness independence = distinct `(author_agent, source)` tuples. IDENTITY passthrough keeps this stable for valid values (C-3). |
| GT-10 | `ecl.py` / `server.py:708,787-790` | ECL v2.0 sidecar sha256 is over the tool **result bytes**, not provenance. `provenance.source` does **not** enter the envelope. A server-side coercion touches no schema/manifest/hash. |
| GT-11 | `server.py:809-816` | The `advice` key is on the **error** path only (exception handler). |
| GT-12 | `server.py:701-709, 797` | The **success** path returns the `result` dict (`layers/episodic.py:257-264` → `{status,id,layer,validation_state,importance,content_ref}`) JSON-dumped at `:708`/`:797`. The observable advisory for a *successful* (now non-erroring) commit must ride as a field on **this result dict**, not the `:809-816` error channel. |
| GT-13 | `install.sh:328-343` | Frozen `schemas/*.json` are verbatim-copied into the install target; `install.manifest.json` embeds them. Widening a schema breaks second-run-no-diff. |

---

## 4. The coercion contract (precise, total, deterministic)

Define one pure helper (name suggestion `coerce_provenance_source`) — total over any JSON-decoded value:

```
VALID = {"human", "verified_agent", "unverified_agent", "environment"}

coerce_source(raw, caller_tier) -> (final_source: str, coerced: bool):
    (a) IDENTITY   — if isinstance(raw, str) and raw in VALID:
                        return (raw, False)            # byte-for-byte passthrough
    (b) FALLBACK   — else (None | "" | whitespace | any other string | non-str):
                        return (source_for_tier(caller_tier), True)
    (c) INVARIANT  — source_for_tier yields "human" ONLY for Tier.T0.
                     Routing every (b) case through source_for_tier(caller_tier)
                     guarantees C-2: never "human" unless caller_tier == T0.
```

**Per-tier resolution of the FALLBACK branch** (from `_TIER_TO_SOURCE`):

| caller_tier | FALLBACK result | Note |
|---|---|---|
| T0 (host) | `human` | allowed for T0 only; consistent with ingest adapter |
| **T1 (roster Eidolon — the observed SPECTRA caller)** | **`verified_agent`** | exactly the LLM's successful manual-retry value |
| T2 | `unverified_agent` | unchanged from today's hardcoded default |
| T3 | `unverified_agent` | unchanged from today's hardcoded default |

**Behavior-shift call-out (honesty for G-EXISTING-TESTS-GREEN):** this contract changes the *absent-source* default too. Today `_handle_commit` hardcodes `"unverified_agent"` when `source` is absent; the new contract resolves the absent case via `source_for_tier(caller_tier)`. This differs only for **T0** (→`human`) and **T1** (→`verified_agent`); T2/T3 are unchanged. Re-verified: no existing test pins the absent-default — `conftest.py:129-130` supplies an explicit `"verified_agent"` (IDENTITY); every `"unverified_agent"` assertion (`test_adapter_mapping.py:72,79`, `test_enforcement.py:204`, `test_quarantine_cli.py:26`) is either the ingest-adapter path or an explicit valid source. Implementer MUST still run the full suite (G-EXISTING-TESTS-GREEN).

**`author_agent` is NOT lost.** The original descriptive string already belongs in `author_agent` (`schemas.py:29`), which the caller supplies. `source` is the trust-class enum **only**. The coercion touches `source` and leaves `author_agent` verbatim — the human-meaningful label is preserved on the crystal.

---

## 5. DECISION — silent vs observable coercion `[DECISION-1]`

**Question:** when coercion fires, should it be silent, or attach a non-fatal advisory to the result?

**Recommended default: SEAMLESS BUT OBSERVABLE.** Coerce without erroring (seamless — meets the user's goal) **and** attach a non-fatal advisory to the **success result dict** (observable — preserves auditability and lets a curious caller learn the canonical value without a retry).

**Mechanism (precise, per GT-11/GT-12):** the `:809-816` `advice` key is error-only. For a now-*successful* commit, attach the advisory as a **conditional field on the result dict** returned by `_handle_commit`, e.g.:

```jsonc
// result, ONLY when coercion actually fired:
{ "status": "committed", "id": "...", "layer": "episodic", "...": "...",
  "provenance_coercion": { "field": "source", "from": "spectra-planning-session", "to": "verified_agent" } }
```

A `created_at` fallback (Story S2) adds an analogous `{ "field": "created_at", "from": <raw>, "to": "server_now" }` entry (use a `provenance_coercions` list, or two sibling keys — implementer's call).

**Why conditional matters:** when `source` is already valid (IDENTITY) **and** `created_at` parses, **no field is added** — the result dict is byte-identical to today. This keeps the ECL sidecar sha256 (GT-10) and any result-shape assertions (`test_ecl_envelope.py`, `test_server.py`) unchanged for the no-coercion path.

**Justification:** the user's word is "seamless," so erroring is off the table. "Silent" would hide a trust-class reclassification (`spectra-planning-session` → `verified_agent`) that a debugging human may want to see. An advisory is free (no retry, no error), rides the result the caller already receives, and is naturally covered by the integrity hash. Observability ≫ silence at zero seam cost.

**Flip instructions (human override):** to make coercion **silent**, delete the advisory-attachment block in `_handle_commit` (do not attach `provenance_coercion`). The coercion math, gates G-SOURCE-* and G-CREATEDAT-TOLERANT are unaffected; only G-ADVISORY-OBSERVABLE is dropped. No other code changes.

---

## 6. EXPLORE — hypotheses + selection

7-dimension weighted rubric (Alignment .25, Correctness .20, Maintainability .15, Performance .15, Simplicity .10, Risk .10, Innovation .05); scores /5.

| # | Hypothesis | Align | Corr | Maint | Perf | Simp | Risk | Inno | **Weighted** |
|---|---|---|---|---|---|---|---|---|---|
| **H1 (SELECTED)** | **Server-side coercion in `_handle_commit` reusing `source_for_tier`** | 5 | 5 | 5 | 5 | 5 | 5 | 2 | **4.85** |
| H2 | Widen the `Literal` / JSON schema to accept any string | 3 | 3 | 2 | 5 | 2 | 1 | 3 | 2.70 |
| H3 | pydantic `field_validator` / `BeforeValidator` on `Provenance.source` | 4 | 4 | 3 | 5 | 3 | 3 | 4 | 3.70 |
| H4 | Catch `ValidationError` at the handler boundary + retry with fallback | 3 | 3 | 2 | 4 | 2 | 2 | 2 | 2.65 |

**Selected: H1.** Conservative + pattern-leveraging: reuses the existing, tested `source_for_tier` primitive (GT-5) and the existing ingest tolerance pattern (GT-4); touches one handler; no schema, envelope, manifest, or hash change (GT-10/GT-13); the model already in scope (`caller_tier`, GT-7) makes it trivial. Lowest risk, highest alignment.

**Rejected alternatives (prevent re-exploration):**
- **H2 — widen the enum/schema:** violates RISK-1 / C-1 (frozen schema → `install.sh` second-run-diff), launders trust semantics (any string would bypass the trust class), and would force a coordinated schema + manifest + ECL re-issue. This is the **escalation** path the spec explicitly forbids as a default. Only revisit if product decides `source` should be free-text — a different change.
- **H3 — pydantic validator on the model:** the validator can't see `caller_tier` (it's not a model field; `Provenance` is `extra="forbid"`), so it could not honor C-2 (never-human-non-T0) without smuggling tier into the model — a worse coupling. Also fires on every `Provenance` construction site (ingest, `__main__`), widening blast radius beyond the bug.
- **H4 — catch-and-retry at the boundary:** reconstructs `Provenance` twice, obscures intent, and still needs the tier mapping to pick the fallback — strictly more code than computing the value once up front.

---

## 7. CONSTRUCT — story hierarchy

```
PROJECT: Harness-agnostic provenance coercion in commit
└── FEATURE: Seamless, observable commit provenance
    ├── STORY S1  source coercion (core)            — P0
    ├── STORY S2  created_at parse tolerance (GAP-1) — P0
    ├── STORY S3  observable advisory (DECISION-1)   — P1
    ├── STORY S4  regression tests                   — P0
    └── STORY S5  layer near-miss normalization (GAP-2) — OPTIONAL / stretch, P2
```

### STORY S1 — `provenance.source` coercion (core) — P0
*As the LLM driving any harness, I want `commit` to accept a descriptive `source` so that I never hit a `literal_error` retry loop.*
- **Timebox:** ≤1d
- **Action Plan:**
  - Extend the existing import at `server.py:50` to add `source_for_tier`.
  - Add pure helper `coerce_provenance_source(raw, caller_tier) -> (str, bool)` per §4 (module-level in `server.py`, or a small private fn near `_handle_commit`).
  - In `_handle_commit` (`server.py:959-964`), replace `source=raw_prov.get("source","unverified_agent")` with the helper result; construct `Provenance(source=final_source, …)`.
  - Apply for `layer ∈ {episodic, semantic, procedural}`; the `execution` branch uses no provenance — leave untouched.
- **Technical context:** GT-1/2/5/6/7. `caller_tier: Tier` already in scope.
- **Agent hints:** Reasoner/Builder. Context: `server.py`, `ingest_adapter.py`, `schemas.py`. Gate: `make test-file F=mcp-server/tests/test_server.py`.
- **Acceptance (GIVEN/WHEN/THEN):** see **G-SOURCE-COERCE-DESCRIPTIVE**, **G-SOURCE-IDENTITY**, **G-SOURCE-NEVER-HUMAN-NON-T0**.

### STORY S2 — `created_at` parse tolerance (GAP-1) — P0
*As the LLM, I want `commit` to tolerate `Z`-suffixed / epoch / non-ISO `created_at` so that a timestamp shape never crashes the call.*
- **Timebox:** ≤1d
- **Action Plan:** in `_handle_commit` (`server.py:956-958`), mirror the ingest tolerance (GT-4): `try: fromisoformat(raw.replace("Z","+00:00")) except ValueError: now_iso`. Additionally handle numeric epoch (`int`/`float`) via `datetime.fromtimestamp(raw, tz=utc)` guarded by `(ValueError, OSError, OverflowError) → now_iso` (FINDING-007 calls out epoch ints explicitly; ingest only handled `str`). On fallback, record a coercion note for S3.
- **Technical context:** GT-3 (gap) mirroring GT-4 (existing tolerant pattern). Use the already-bound `now_iso` (`server.py:955`).
- **Agent hints:** Builder. Context: `server.py:955-964`, `server.py:1055-1061`. Gate: `make test-file F=mcp-server/tests/test_server.py`.
- **Acceptance:** **G-CREATEDAT-TOLERANT**.

### STORY S3 — observable advisory (DECISION-1) — P1
*As a debugging human, I want a non-fatal advisory when a commit's provenance was coerced so that the reclassification is auditable without a retry.*
- **Timebox:** ≤1d
- **Action Plan:** when S1 and/or S2 coercion fires, attach a conditional `provenance_coercion` field (or `provenance_coercions` list) to the result dict returned by `_handle_commit` (GT-12), carrying `{field, from, to}`. **Attach nothing on the IDENTITY/clean path** so the result is byte-identical to today.
- **Technical context:** GT-11 (error channel ≠ this), GT-12 (success result dict). Result dict is the mutable return of `layers/*.commit`.
- **Agent hints:** Builder. Gate: `make test-file F=mcp-server/tests/test_server.py` + `test_ecl_envelope.py`.
- **Acceptance:** **G-ADVISORY-OBSERVABLE** (+ the no-coercion byte-identity clause inside **G-EXISTING-TESTS-GREEN**).

### STORY S4 — regression tests — P0
*As a maintainer, I want positive + identity tests so that the seam can't silently regress.*
- **Timebox:** ≤1d
- **Action Plan:** add to `mcp-server/tests/test_server.py` (and a `never-human` guard near `test_protected.py`): the new positive test (descriptive→`verified_agent`, `committed`), the IDENTITY test (each of the 4 valid values unchanged + `author_agent` preserved), the never-human-non-T0 test, and the `created_at` tolerance cases (epoch int, `Z`, garbage→fallback). Assert the advisory appears **only** on coercion and is **absent** on the clean path.
- **Agent hints:** Builder. Container-first: `make test`.
- **Acceptance:** all gates G-* below.

### STORY S5 — `layer` near-miss normalization (GAP-2) — OPTIONAL / STRETCH — P2
> **Ship S1–S4 independently. S5 is optional and must not block the core fix.**
*As the LLM, I want near-miss `layer` tokens (`"episodic-memory"`, `"memory"`, `"episodic_memory"`) normalized so that a descriptive layer name doesn't force a retry.*
- **Timebox:** ≤1d
- **Action Plan (if taken):** before the `layer ==` dispatch in `_handle_commit` (`server.py:973-986`), map known aliases → canonical `{episodic,semantic,procedural,execution}` via a small alias table; unknown still raises the **structured** `UNKNOWN_LAYER` (today's behavior, GT — `server.py:982-986`). Emit a `layer_coercion` advisory consistent with S3.
- **Why separable:** today this is a *structured* error, not a raw `ValidationError` — lower severity than the source bug. Keep it out of the core to keep blast radius minimal.
- **Acceptance:** **G-LAYER-NORMALIZE** (optional).

---

## 8. Acceptance gates (named, GIVEN/WHEN/THEN)

| Gate ID | Priority | Verify |
|---|---|---|
| **G-SOURCE-COERCE-DESCRIPTIVE** | P0 | **GIVEN** a T1 caller **WHEN** `commit` is called with `provenance.source="spectra-planning-session"` (and a valid `author_agent`) **THEN** the result `status=="committed"`, the stored crystal's `provenance.source=="verified_agent"`, `author_agent` is preserved verbatim, and no `ValidationError` is raised. | new test in `test_server.py` |
| **G-SOURCE-IDENTITY** | P0 | **GIVEN** any of `{human, verified_agent, unverified_agent, environment}` as `source` **WHEN** `commit` is called **THEN** the stored `source` is **identical** (byte-for-byte) and **no** `provenance_coercion` advisory is attached. | new test |
| **G-SOURCE-NEVER-HUMAN-NON-T0** | P0 | **GIVEN** a non-T0 caller (T1/T2/T3) **WHEN** `source` is absent / empty / descriptive **THEN** the coerced `source` is **never** `"human"` (it is `verified_agent` for T1, `unverified_agent` for T2/T3) — so no spurious permanent forgetting-protection (`protection.py:54-58`). | new guard test |
| **G-CREATEDAT-TOLERANT** | P0 | **GIVEN** `created_at` as an epoch int, a `Z`-suffixed ISO string, or a non-ISO garbage string **WHEN** `commit` is called **THEN** it parses where possible (epoch/`Z`) or safely falls back to server-now (garbage) and **never crashes**. | new test |
| **G-ADVISORY-OBSERVABLE** | P1 | **GIVEN** a coercion fires (source and/or created_at) **WHEN** `commit` succeeds **THEN** the result carries a non-fatal `provenance_coercion` advisory with `{field, from, to}`; **GIVEN** no coercion **THEN** no advisory field is present. | new test |
| **G-NO-SCHEMA-DRIFT** | P0 | **GIVEN** the fix is applied **WHEN** the tree is inspected **THEN** no `schemas/*.v1.json`, no ECL envelope schema, and no `install.manifest.json` is modified, **AND** `install.sh` second-run-no-diff stays green. | `git diff --stat schemas/ install.manifest.json` empty; `make` install idempotency job |
| **G-EXISTING-TESTS-GREEN** | P0 | **GIVEN** the full suite **WHEN** `make test` runs in-container **THEN** all 8 gates pass, and specifically `test_promotion_gate.py:215-255` (witness independence), `test_protected.py` (human-protection), `test_trust_propagation.py`, `test_ecl_envelope.py` are unchanged and green. | `make test` |
| **G-LAYER-NORMALIZE** *(optional, S5)* | P2 | **GIVEN** `layer="episodic-memory"` (or `"memory"`) **WHEN** `commit` is called **THEN** it routes to `episodic` and succeeds; an unknown layer still returns the structured `UNKNOWN_LAYER` error (not a crash). | optional test |

---

## 9. TEST — 6-layer verification (SPECTRA self-check)

| Layer | Result |
|---|---|
| Structural | Hierarchy intact (1 Project → 1 Feature → 5 stories, S5 isolated/optional). Stories independent: S1, S2, S3, S4 are P0-shippable; S5 detachable. No orphan tasks. **PASS** |
| Self-Consistency | Three decompositions (by-symptom / by-file / by-invariant) converge on the same single-handler edit + reuse-`source_for_tier` core (>70% overlap). **PASS** |
| Dependency | All sites enumerated and re-verified: failure `server.py:959-964`; gap `server.py:956-958`; primitive `ingest_adapter.py:125-127`; import `server.py:50`; result `server.py:708,797` + `layers/episodic.py:257-264`; downstream reads `protection.py:54-58`, `gate.py:341-351`. Siblings (ingest/`__main__`/update) confirmed out of scope. **PASS** |
| Constraint | C-1 no schema edit (server-side only); C-2 never-human-non-T0 (G-SOURCE-NEVER-HUMAN-NON-T0); C-3 identity (G-SOURCE-IDENTITY); C-4 container-first (make targets); C-5 deterministic/total mapping. **PASS** |
| Process Reward | Each story strictly reduces seam surface; ordering S1→S2→S3→S4 (S5 last/optional) is optimal — core lands first, advisory layers on, tests lock. **PASS** |
| Adversarial | Checked: under-spec (helper signature pinned), absent-default behavior shift (called out + verified no test pins it), advisory-vs-hash interaction (conditional attach keeps identity bytes stable), witness-independence narrowing (RISK-3 accepted — strict improvement over rejection; `author_agent` still differentiates). **PASS** |

---

## 10. Risks & mitigations

| ID | Risk | Mitigation (gate) |
|---|---|---|
| RISK-1 | Tempting to widen the frozen `Literal`/JSON schema | C-1 + G-NO-SCHEMA-DRIFT; widen is an explicit **escalation**, never default. H2 rejected. |
| RISK-2 | Coercing to `"human"` for a non-T0 caller → permanent protection (W4 breach) | Route every FALLBACK through `source_for_tier(caller_tier)` (only T0→human). G-SOURCE-NEVER-HUMAN-NON-T0. |
| RISK-3 | Many descriptive strings collapse to one enum → slightly narrows witness independence | Accepted: strictly better than today's outright rejection; `author_agent` (verbatim) still differentiates witnesses. Documented, not gated. |
| RISK-4 | Absent-source default shifts (T0→human, T1→verified_agent) could surprise a pinned test | Re-verified none pin it (§4); G-EXISTING-TESTS-GREEN backstops. |
| RISK-5 | Advisory field could break a result-shape assertion | Conditional attach — clean path is byte-identical (G-EXISTING-TESTS-GREEN + G-ADVISORY-OBSERVABLE no-coercion clause). |

---

## 11. Container-first note (implementer)

All verification runs **inside** the `crystalium` compose service (P0-13). The host runs only `docker compose`, `git`, `make`.

```bash
make test                                          # full suite (all 8 gates) — REQUIRED green
make test-file F=mcp-server/tests/test_server.py   # focused on the new tests
make lint                                          # ruff
make typecheck                                     # mypy
make schema                                        # JSON-schema validity (must be unchanged)
```

**Do NOT** run `python`/`pip`/`uv`/`pytest` on the host (CLAUDE.md §Container-first; the `no-host-python` guard will block it).

---

## 12. Confidence report

**Confidence: 92% → AUTO_PROCEED.**

| Factor (25% ea.) | Score | Basis |
|---|---|---|
| Pattern match | 95% | Reuses `source_for_tier` (GT-5) + ingest tolerance pattern (GT-4) verbatim |
| Requirement clarity | 95% | Goal + victim + contract explicit; sole policy resolved as flippable DECISION-1 |
| Decomposition stability | 88% | 3 decompositions converge; S5 cleanly detachable |
| Constraint compliance | 90% | All C-1..C-5 mapped to gates; schema-freeze respected |

---

## 13. Handoff → APIVR-Δ / Vivi

- **Edge:** `spectra → apivr` (performative `PROPOSE`). ECL v2.0 sidecar emitted alongside this spec.
- **Implement order:** S1 → S2 → S3 → S4 (P0 core, shippable). S5 optional/stretch, only if time permits — must not block.
- **maker(vivi) ≠ checker(vigil):** Vigil drift-checks against this spec at `delivered` before archive.
- **Definition of done:** all P0 gates green via `make test` in-container; `git diff` shows zero changes under `schemas/` and to `install.manifest.json`; the spec's GIVEN/WHEN/THEN map 1:1 to `acceptance_checks` in `change.json`.
- **Single most load-bearing edit:** `mcp-server/src/crystalium/server.py` — `_handle_commit`, lines `956-964` (created_at parse + source construction), plus the `source_for_tier` import at `:50`.

*SPECTRA — Strategic Specification through Deliberate Reasoning. Plans only; no code produced.*
