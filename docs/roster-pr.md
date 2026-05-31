# Roster-entry PR draft — CRYSTALIUM → Rynaro/eidolons

**Status: DRAFT for operator review. Do NOT consider CRYSTALIUM a published roster
member until the operator opens + merges this entry into the external nexus repo
`Rynaro/eidolons`.** This file is the in-repo draft; the actual PR is an
external-repo artifact the operator creates.

## What to add to the nexus `members:` list

Mirror the existing member schema (`eidolons.yaml` source-manifest form):

```yaml
- name: crystalium
  version: "^1.0.0"
  source: github:Rynaro/CRYSTALIUM
  role: shared-memory-substrate
  capability_class: memory          # confirm against the nexus capability_class enum
  grants_to_eidolons: all           # every roster Eidolon gets the recall/commit/ingest tools
```

And bump the stable pin:

```yaml
versions:
  latest: 1.0.0
  pins:
    stable: 1.0.0
```

## Why CRYSTALIUM is roster-ready (1.0 evidence)

- **Conformance:** `pytest -m conformance` green — every mechanical invariant (G1–G8 +
  path-escape, rate-limit, ECL 11-field+SHA-256 integrity, trust-tier MIN propagation,
  never-hard-delete + the RTBF exception, working-set ≤3500) has a passing test, run as
  a blocking CI job.
- **Integration:** the ATLAS→SPECTRA→APIVR-Δ→IDG round-trip ingests every handoff
  through `crystalium.ingest` with provenance + MIN trust tier intact (W7 DoD gate).
- **Bidirectional substrate:** AGENTS.md frontmatter declares
  `handoffs.upstream/downstream` = the full roster; CRYSTALIUM both ingests artifacts
  from and serves recall to every member.
- **EIIS v1.4 / ECL v2.0 conformant:** install manifest validates; ECL emit + ingest.
- **Honest ablation record:** 8 augments A/B-tested (2 ON, 6 documented nulls); see
  `DESIGN-RATIONALE.md` §D6.7.

## Known caveats to disclose in the PR

- Canary memory-on beats memory-off (+0.75) but lands below the 0.80 bar by one
  recall-after-update `[GAP]` (documented, deferred post-1.0).
- Availability SLO: 100% availability; recall p95 ~205 ms (embedder-bound, marginally
  over the 200 ms target). Both `[PROXY]` (synthetic harness).
- Confirm the nexus `capability_class` enum value before opening the PR.

## Operator checklist (outside this repo)

1. Tag the release: `git tag v1.0.0 && git push origin v1.0.0` (fires `release.yml` →
   GHCR image + GitHub release).
2. Open the `Rynaro/eidolons` PR adding the member entry above + the pin bump.
3. On merge, flip README from "roster-ready; publication pending" to "roster member".
