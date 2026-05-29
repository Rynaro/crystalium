---
name: apivr-verify-incoming
description: "Load when reading any upstream artefact handed off by ATLAS (scout-report), SPECTRA (spec), VIGIL (root-cause-report), or FORGE (reasoning-report). Checks for a sibling .envelope.json, validates it against ECL v1.0 schemas, and appends a verify_pass or verify_fail trace event. WARN-ONLY on failure — the payload is always processed."
methodology: APIVR-Δ
methodology_version: "3.1"
---

# Verify-Incoming Skill

Opt-in incoming envelope verification for APIVR-Δ v3.1. When a handoff artefact arrives with a sibling `.envelope.json`, this skill validates it and records the outcome. Failures are **warn-only** — the payload is never refused.

---

## Trigger

Load this skill automatically when:

- Entering the **A — Analyze** phase AND
- Reading an upstream artefact at path `P` AND
- A sibling file `${P%.*}.envelope.json` exists in the same directory

Detection rule (POSIX sh compatible):

```sh
envelope_path="${artefact_path%.*}.envelope.json"
[ -f "$envelope_path" ] && load_skill "verify-incoming"
```

The four inbound artefact kinds that trigger verification:

| Kind | From | Contract |
|---|---|---|
| `scout-report` | atlas | `contracts/atlas-to-apivr.yaml` |
| `spec` | spectra | `contracts/spectra-to-apivr.yaml` |
| `root-cause-report` | vigil | `contracts/vigil-to-apivr.yaml` |
| `reasoning-report` | forge | `contracts/forge-to-apivr.yaml` |

---

## Validation Pipeline

Run in order. Stop at first failure, **emit a warning, and continue processing the payload**.

### Step 1 — Schema shape (`SCHEMA_INVALID`)

Validate the envelope JSON against `.eidolons/apivr/schemas/ecl-envelope.v1.json`.

Using `jq` (shell):

```sh
jq empty envelope.json 2>/dev/null || { warn "SCHEMA_INVALID: malformed JSON"; return 0; }
# Then validate required fields: envelope_version, message_id, thread_id, parent_id,
# from, to, performative, objective, artifact, integrity, trace
```

If `jq` is absent, treat as best-effort: skip schema check, log advisory.

Failure code: `SCHEMA_INVALID`

### Step 2 — Integrity (`INTEGRITY_MISMATCH`)

Recompute SHA-256 of the payload bytes and compare against `envelope.integrity.value`.

```sh
computed=$(shasum -a 256 "$payload_path" | awk '{print $1}')
declared=$(jq -r '.integrity.value' "$envelope_path")
[ "$computed" = "$declared" ] || { warn "INTEGRITY_MISMATCH"; trace_fail "INTEGRITY_MISMATCH"; return 0; }
```

Failure code: `INTEGRITY_MISMATCH`

**Note on ECL §6.2.2**: The spec says receivers SHALL NOT process a payload on mismatch. APIVR-Δ adopts the weaker opt-in posture (ECL §0) — warn-only, payload is processed. This is documented in `DESIGN-RATIONALE.md` §ECL adoption.

### Step 3 — Contract match

Check three sub-conditions in order:

**3a — Declared edge** (`UNDECLARED_EDGE`): `from.eidolon` MUST be one of `atlas`, `spectra`, `vigil`, `forge`; `to.eidolon` MUST be `apivr`.

**3b — Performative allowed** (`PERFORMATIVE_NOT_ALLOWED`): check `performative` against the set declared in the relevant inbound contract:

| from | allowed performatives |
|---|---|
| atlas | PROPOSE, INFORM, REFUSE |
| spectra | PROPOSE, INFORM, REFUSE |
| vigil | PROPOSE, CRITIQUE, INFORM |
| forge | PROPOSE, INFORM, CRITIQUE |

**3c — Artifact kind allowed** (`ARTIFACT_KIND_NOT_ALLOWED`): check `artifact.kind` against the contract:

| from | allowed kinds |
|---|---|
| atlas | scout-report |
| spectra | spec |
| vigil | root-cause-report |
| forge | reasoning-report |

---

## Failure Mode (warn-only)

On any failure:

1. Print warning to stderr: `[apivr-verify-incoming] WARN: <FAILURE_CODE> from <from.eidolon>`
2. Append `verify_fail` event to `.eidolons/.trace/<thread_id>.jsonl`
3. **Continue processing the payload** (do not refuse, do not abort)

On success:

1. Append `verify_pass` event to `.eidolons/.trace/<thread_id>.jsonl`
2. Continue with the payload

---

## Trace Events

Append one JSONL line per verification:

**verify_pass:**
```json
{"ts":"<RFC3339>","event":"verify_pass","message_id":"<uuid>","thread_id":"<uuid>","from":"<eidolon>@<version>","to":"apivr@3.1.0","performative":"<performative>","integrity_method":"sha256"}
```

**verify_fail:**
```json
{"ts":"<RFC3339>","event":"verify_fail","message_id":"<uuid>","thread_id":"<uuid>","from":"<eidolon>@<version>","to":"apivr@3.1.0","performative":"<performative>","integrity_method":"sha256","verify_failure_code":"<CODE>"}
```

Trace directory: `.eidolons/.trace/<thread_id>.jsonl` (create if absent).

The `thread_id` comes from `envelope.thread_id`. If the envelope is invalid JSON, use a fallback `thread_id` of `unknown`.

---

## Token-Budget Advisory (Q2 from spec)

The inbound payload may be large. Apply the contract's declared `token_budget_max` as a soft cap:

| from | token_budget_max |
|---|---|
| atlas | 4000 |
| spectra | 6000 |
| vigil | 4000 |
| forge | 3000 |

If `envelope.context_delta.tokens_used > token_budget_max`, log a `CONTEXT_OVER_BUDGET` warning to stderr (warn-only; this does not add a `verify_fail` event).

---

## Notes

- Verification is **opt-in**: if no `.envelope.json` sibling is present, skip silently.
- This skill is **prompt-only** (no `bin/verify-incoming.sh`). Rationale: ATLAS v1.5.0 precedent; promote to a shell helper if bats proves the prompt-only contract is too loose (see `DESIGN-RATIONALE.md`).
- Schemas referenced: `.eidolons/apivr/schemas/ecl-envelope.v1.json` (installed by `install.sh`).

---

*Verify-Incoming Skill — warn-only, opt-in, trace-event-anchored*
