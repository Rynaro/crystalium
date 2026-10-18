# Structured repair records

Issue [#73](https://github.com/Rynaro/crystalium/issues/73) adds `fix_span.v1`:
bounded repair facts stored in the existing four-layer Crystal model. Legacy
records remain compatible. This records a reported repair; it does not verify
that a fix works or measure coding performance.

## Enable writes

Both feature flags default to false. In YAML configuration:

```yaml
fix_span_enabled: true
fix_span_extractor_enabled: false
repo_root: /app
```

Set `repo_root` to the existing repository directory visible inside the server
container. File/directory anchors require this configured root; without it they
are rejected. `sandbox_root` belongs to skill execution and is not a substitute.
The environment-based flag settings are `CRYSTALIUM_FIX_SPAN_ENABLED` and
`CRYSTALIUM_FIX_SPAN_EXTRACTOR_ENABLED`. Configure `repo_root` through YAML or
`Config`; there is no corresponding environment setting.

`fix_span_enabled` enables declared repair writes and updates.
`fix_span_extractor_enabled` enables the optional proposal hook only when the
first flag is also true. With either flag off, ingest performs no extraction,
proposal generation, or extra source reads. Turning both off still permits
reading/rendering previously stored repair records; declared writes reject with
`fix_span_disabled` when the write flag is off.

## Commit a repair

Use the existing `commit` tool. Supply both `payload.kind="fix_span"` and
`payload.fix_span`; an incomplete or mismatched pair is rejected. Example tool
arguments, after enabling writes and configuring the repository root:

```json
{
  "layer": "episodic",
  "payload": {
    "kind": "fix_span",
    "scope": {"project": "<canonical-project-key>"},
    "fix_span": {
      "schema_version": "1.0",
      "symptom": "Parser rejected an empty optional field.",
      "root_cause": "The optional field used the required-field branch.",
      "fix_pattern": "Handle the empty optional field before required-field validation.",
      "anchors": [{"kind": "file", "value": "src/parser.py"}],
      "constraints": ["Keep required-field validation unchanged."],
      "locator": {"kind": "pr", "value": "repair-example"}
    }
  },
  "provenance": {"source": "verified_agent", "author_agent": "idg"}
}
```

Use the canonical project key advertised by server initialization. The service
normalizes write scope through its existing path. The caller's authoritative
identity and outer Crystal provenance determine trust; fields inside `fix_span`
cannot supply a tier, provenance, or promotion override.

| Field | Contract |
|---|---|
| `schema_version` | Literal `"1.0"`; defaults to this value |
| `symptom` | Nonblank string, at most 200 characters |
| `root_cause` | Nonblank string, at most 300 characters |
| `fix_pattern` | Nonblank string, at most 400 characters |
| `anchors` | At least one `{kind,value}` object; kind is `file`, `dir`, or `symbol` |
| `constraints` | Required list of strings; an empty list is allowed |
| `locator` | Required `{kind,value}` object; kind is `commit`, `pr`, or `handoff`; value is nonblank |

The repair and nested anchor/locator models reject extra fields. See the
[JSON Schema](../schemas/fix-span.v1.json).

File/directory anchors are historical, repository-relative POSIX references:
redundant `.` and separators normalize, and nonexistent suffixes are allowed.
Empty, absolute, drive/UNC, backslash, NUL, and `..` paths are rejected. Existing
symlink prefixes must resolve beneath `repo_root`. Symbol anchors preserve spelling
and accept identifiers qualified with `.` or `::` (for example `Parser.parse` or
`Parser::parse`), not filesystem paths. A locator is inert text; the server never
fetches or executes it. Runtime sandbox path checks retain their strict existence
requirements.

## Updates, trust, and lifetime

All four layer commits, typed ECL ingest, structured updates, and execution
checkpoint/replan paths validate before persistence. A rejected structured update
leaves the original active record intact. Updates cannot erase the structured
kind or discard previously verified contributing source references.

Trust is the least trusted of the caller, existing repair, and verified sources;
T3 dominates T1. Existing quarantine and layer ceilings remain enforced. Mixed
T1/T3 content can be stored only as quarantined episodic memory, and a more
privileged caller cannot upgrade its content trust.

Approximate summary dedup skips comparisons when either record is structured.
Dream excludes structured records from lossy concatenation, automatic admission,
and consolidation pruning. Existing explicit human lifecycle controls still
apply. Storage adds nullable typed columns idempotently, retaining older records
and their existing blob/provenance data.

## Optional ingest proposals

With both flags enabled, the decoded JSON payload passed to ECL `ingest` may
contain this candidate alongside its ordinary content:

```json
{
  "fix_span_candidate": {
    "fix_span": {
      "symptom": "Parser rejected an empty optional field.",
      "root_cause": "The wrong validation branch ran.",
      "fix_pattern": "Check optional emptiness first.",
      "anchors": [{"kind": "symbol", "value": "Parser.parse"}],
      "constraints": [],
      "locator": {"kind": "handoff", "value": "parser-repair-note"}
    },
    "source_ids": ["<existing-crystal-id>"]
  }
}
```

The candidate must contain exactly `fix_span` and a nonempty `source_ids` list.
The server resolves every source as active and authorized within the canonical
ingest project. Missing, inactive, cross-project, unauthorized, or unparseable
sources produce no proposal without exposing source content. Current ingest
trust also contributes; its stored id is added to the verified source list.
There is no inference from arbitrary prose, remote model call, or second write.

A successful response adds `fix_span_proposal` containing `kind`, `fix_span`,
`scope`, a rendered `summary`, suggested `layer="episodic"`, `source_ids`,
`extraction_method="explicit_ecl_candidate"`, `trust_tier`, `validation_state`,
and `encoding_context` with `fix_span_sources` and `extraction_method`.
T3 proposals display `validation_state="quarantined"`; other proposals display
`"unverified"`. Absent proposals leave the ordinary ingest response unchanged.

A proposal is inert. To store it, explicitly call `commit` with episodic layer,
the proposal's `kind`, `fix_span`, `scope`, and `encoding_context` in `payload`,
and ordinary outer provenance. Preserve `encoding_context.fix_span_sources`:
enforcement re-resolves those ids and derives authoritative trust on recommit.
Displayed `trust_tier`, `validation_state`, or top-level `source_ids` do not
establish write authority. Stale or invalid referenced sources reject recommit.

## Recall, errors, and observability

Recall renders fields in fixed order: symptom, root cause, fix, anchors,
constraints, locator. Empty constraints render as `none`. That text passes through
existing redaction before composition; the returned summary, composer budget,
and context-injection token count use the same redacted text. Recall does not
reload an unredacted blob afterward.

The [conditional assembly policy](memory-injection.md) from
[#71](https://github.com/Rynaro/crystalium/issues/71) still applies: a closed gate
injects nothing, and an open gate keeps its final record cap. Typed storage does
not bypass project scope, active-record checks, tier ceilings, or redaction.

Structured validation reasons are `fix_span_disabled`, `missing_anchor`, `cap`,
`path`, and `malformed`, surfaced through each entry point's existing error
convention. Existing tier/ceiling errors can also reject a write. Enforcement
records content-free accepted, rejected, and downgraded outcomes with reasons;
the enabled proposal hook also records skipped outcomes such as `no_candidate`
or invalid sources. Repair text and anchor values are excluded from telemetry
labels.

Live coding evaluation remains separate in
[#74](https://github.com/Rynaro/crystalium/issues/74). Upstream agent integration
is tracked in [eidolons#635](https://github.com/Rynaro/eidolons/issues/635).
