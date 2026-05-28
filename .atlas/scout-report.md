# ATLAS Scout Report — CRYSTALIUM v0.1.0 Bootstrap
**Phase:** T (Triage → full ATLАС run)
**Date:** 2026-05-28
**Methodology:** ATLAS v1.8.0 (A→T→L→A→S)
**Confidence key:** high = direct code evidence; medium = strong inference from schema/spec; low = inferred from pattern

---

## FINDING-001 — Keystone chokepoint pattern (atlas-aci)
**Confidence:** high

**File:** `atlas-aci/mcp-server/src/atlas_aci/enforcement.py`

The `Enforcement` class is the single chokepoint. Every `tools/call` invocation in `server.py` runs two mandatory pre-checks before any tool implementation runs:

```
enforcement.assert_read_only(name)   # line 81-88
enforcement.assert_rate_limit()      # line 100-114
```

Then each tool impl calls `enforcement.assert_path_in_repo(p)` before any I/O — e.g. `view_file`, `list_dir`, `search_text` each receive the `enforcement` object and call it inline.

**Dispatcher wiring** (`server.py:168`):
```python
@server.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    enforcement.assert_read_only(name)   # pre-check 1
    enforcement.assert_rate_limit()      # pre-check 2
    # ... tool dispatch by name ...
```
All tool branches pass `enforcement` into the tool function; the tool function calls `enforcement.assert_path_in_repo(p)` (`config.py:54-60`).

**Pre-check inventory:**
1. **Read-only guard** (`enforcement.py:81`): rejects any tool name not in the `READ_ONLY_TOOLS` frozenset; raises `ToolError("FORBIDDEN", ...)`.
2. **Rate limit** (`enforcement.py:100`): sliding-window counter (60s window, deque), raises `ToolError("TIMEOUT", ...)` on breach. Configurable via `Config.max_calls_per_minute` (default 200).
3. **Path-traversal guard** (`enforcement.py:90`; `config.py:54`): `Config.is_in_repo()` resolves symlinks via `Path.resolve()`, then calls `resolved.relative_to(self.repo)`; raises `ToolError("FORBIDDEN", ...)` on escape.
4. **Telemetry tap** (`enforcement.py:141`): `enforcement.record()` called at every tool exit point, emitting a `ToolCallRecord` to in-memory list and via `structlog` JSONL.
5. **Output-size bounds** (`enforcement.py:118-138`): `cap_lines`, `cap_matches`, `cap_entries`, `cap_bytes` — called by each tool impl; overflow flag propagated to telemetry record.

**MCP wiring:** `server.py` constructs `Server("atlas-aci")`, registers `@server.list_tools()` and `@server.call_tool()` decorators, then calls `stdio_server()` (`server.py:216`). The Python MCP SDK handles JSON-RPC framing; all enforcement runs inside the `_call_tool` async handler before any tool dispatch.

**CRYSTALIUM mirror:** `enforcement.py` in crystalium must replicate this shape: one `Enforcement` class, same three guards (read-only → replace with write-tier validation, path-guard, rate-limit), same telemetry sink. The `assert_read_only` becomes `assert_tier_allowed(tool_name, layer, trust_tier)`. The `assert_path_in_repo` becomes `assert_no_path_escape(target_dir)`. Inject `Enforcement` into every MCP tool handler before store code runs.

---

## FINDING-002 — ECL envelope minimum-conformant shape
**Confidence:** high

**File:** `eidolons-ecl/schemas/envelope.v2.json` (ECL v2.0, current)
**File:** `eidolons-ecl/schemas/performative.v1.json`
**File:** `eidolons-ecl/conformance/lib/integrity.sh`

**Current ECL version:** 2.0 (`eidolons-ecl/ECL_VERSION`)

**Required fields** (`envelope.v2.json` top-level `required` array, line 7-19):
```
envelope_version   string  pattern ^(1\.[012]|2\.0)(\.d+)?$
message_id         string  uuid (UUIDv7 RECOMMENDED)
thread_id          string  uuid
parent_id          string|null  uuid or null (null only on first envelope of thread)
from               agentRef  {eidolon: slug, version: semver|"n/a"}
to                 agentRef  {eidolon: slug, version: semver|"n/a"}
performative       one of the closed 10-value enum (see below)
objective          string  1-240 chars
artifact           object  {kind, schema_version, path, sha256, size_bytes}
integrity          object  {method: "sha256"|"hmac-sha256", value: 64-char hex}
trace              object  {ts: RFC3339, host: string, model: string, tier: "standard"|"trance"}
```

**Closed 10-performative set** (`performative.v1.json`):
REQUEST, INFORM, PROPOSE, CRITIQUE, DECIDE, DELEGATE, ACKNOWLEDGE, ESCALATE, RESUME, REFUSE

**SHA-256 integrity helper** (`conformance/lib/integrity.sh:10-20`):
- Uses `shasum -a 256` (macOS/BSD) with `sha256sum` (Linux) fallback.
- `integrity.value` = lowercase hex digest of the payload file bytes.
- `artifact.sha256` = same digest (redundant field; both are required).
- HMAC-SHA-256 is alternative method (requires `openssl`; key via `ECL_HMAC_KEY` env var).
- `artifact.size_bytes` = byte count via `wc -c` (SHOULD match, not MUST-fail).

**CRYSTALIUM MCP response conformance:** When CRYSTALIUM emits an ECL envelope sidecar (opt-in per ECL v1.0; file named `*.envelope.json`), it must carry all 11 required fields. The `from.eidolon` slug will be `crystalium`, version from its own release tag. For `integrity.method = "sha256"`, the Python implementation must call `hashlib.sha256(payload_bytes).hexdigest()` — no external binary needed.

**Optional fields of interest:** `edge_origin` (roster/composition/implicit), `context_delta`, `constraints.trust_level`, `ise.assertion_grade` (v2.0 ISE block — optional). CRYSTALIUM should use `edge_origin: "implicit"` until a formal contracts/ entry exists.

---

## FINDING-003 — EIIS v1.4 canonical install-target inventory whitelist
**Confidence:** high

**File:** `eidolons-eiis/spec/eiis-1.4.md` §1.9
**Current EIIS version:** 1.4 (`eidolons-eiis/EIIS_VERSION`)

**Install-target whitelist** (§1.9.1 table — only these paths allowed under `<target>/`):

| Path | Required? |
|---|---|
| `<target>/agent.md` | MUST (role: agent-profile) |
| `<target>/SPEC.md` | MUST (role: spec) |
| `<target>/install.manifest.json` | MUST (role: manifest) |
| `<target>/ECL_VERSION` | MUST if source repo has ECL_VERSION |
| `<target>/skills/<skill>.md` | MAY |
| `<target>/templates/<artifact>.md` | MAY |
| `<target>/schemas/install.manifest.v1.json` | SHOULD |
| `<target>/schemas/<aux>.json` | MAY |

**Explicitly forbidden in the install target** (§1.9.3):
- Legacy spec filenames: `CRYSTALIUM.md`, any eidolon-slug-named `.md`
- Source-repo files copied into target: `AGENTS.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `DESIGN-RATIONALE.md`
- Root-level `SKILL.md`
- Subdir skill layout: `skills/<phase>/SKILL.md`
- Directories not in whitelist: `hosts/`, `evals/`, `research/`, `tools/`, `commands/`

**Source-repo required files** (§1.1, unchanged):
`agent.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `install.sh`, `EIIS_VERSION`

**Source-repo MAY contain:** `skills/<skill>.md`, `templates/`, `evals/`, `LICENSE`, `ECL_VERSION`

**v1.4 new obligations** (MUST-fail for `EIIS_VERSION >= 1.4`):
- §1.8.6: `agent.md` must appear in `files_written[]` with `role: "agent-profile"` (exactly one).
- §3.7.1: If source declares `ECL_VERSION`, installer MUST copy it to `<target>/ECL_VERSION` verbatim.
- §4.2.3: claude-code dispatch file MUST reference both `agent.md` AND `SPEC.md`.
- §6.X: Cleanup sweep after install removes any non-whitelisted files.
- §6.Y: Every `skills/<skill>.md` reference in `agent.md` must resolve to a `files_written[]` entry.

**CRYSTALIUM implication:** Design the source repo layout now with `agent.md`, `SPEC.md`, `install.sh`, `EIIS_VERSION = 1.4`, `ECL_VERSION`. The install target at `./.eidolons/crystalium/` must carry only the whitelisted set. Do not copy `DESIGN-RATIONALE.md`, `MISSION.md`, or any docker/stack files into the target. The `cleanup_inventory_sweep` reference implementation in Appendix A of the spec is bash 3.2 compatible and directly reusable.

---

## FINDING-004 — Junction MCP wiring pattern
**Confidence:** high

**Files:**
- `Junction/cmd/junction/mcp.go` — entry: `mcpServeCmd`
- `Junction/internal/mcp/server.go` — `Server` struct, `Serve()`, `SendRequest()`
- `Junction/internal/mcp/tools.go` — `Registry`, `HandlerFunc`, four tools

**Transport:** Pure stdio (stdin/stdout), JSON-RPC 2.0, MCP 2025-03-26. No HTTP transport in the Go binary; Claude Code launches it as a subprocess via `.mcp.json`. `Serve()` reads lines via `bufio.Scanner` (4 MiB buffer), dispatches, writes JSON responses.

**Tool registry pattern** (`tools.go:28-89`): `Registry` holds a `[]ToolDef` (definitions for `tools/list`) and a `map[string]HandlerFunc` (handlers for `tools/call`). `NewRegistryDefaultWithServer()` loads contracts from the embedded FS, constructs four tools: `harness.plan_from_prompt`, `harness.run`, `harness.verify`, `harness.inject`. On-demand tool loading is not implemented in the registry itself — all tools are registered at server start.

**"On-demand" pattern in atlas-aci:** The Python SDK (`server.py:163`) uses `@server.list_tools()` / `@server.call_tool()` decorators; the tool manifest is static but the MISSION.md references "on-demand loading (Anthropic Code execution with MCP pattern)" — this refers to the host LLM loading tool descriptions on demand from the manifest, not dynamic server-side registration.

**Long-running / async dispatch** (Dream-relevant, `dispatch/container.go:152`):
- `ContainerExecutor.Execute(ctx, req)` runs a two-phase orchestration: `invoke(assemble)` → `ReasoningStep` seam → `invoke(package)`. Each phase uses `exec.CommandContext(ctx, ...)` which inherits cancellation from the caller's `context.Context`.
- Junction does NOT use a background worker/scheduler for long-running steps. All execution is synchronous within the MCP request context — the client blocks until the plan completes.
- The `SendRequest` (`server.go:115`) call blocks with a `select` on a channel, supporting per-request cancellation via ctx.

**Dream worker implication:** Junction's pattern is synchronous within a request. For CRYSTALIUM's Dream (async consolidation), the correct analogue is NOT Junction's dispatch loop — Dream must run outside the MCP request context entirely, using `apscheduler` (or `arq`) triggered by idle/event-count/cron. The MCP request handler for `crystalium.commit` should enqueue a consolidation hint to the Dream queue and return immediately; Dream drains the queue asynchronously. Junction's `ChainExecutor` is useful only as a reference for the `skill_invoke` sandbox path (subprocess + timeout via context).

**`skill_invoke` sandbox pattern** (from `atlas-aci` `test_dry_run`): `server.py:119-134` shows the test_dry_run tool: 30s timeout, output capped at 8 KiB. CRYSTALIUM's `skill_invoke` should mirror this: `subprocess.run(..., timeout=config.skill_timeout_s, ...)` wrapped in the chokepoint's `assert_tier_allowed` + path guard.

---

## FINDING-005 — Nexus roster + cortex touchpoints for future crystalium publish
**Confidence:** high

**File:** `eidolons/roster/index.yaml` (registry_version: "1.0")
**File:** `eidolons/methodology/cortex/handoff-graph.md`

**Full required roster entry shape** (derived from atlas entry at lines 15-188):

```yaml
- name: crystalium
  display_name: CRYSTALIUM
  capability_class: memory-harness      # new capability_class; no existing Eidolon uses this
  status: shipped                       # or in_construction (skips EIIS conformance check)
  methodology:
    name: CRYSTALIUM
    version: "0.1"
    cycle: "commit→gate→store→recall"   # or a named cycle
    summary: "Portable memory harness. Gated write, hybrid retrieval, Dream consolidation."
  source:
    type: github
    repo: Rynaro/crystalium             # canonical casing must match GitHub
    default_ref: main
  versions:
    latest: "0.1.0"
    pins:
      stable: "0.1.0"
    releases:
      0.1.0:
        tag: v0.1.0
        commit: <sha>
        tree: <sha>
        archive_sha256: <sha>           # MUST be from Release workflow, not hand-computed
        manifest_sha256:
        provenance:
          github_attestation: true
          workflow: .github/workflows/release.yml
  install:
    target_default: "./.eidolons/crystalium"
    standalone: true
  handoffs:
    upstream: []                        # crystalium is a lateral service, not in a linear chain
    downstream: []
    lateral: [atlas, spectra, apivr, idg, forge, vigil]
  comm:
    envelope_version: "2.0"
  working_set_tokens:
    entry: <agent.md token count>
    target: 3500
  security:
    reads_repo: false
    reads_network: false
    writes_repo: false
    persists: ["~/.crystalium/<project>/"]
  references: []
```

**registry_version:** Currently `"1.0"`. No bump needed for a new entry — bumping is only for breaking changes to the file's shape.

**eiis_required:** Currently `"1.4"` at the roster root (line 11). CRYSTALIUM's install.sh must conform to EIIS 1.4.

**integrity.enforcement:** `strict` (line 13). The `archive_sha256` in the roster entry MUST come from the GitHub release workflow auto-tarball; hand-computed values will differ. Use the `Release CRYSTALIUM` + `Roster Intake` workflow_dispatch pattern used for other Eidolons.

**Cortex touchpoint** (`methodology/cortex/handoff-graph.md`): CRYSTALIUM is a lateral service rather than a linear pipeline stage. When added, the cortex handoff-graph should declare it as a lateral edge from any Eidolon (similar to VIGIL's bidirectional lateral). No new `edge_origin` class needed — `"implicit"` or `"roster"` once the entry exists.

**Capability class gap:** The existing classes are: `scout`, `planner`, `coder`, `scriber`, `reasoner`, `debugger`. `memory-harness` is new — no collision, but FORGE will need to confirm this is acceptable or propose an alternative (e.g. `infrastructure`).

---

## GAP-001 — Streamable-HTTP transport in crystalium
**Confidence:** low
MISSION.md specifies both stdio and Streamable-HTTP transports. Junction implements stdio only. atlas-aci also uses stdio only (`run_stdio` in `server.py`). No reference implementation for Streamable-HTTP exists in the workspace. FORGE will need to derive the Streamable-HTTP wiring from the MCP Python SDK docs directly. The `mcp` Python SDK package exposes a `streamable_http_server` context manager (sibling to `stdio_server`) — check `mcp` PyPI package for exact API.

## GAP-002 — Dream worker idle-trigger integration with MCP lifecycle
**Confidence:** medium
No reference in Junction or atlas-aci for an idle-trigger or end-of-session hook. The MCP protocol `notifications/initialized` notification (`server.go:309`) is available as a session-start signal; there is no session-end notification in MCP 2025-03-26. FORGE must decide: (a) poll-based idle detection in the Dream scheduler, or (b) a dedicated `crystalium.session_end` MCP tool that the host calls before closing. Both are compatible with the chokepoint pattern.

## GAP-003 — ECL opt-in vs first-class requirement for crystalium v0.1
**Confidence:** medium
ECL v1.0 is opt-in. MISSION.md does not explicitly require ECL envelope emission for every MCP response. The EIIS v1.4 `ECL_VERSION` target copy (§3.7.1) is required only if the source repo declares `ECL_VERSION`. FORGE must decide: ship `ECL_VERSION = 2.0` in the source repo (triggering §3.7.1 obligation) or defer ECL emission to a post-v0.1 milestone. If deferred, the v0.1 install.sh can omit `ECL_VERSION` entirely without violating EIIS.

## GAP-004 — `capability_class` value for crystalium in the roster
**Confidence:** medium
No existing `capability_class` covers an infrastructure/memory service. Options: `memory-harness`, `infrastructure`, or a new value. The roster schema (`schemas/roster-entry.schema.json` — not read in this scout) may constrain the enum. FORGE should check `eidolons/schemas/roster-entry.schema.json` before finalizing; if the schema is an open string the value is free-form.

## GAP-005 — `skill_invoke` sandbox OS isolation boundary
**Confidence:** medium
MISSION.md states: "CRYSTALIUM enforces what it can mechanically; cannot enforce OS isolation by itself." atlas-aci's `test_dry_run` carries the same caveat ("SANDBOXING IS THE OPERATOR'S RESPONSIBILITY", `server.py:124`). No workspace repo provides a container-sandbox wrapper for subprocess execution. FORGE must define the `skill_invoke` sandbox contract: whether it shells out to `docker run` (like Junction's ContainerExecutor), uses a chroot/seccomp wrapper, or delegates entirely to operator. v0.1 can mirror atlas-aci's approach (subprocess + timeout + output cap) with the operator warning.

---

## Synthesis

The five DECISION_TARGET questions are fully answered with high confidence from direct source evidence. The primary structural decisions for CRYSTALIUM v0.1.0:

1. **enforcement.py** directly mirrors `atlas-aci/mcp-server/src/atlas_aci/enforcement.py` with three adaptations: replace `assert_read_only` with `assert_tier_allowed(tool, layer, trust_tier)`; extend path guard to cover the `~/.crystalium/<project>/` blob store; add write-tier-specific rate limits.

2. **ECL conformance** requires `hashlib.sha256` in Python (no shell dep) and 11 required envelope fields. Opt-in for v0.1; defer `ECL_VERSION` declaration until post-v0.1 unless FORGE decides otherwise (GAP-003).

3. **EIIS v1.4 install layout** is well-specified. crystalium's install target at `./.eidolons/crystalium/` must contain exactly: `agent.md`, `SPEC.md`, `install.manifest.json`, optionally `ECL_VERSION` and `skills/*.md`. The Appendix A sweep implementation is copy-paste-ready.

4. **Junction MCP wiring** provides the stdio server architecture (JSON-RPC 2.0, `Registry` + `HandlerFunc`). For CRYSTALIUM: use the Python MCP SDK's decorator pattern (atlas-aci style), not the Go hand-rolled server. Dream worker is NOT a Junction-style dispatch loop — it is an `apscheduler`/`arq` background task outside MCP lifecycle.

5. **Roster entry** is template-ready pending `capability_class` resolution (GAP-004) and the `Rynaro/crystalium` GitHub repo creation. The `archive_sha256` must come from the CI release workflow, never hand-computed.

---

## HANDOFF:

**Consumer:** FORGE (implementation agent)
**Handoff performative:** DELEGATE

**FORGE focus areas (ordered by blocking risk):**
1. **DECISION-1 / GAP-005:** Design the `enforcement.py` write-tier guard and `skill_invoke` sandbox contract. The chokepoint shape is fully specified; the tier-mapping logic (which layer allows which tool at which trust tier) is the open design question.
2. **DECISION-4 / GAP-001:** Confirm MCP Python SDK `streamable_http_server` API for the Streamable-HTTP transport; the stdio path is fully specified by atlas-aci.
3. **DECISION-4 / GAP-002:** Decide Dream idle-trigger mechanism (poll vs `session_end` tool).
4. **DECISION-5 / GAP-003:** Decide ECL v0.1 opt-in scope — whether to declare `ECL_VERSION = 2.0` in source repo now.
5. **DECISION-5 / GAP-004:** Check `eidolons/schemas/roster-entry.schema.json` for `capability_class` enum constraints before committing to `memory-harness`.

**Artefacts to pass forward:**
- `/Users/henrique/workspace/oss/agents/crystalium/.atlas/scout-report.md` (this file)
- Source paths (read-only, do not modify): `atlas-aci/mcp-server/src/atlas_aci/{enforcement.py,server.py,config.py}`, `eidolons-ecl/schemas/envelope.v2.json`, `eidolons-eiis/spec/eiis-1.4.md`, `Junction/internal/mcp/{server.go,tools.go}`, `Junction/cmd/junction/mcp.go`
