# Composition Methodology

Loaded during the Draft phase. Governs how the Scribe transforms context into structured prose.

---

## Envelope-Aware Intake (ECL v1.0)

When classifying a source artefact during the **I — Intake** phase, check whether the artefact arrived with an ECL sidecar envelope. The four steps below are always executed in order when any `*.envelope.json` file is present; the overall verification is **warn-only** — IDG always produces the document.

### Step 1 — Detect

Look for a file whose name is `<payload-basename>.envelope.json` in the same location as the payload. Examples:

- Payload: `apivr-completion-report.md` → Sidecar: `apivr-completion-report.md.envelope.json`
- Payload: `root-cause-report.md` → Sidecar: `root-cause-report.md.envelope.json`

If **no sidecar is found**, proceed normally. In the provenance block note: "no envelope; ECL verification skipped." No CHT score impact.

### Step 2 — Validate

Validate the sidecar JSON against the vendored schema at `schemas/ecl-envelope.v1.json`.

**Edge case — version outside compatibility range**: If `envelope.envelope_version` does not match `^1\.0(\.\d+)?$`, add a `[GAP]` marker:

```
[GAP] envelope_version <X.Y> is outside the IDG v1.2.0 compatibility range (1.0.x).
Verification skipped; chronicle proceeds without envelope provenance.
```

### Step 3 — Recompute and compare sha256

Compute the sha256 digest of the payload file and compare it to `envelope.integrity.value`.

**Only `method: "sha256"` is supported.** If `integrity.method == "hmac-sha256"`, add:

```
[GAP] integrity.method is hmac-sha256; shared secret unavailable to IDG.
Verification inconclusive; treating envelope as informational only.
```

**Digest matches** → record `verify_pass` in working memory.
**Digest mismatches** → record `verify_fail` in working memory; add `[DISPUTED]` to the chronicle:

```
[DISPUTED] ECL envelope sha256 mismatch for <payload-path>.
Envelope claims <expected>; recomputed digest is <actual>.
Source integrity cannot be confirmed; treat content with caution.
```

### Step 4 — Check performative

Confirm that `envelope.performative` is in the allowed inbound set for the sender:

| Sender (`from.eidolon`) | Allowed performatives |
|---|---|
| `apivr` | `PROPOSE`, `INFORM` |
| `vigil` | `PROPOSE`, `INFORM` |
| any other | any (treat as `INFORM`) |

If the performative is outside the allowed set for the declared sender, add:

```
[GAP] Unexpected performative <X> from <sender>; treating as INFORM and proceeding.
```

### Record and trace

After Steps 1–4, record the full outcome in working memory:

```
ecl_verification:
  message_id: <uuid>
  thread_id: <uuid>
  from: <eidolon slug>
  performative: <value>
  outcome: verify_pass | verify_fail | inconclusive | skipped
```

This record surfaces in the Gate phase and populates the chronicle's Communication Lineage section and provenance block.

**RECOMMENDED**: Append a trace event at `.eidolons/.trace/<thread_id>.jsonl` per ECL §5.1.2:

```jsonl
{"event":"receive","ts":"<RFC3339>","message_id":"<uuid>","from":"<slug>","to":"idg"}
{"event":"verify_pass","ts":"<RFC3339>","message_id":"<uuid>"}
```

(or `"verify_fail"` with a `"reason"` field on mismatch). This trace is RECOMMENDED, not REQUIRED.

**IDG does not, and shall not, fetch any envelope referenced via `input_handles`**. P0 forbids retrieval. If a handle resolves to a path already in-context (the requester has explicitly provided the file), reading it is permitted. Otherwise mark `[GAP]`.

### Worked examples

**Example A — verify_pass**

Input: `report.md` + `report.md.envelope.json`
- Schema validates. `integrity.method == "sha256"`. `sha256(report.md)` matches `envelope.integrity.value`. `performative == "PROPOSE"` (allowed from `apivr`).
- Working memory: `outcome: verify_pass`, `performative: PROPOSE`, `message_id: abc-123`.
- Provenance block entry: `ecl://thread/<thread_id>/message/abc-123 — verify_pass`.

**Example B — verify_fail (sha256 mismatch)**

Input: `report.md` + `report.md.envelope.json`
- Schema validates. `integrity.method == "sha256"`. Recomputed digest does NOT match `envelope.integrity.value`.
- Working memory: `outcome: verify_fail`.
- Chronicle body gains:
  ```
  [DISPUTED] ECL envelope sha256 mismatch for report.md.
  Envelope claims d4e5f6...; recomputed digest is a1b2c3....
  Source integrity cannot be confirmed; treat content with caution.
  ```
- Provenance block entry: `ecl://thread/<thread_id>/message/<id> — verify_fail`.
- Truthfulness score: NOT automatically lowered; the `[DISPUTED]` marker documents the discrepancy. Document is delivered with the flag.

---

## Section-Level Composition

Write one section at a time, in topological order. For each section:

1. **Scope the section** — what question does this section answer for the reader?
2. **Select context** — pull only the source material relevant to this section. Discard the rest for now.
3. **Draft** — write the section. Ground every claim.
4. **Cite** — tag each factual statement with its source artifact (inline or footnote, per template convention).
5. **Mark** — apply structural markers where appropriate.
6. **Transition** — write the bridge to the next section if the document type requires narrative flow.

### Context Budget Rule

When composing a section, keep injected context to the minimum necessary. If the full source material for a section exceeds ~2,000 tokens, summarize the supporting evidence and keep the original references as citations. This preserves working space for the model to reason about composition rather than drowning in raw material.

### Topological Section Order

Write sections that establish context before sections that depend on it:

- Background/Context → Decisions → Consequences
- Problem Statement → Steps Taken → Outcomes → Lessons
- Summary → Details → Follow-ups

If sections have no dependency relationship, write them in the template's default order.

---

## Structural Markers Reference

Markers are the Scribe's primary value-add. They transform passive documentation into actionable intelligence.

### `[DECISION]`
A choice was made. Always include:
- **What** was decided
- **Why** (rationale, even if brief)
- **Alternatives rejected** (if available in source material; `[GAP]` if not)

```markdown
[DECISION] Adopted Redis for session caching over Memcached.
Rationale: Redis supports data structures needed for rate-limiting (sorted sets).
Rejected: Memcached (no sorted sets), DynamoDB (latency budget exceeded).
```

### `[ACTION]`
Something needs to happen. Include:
- **What** needs doing
- **Owner** (if known; `TBD` if not)
- **Deadline or trigger** (if known)

```markdown
[ACTION] Update Terraform modules to provision Redis cluster. Owner: Platform team. Trigger: Before Sprint 14 deployment.
```

### `[DISPUTED]`
Source material conflicts. Present both sides neutrally:

```markdown
[DISPUTED] Load test results disagree on p99 latency.
- Agent A's benchmark: 12ms p99 under 10k RPS
- Manual test by engineer: 45ms p99 under 8k RPS
Resolution: Not yet determined. Recommend re-running with standardized methodology.
```

### `[GAP]`
Expected information is missing from provided context:

```markdown
[GAP] Rollback procedure not documented in session artifacts. Requested from Ops team.
```

---

## Writing Standards

### Grounding Rules

| Rule | Detail |
|------|--------|
| **No unsourced claims** | Every factual assertion must trace to a specific source artifact |
| **Distinguish inference from fact** | If the Scribe infers something (e.g., likely rationale for a decision), prefix with "Likely:" or "Inferred:" |
| **Preserve source fidelity** | Do not editorialize or interpret beyond what sources support |
| **Quote sparingly** | Paraphrase unless exact wording is load-bearing (error messages, commit messages, CLI output) |

### Audience Adaptation

| Audience | Depth | Jargon | Examples |
|----------|-------|--------|----------|
| Engineers on the team | Full technical depth | Domain terms OK without definition | Code snippets, file paths, CLI commands |
| Engineering leadership | Architectural level | Define non-obvious acronyms | Diagrams, trade-off summaries, impact statements |
| Cross-functional | Business outcomes | Minimal jargon, explain technical terms | User-facing impact, timelines, risk levels |

Determine audience from the document type default or explicit instruction. When uncertain, default to "Engineers on the team."

### Tone

- **Active voice** preferred ("The team decided..." not "It was decided...")
- **Concrete over abstract** ("Added 3 retry attempts with exponential backoff" not "Improved error handling")
- **Terse over verbose** — every sentence should earn its place
- **No hedging without cause** — "The migration completed successfully" not "The migration appears to have completed successfully" (unless completion is genuinely uncertain)

---

## Section Composition Checklist

Before moving to the next section, verify:

- [ ] Section answers the question it was scoped to answer
- [ ] Every factual claim has a source citation
- [ ] Structural markers applied where appropriate
- [ ] Tone matches audience level
- [ ] No information fabricated or assumed without marking
- [ ] Transition to next section is clear (if applicable)

---

*Scribe v1.2.0 — Composition Skill*
