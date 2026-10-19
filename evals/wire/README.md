# Release wire verifier

CRYSTALIUM owns the active release verifier in `evals/wire/`. It replaces use of
the nexus archive's `compare_wire.py` for AC-361 / VP-M8. The nexus archive is
historical evidence, not a current release gate. Installed `.eidolons/` files
are not edited by this migration. Nexus release operators should invoke this
repository's verifier at the candidate commit and attach its output.

The default baseline is the actual **released v2.4.0** source at
`67a739c0f47b6decd5ba7895823f062695a2301f`, not current sources that still report
version 2.4.0. `baselines/v2.4.0.provenance.json` records exact source, image, wire,
and environment evidence. This was rebuilt from released source, not downloaded
from GHCR; the release Dockerfile resolves dependencies during build. The
recorded environment is authoritative for this capture, not a claim that the
original published image used the same dependency patch versions.

Run inside Docker:

```sh
make wire-check
make wire-capture
docker compose run --rm crystalium python -m evals.wire.compare \
  evals/wire/baselines/golden-wire-v2.4.0.json evals/wire/results/candidate.json
```

The bare comparison currently exits **1** on two intentional guidance strings
shipped by [PR #79](https://github.com/Rynaro/crystalium/pull/79) / #72. The exact
full diff and rationale are in `reviewed-deltas/main-after-v2.4.0.json` and remain
visible in review. No descriptions are masked. To verify that precise delta:

```sh
docker compose run --rm crystalium python -m evals.wire.compare \
  evals/wire/baselines/golden-wire-v2.4.0.json evals/wire/results/candidate.json \
  --reviewed-delta evals/wire/reviewed-deltas/main-after-v2.4.0.json
```

An additional change, a missing change, an invalid raw capture, or a changed
tool/schema/error fails. `wire-check` runs null/type redchecks plus independent
fresh-store live captures, comparing candidate to the exact reviewed delta.
These tests also run in the ordinary container CI suite, in both mounted and
baked layouts. Results go to ignored `results/`; the baseline is never overwritten.

## Previous release versus candidate

For every release, explicitly select the most recent **published predecessor**
tag, verify its commit against the GitHub release, and build from isolated source:

```sh
git fetch origin --tags
git rev-parse v2.4.0^{commit}
make wire-build-release WIRE_RELEASE=v2.4.0
make wire-capture-release WIRE_RELEASE=v2.4.0
make wire-capture WIRE_OUTPUT=candidate.json
docker compose run --rm crystalium python -m evals.wire.compare \
  evals/wire/results/v2.4.0.json evals/wire/results/candidate.json
```

`wire-build-release` refuses an existing worktree directory. For a new attempt,
remove only that clean temporary worktree with `git worktree remove
.wire-build/v2.4.0` first. Baseline and candidate should use the same interpreter,
MCP dependency version, transport, and offline model setting; inspect their
`.environment.json` sidecars before interpreting a difference. Pin or reconcile
toolchain differences explicitly rather than normalizing away their effects.

Release owner reviews every difference against the changelog/specification.
Intentional differences require an exact diff artifact with rationale in the
PR; `--reviewed-delta` accepts only that complete diff, with no wildcard paths.
Never automatically generate and accept a reviewed delta in CI. After publishing
a new release, add its raw capture and provenance in a reviewed maintenance PR,
retain older captures, and advance the test's baseline and reviewed delta together.
Do not compare every future release forever against v2.4.0.

## Probe and exclusions

The probe retains the six-exchange historical oracle: initialize (including
notifications/initialized), tools/list, successful empty recall, successful
episodic commit, missing-scope schema violation, and unknown-tool error. It uses
the post-v2 unprefixed tool names. Every capture starts a new subprocess with a
fresh temporary data directory whose basename is `golden-wire`; this fixes the
project-derived instruction/example strings. It never accesses the shared named
volume. Server stderr is retained for errors, each response has a timeout, and
the subprocess is closed or killed before its store is deleted.

Both raw captures pass typed/nonempty guards **before any masking**. The only
excluded values are `initialize.result.serverInfo.version` and the top-level
successful commit payload's `id`, `content_ref`, `created_at`, `committed_at`, and
`timestamp` when present and nonnull. No nested or similarly named fields are
excluded. Required id and content_ref cannot be absent/null/empty. Content_ref
must be a 64-character lowercase hex SHA-256 digest; capture also resolves it to
the actual stored blob and verifies its digest before deleting the fresh store.
Comparison of a saved capture cannot independently re-resolve its deleted blob.

UUIDs and record timestamps are fresh per run. Content_ref addresses a serialized
record containing this volatility, so even identical inputs can produce different
pointers. A valid server version must remain a nonblank string despite being an
expected release difference. All other bytes/values, including tool order/names,
descriptions, schemas, capabilities, error text and isError, remain compared.

## Historical evidence and ownership migration

`historical/golden-wire-v1.11.0.json` is an exact byte copy from
`Rynaro/eidolons/.spectra/changes/archive/2026-08-05-crystalium-mcp-sdk-2x-39/`;
SHA-256 `52ee03efdc8bfce02a1eec0b364033ca96bb28ad907e0e054ebc1606d195b983`.
Its filename records the historical label, but its actual serverInfo.version is
**1.4.0** because the old mounted source used stale installed image metadata.
Do not relabel it or use it as proof of released 1.11.0 package metadata.

The archived vulnerable comparator is deliberately not copied as an executable
entry point. Its provenance remains at the
[nexus archive](https://github.com/Rynaro/eidolons/tree/main/.spectra/changes/archive/2026-08-05-crystalium-mcp-sdk-2x-39).
Nexus owners maintain that immutable archive. CRYSTALIUM release owners maintain
this active verifier, baselines, tests and reviewed deltas. Future nexus release
plans must point here; no write to an ignored installed archive is required.
