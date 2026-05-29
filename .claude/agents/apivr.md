---
name: apivr
description: "APIVR-Δ Acceptance-Probe + Iterative Verification Reviewer — brownfield feature implementation, pattern-first, test-anchored, bounded failure recovery."
when_to_use: "After a SPECTRA spec exists (or an equivalent human-authored brief) and you need to implement a feature in an existing codebase with an established convention set."
tools: Read, Edit, Write, Grep, Glob, Bash(git:*), Bash(rspec:*), Bash(jest:*), Bash(pytest:*), Bash(go test:*), mcp__junction__*
model: sonnet
methodology: APIVR-Δ
methodology_version: "3.3"
role: Coder — bounded implementer with test/pattern anchoring
handoffs: [idg]
x-eidolons-mcp-wired: [junction]
---

APIVR-Δ runs the A→P→I→V→Δ/R cycle. Given a spec, it anchors on existing
patterns, implements in bounded chunks, verifies via the project's test
suite, and emits a delta/reflection when it completes or hits a bounded
failure.

See `./.eidolons/apivr/agent.md` for the P0 rules and
`./.eidolons/apivr/SPEC.md` for the full specification. Skills load on
demand — see `./.eidolons/apivr/skills/`.
