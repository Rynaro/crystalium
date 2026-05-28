# Skill: verifier-sandbox — D5 skill_invoke sandbox contract

Load this skill when writing or debugging a procedural skill verifier.

## When to load

- A skill verifier is timing out or producing too much output.
- `SkillPathEscape` error surfacing.
- Designing a new procedural skill verifier script.

## D5 sandbox bounds (FORGE D5, G3)

| Bound | Value |
|---|---|
| Timeout | 30 seconds (hard; `subprocess.run(timeout=30)`) |
| Output cap | 8192 bytes stdout+stderr combined |
| Workdir prefix | `/sandbox/<skill_id>` |
| Path resolution | `Path.resolve(strict=True)` then `relative_to(/sandbox/<skill_id>)` |

## Enforcement order (G3, spec.yaml §tool_surface)

1. `assert_rate_limit()`
2. `assert_no_path_escape(workdir, expected=/sandbox/<skill_id>)`
3. Log `OPERATOR WARNING: container IS the sandbox boundary`
4. `subprocess.run(cmd, timeout=timeout_s, cwd=workdir)`
5. `cap_output(output_cap_bytes)` — truncate + set `overflow_flag=True`
6. `emit_ecl_envelope_sidecar()`
7. `record()` telemetry

## Exit codes

- Exit 0 + no overflow: verifier passes → skill promoted to `admitted`.
- Exit non-zero OR overflow: verifier fails → skill stays `candidate`.

## Writing a safe verifier

```bash
#!/usr/bin/env bash
set -euo pipefail
# Keep output minimal — 8 KiB cap is strict
echo "VERIFIER_PASS"
exit 0
```

Avoid: generating large diffs, running full test suites, network calls.
The container IS the OS-level sandbox; the workdir prefix is a
path-traversal guard, NOT a seccomp/namespaced sandbox.

## SkillPathEscape

Raised when `workdir` does not resolve under `/sandbox/<skill_id>`.
String-prefix check (no filesystem resolve for non-existent paths):
  `workdir_str.startswith(f"/sandbox/{skill_id}")`.
