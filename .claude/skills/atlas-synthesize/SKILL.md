---
name: atlas-synthesize
description: Phase S (Synthesize) — emit the scout-report.md artifact that downstream agents (SPECTRA, APIVR-Δ, human) consume. Use after Phase A (Abstract) fold is validated. Hard cap 3000 tokens; every factual clause cited with FINDING-XXX; every recommended action carries a handoff label.
when_to_use: After fold summary validates; when DECISION_TARGET has an answer or its gaps are recorded. Final phase of an ATLAS mission.
allowed-tools: memex_read view_file
methodology: ATLAS
methodology_version: "1.0"
phase: S
---

# SKILL: Synthesize — scout report emission (Phase S)

**Load when:** fold summary is validated and all `DECISION_TARGET` sub-questions
are either answered or recorded as gaps.

**Unload when:** `scout-report.md` is emitted and handed off.

---

## Contract

| Field | Value |
|-------|-------|
| LLM calls permitted | Yes — this is the substantive synthesis edge. |
| Tool budget | ≤ 15% of mission `max_tool_calls`. Mostly `memex.read` for quoted excerpts. |
| Output | `scout-report.md` conforming to `templates/scout-report.md` |

---

## What this phase does

Emit the single artifact downstream agents will consume. Everything
upstream was preparation; this is the delivery. The report must be:

- **Self-contained.** A SPECTRA or APIVR-Δ agent receiving it should not
  need to re-explore the codebase.
- **Decision-ready.** Answers `DECISION_TARGET` explicitly, not implicitly.
- **Handoff-labeled.** Every recommended action is tagged with the right
  downstream agent.
- **Small.** Hard cap 3000 tokens total. If you need more, the mission
  should have been split.

---

## Structure (mandatory, in order)

### 1. Mission recap (≤150 tokens)

Copy verbatim from `mission.md`:

- `MISSION-ID`, `GOAL`, `DECISION_TARGET`, `SCOPE`.

Do not paraphrase. Downstream agents key off these fields.

### 2. Topology summary (≤500 tokens, ≤10 bullets)

Distilled from `map.md`. Each bullet is one structural fact with at least
one `path` reference. Example:

```
- HTTP entrypoint: POST /ballots/:id/cast → BallotsController#cast
  (config/routes.rb:42).
- Single FlowObject handles vote recording: RecordVote
  (app/flows/vote_casting/record_vote.rb).
- Authorization policy: VotingAuthorizer
  (app/policies/voting_authorizer.rb).
- No background-job path; synchronous only [GAP-003, M].
- Test coverage: test/flows/record_vote_test.rb (42 cases).
```

### 3. Answer to DECISION_TARGET (≤800 tokens)

The substantive deliverable. Every factual clause is followed by a
`FINDING-XXX` reference. If a question couldn't be answered, say so and
cite the `GAP-XXX`.

This section is where the LLM earns its keep. Graph retrieval and AST
parsing got the facts; synthesis turns them into an answer shaped like what
a senior engineer would write in a PR description or a design doc comment.

**Style rules:**

- One answer per sub-question. No meandering.
- Confidence tier flows through: M-tier findings warrant "likely" language;
  L-tier findings are flagged as assumptions.
- No new claims beyond what findings establish. If you notice a gap while
  writing, stop and decide: spawn follow-up mission, or record in §5.

### 4. Recommended next actions (≤600 tokens)

Ranked list. Each item:

```
R-1 [→ SPECTRA] | priority: high
    Draft spec for hardening authorization on RecordVote#call to cover
    the anonymous-ballot case [GAP-003].
    References: FINDING-017, GAP-003.

R-2 [→ APIVR-Δ] | priority: medium
    Add a test asserting that RecordVote#call refuses guest users.
    References: FINDING-017. Estimated timebox: up to 0.5 days.

R-3 [→ human]   | priority: high
    Decide policy for retry behavior when cast_vote_records write fails
    mid-request. No existing convention.
    References: GAP-003, FINDING-021.
```

**Handoff labels are mechanical:**

- `→ SPECTRA` — needs spec generation before implementation.
- `→ APIVR-Δ` (or `→ APIVR`) — spec is clear; ready for implementation loop.
- `→ human` — blocked on a judgment call you cannot make.
- `→ ATLAS` — deserves a follow-up scout mission (max recursion = 1).

### 5. Risks & gaps (≤300 tokens)

Every `GAP-XXX` surfaced in Abstract, plus any risk identified during
synthesis. Each entry gets a confidence tier and, if possible, a proposed
mitigation.

### 6. Telemetry (≤100 tokens)

```
phase   | tokens_in | tokens_out | tool_calls
A (Assess)      | 412   | 280    | 0
T (Traverse)    | 2,104 | 620    | 14
L (Locate)      | 8,730 | 1,840  | 47
A (Abstract)    | 1,520 | 1,980  | 3
S (Synthesize)  | 2,100 | 2,450  | 6
TOTAL           |14,866 | 7,170  | 70

fold_ratio: 0.09
η (search efficiency): 0.28
```

---

## Synthesis anti-patterns

| Anti-pattern | Why it's wrong | Fix |
|--------------|----------------|-----|
| Restating every finding | Report bloat, obscures the answer | §3 is prose, not a findings dump; the index is already in the fold |
| Adding new claims ("also, I noticed...") | Bypasses evidence anchoring | New observations = new findings = back to Locate |
| Omitting confidence language | Downstream misreads M as H | "Likely", "not directly verified", "inferred from X" — but only when tier is M or L |
| Generic next actions | Wastes downstream agent's context | Every recommended action has a concrete anchor and a labeled handoff |
| Answering questions the mission didn't ask | Scope creep | If it's valuable, propose it as R-N with `→ ATLAS` follow-up tag |

---

## Handoff emission

After `scout-report.md` is written, emit exactly one handoff block:

```
<handoff>
  <primary_recipient>SPECTRA</primary_recipient>
  <fallback_recipient>human</fallback_recipient>
  <report_path>artifacts/ATLAS/scout-report-MISSION-042.md</report_path>
  <critical_gaps>GAP-003</critical_gaps>
  <open_questions>
    - Retry policy on partial CVR write (R-3)
  </open_questions>
</handoff>
```

Downstream tooling parses this block deterministically.

### Envelope sidecar (ECL v1.0)

Immediately after the `<handoff>` block, also emit a `scout-report.envelope.json`
file adjacent to the scout report. This is a terminal Phase-S artefact in the
same class as `scout-report.md` — it is produced by the harness as Phase-S
output, NOT via a write tool call, preserving the I-1 read-only invariant.

Use `templates/scout-report.envelope.json` as the fill-in-the-blank skeleton.
Key fields to populate:

- `artifact.path` — relative path to the `scout-report.md` file (e.g.
  `artifacts/ATLAS/scout-report-MISSION-042.md`).
- `artifact.sha256` + `integrity.value` — SHA-256 hex digest of the scout-report
  file bytes. Compute with `shasum -a 256 <file> | awk '{print $1}'` or
  equivalent. The values MUST match.
- `artifact.size_bytes` — byte count of the scout-report file
  (`wc -c < <file> | tr -d '[:space:]'`).
- `from.version` — ATLAS SemVer at the time of emission (e.g. `"1.5.0"`).
- `to.eidolon` / `to.version` — primary handoff recipient (usually `"spectra"`).
  See `SPEC.md §6` for recipient mapping.
- `performative` — `"PROPOSE"` for ATLAS→SPECTRA scout handoffs (ECL v1.0 §2).
- `objective` — one sentence ≤240 chars matching the mission GOAL.
- `trace.host`, `trace.model`, `trace.ts` — fill from the active session context.

Reference contract: `eidolons-ecl/contracts/atlas-to-spectra.yaml` (in the
`Rynaro/eidolons-ecl` repo) defines the normative `from`/`to`/`edge_origin`/
`performative` values for the ATLAS→SPECTRA edge.

Validate the emitted envelope against `schemas/ecl-envelope.v1.json` before
marking Phase S complete.

---

## Exit gate

- [ ] All six sections present in order.
- [ ] Total report ≤ 3000 tokens.
- [ ] Every factual clause in §3 has a FINDING-XXX reference.
- [ ] Every R-N in §4 has a concrete handoff label.
- [ ] Every GAP-XXX from the fold appears in §5.
- [ ] Handoff block emitted and well-formed.
- [ ] Envelope sidecar emitted, schema-valid against `schemas/ecl-envelope.v1.json`, `integrity.sha256` matches payload.
- [ ] Memex remains intact; downstream agents can dereference anchors.

If exit gate fails, the mission did not complete. Report `STATUS: partial`
in the recap and escalate — do not ship a half-formed report.
