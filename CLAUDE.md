<!-- eidolon:cortex start -->
## Eidolons Routing Cortex

**Default operating mode:** route all non-trivial work through the Eidolons pipeline — this is the default, not an opt-in. The orchestrator delegates to Eidolon roles via the cortex and does not implement, spec, or scout directly. Answer directly only when a prompt is trivial, conversational, or a single-fact lookup.

**Read:** `.eidolons/cortex/EIDOLONS.md` — always-loaded descriptor table + dispatch protocol. It tells you which Eidolon (or chain) handles the prompt, at what tier (`standard` is the default; `TRANCE` is gated, never default), and what hand-off contract to use.

**Deep tables** (load on demand): `.eidolons/cortex/trance-matrix.md`, `.eidolons/cortex/handoff-graph.md`, `.eidolons/cortex/validation-gates.md`.
<!-- eidolon:cortex end -->

<!-- eidolon:dispatch-pointer start -->
## Eidolons

This project uses [Eidolons](https://github.com/Rynaro/eidolons). The canonical agent dispatch table, methodology references, and per-Eidolon hand-off contracts live at [`./EIDOLONS.md`](./EIDOLONS.md). Read that file before any non-trivial prompt — this is the default operating mode, not an opt-in.
<!-- eidolon:dispatch-pointer end -->

## Crystalium memory guidance

Recall only for explicit need, prior failure, or ambiguity that prior records can
resolve. Request `k<=3` and prefer relevant verified fix-span records. Use
`assemble_context` for task context: explicit need or memory-relevant ambiguity
maps to `needs_memory=true`; a failed attempt maps to `prior_failure=true`.
Both default false. Explicit `recall` retains API default `k=10`.
Guidance is provisional pending representative [#74](https://github.com/Rynaro/crystalium/issues/74)
coding evaluation; the fixture report is synthetic calibration only.
See [migration](docs/memory-injection.md) and upstream
[eidolons#635](https://github.com/Rynaro/eidolons/issues/635).
