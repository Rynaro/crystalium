# CRYSTALIUM

Portable memory harness for the Eidolons. Stores, gates, retrieves, consolidates, and forgets memory. Does not reason, plan, or write code.

## Status

v0.1.0 / Pre-roster / Standalone. EIIS v1.4 conformant. Container-first (all Python toolchain runs inside Docker; host runs only `docker compose`, `git`, `make`). Will publish to the Eidolons roster post-v0.1 once the canary suite is stable and operators have battle-tested it.

---

## Architecture (10-second version)

```
HOST (operator only: docker, git, make)
    │
    ├─→ docker compose run --rm crystalium
    │
    ├─→ MCP stdio server (JSON-RPC 2.0)
    │
    ├─→ enforcement.py (CHOKEPOINT)
    │   • assert_tier_allowed (tier × layer × op matrix)
    │   • assert_no_path_escape (symlink + traversal guard)
    │   • assert_rate_limit (200 calls/min sliding window)
    │   • record telemetry (structlog JSONL + OpenTelemetry)
    │
    ├─→ Four memory layers
    │   • Episodic (pointers, quarantine-by-default)
    │   • Semantic (gated promotion, indefinite)
    │   • Procedural (verifier-gated, skills)
    │   • Execution (ephemeral, TTL)
    │
    ├─→ Aetheryte (hybrid recall)
    │   • BM25 (SQLite + FTS5, sparse)
    │   • Vector (LanceDB, dense)
    │   • Graph (KuzuDB, structured facts)
    │
    └─→ Dream worker (async, idle-triggered)
        • Orient → Gather → Consolidate → Prune
        • Runs outside MCP request context
        • Proposes (never force-writes)

Storage:
  • Indices + metadata in SQLite/LanceDB/KuzuDB (queryable, cacheable)
  • Episodic payloads on filesystem (content-addressed, immutable)
  • All under ~/.crystalium/<project>/ (portable, local-first)

Every tool result emits ECL v2.0 envelope sidecar (integrity via SHA-256).
```

---

## Quick start (container-first)

```bash
git clone https://github.com/Rynaro/crystalium
cd crystalium

# Build and smoke-test
docker compose build
docker compose run --rm crystalium make test

# Start the MCP server (host connects via stdio)
docker compose up crystalium
```

The server listens on stdin/stdout. Wire it to Claude Code, Cursor, or your host LLM via `.mcp.json`:

```json
{
  "mcpServers": {
    "crystalium": {
      "command": "docker",
      "args": ["compose", "run", "--rm", "crystalium", "python", "-m", "crystalium", "serve"]
    }
  }
}
```

**No host `pip`, `uv`, `python`, or `pytest` invocations.** All dev commands run inside the container via `docker compose run --rm crystalium <cmd>` or `make` targets.

---

## What it gives the Eidolons

- **Memory across sessions.** Episodic recall of past missions, PRs, and outcomes.
- **Consolidated facts.** Semantic layer curates API signatures, project conventions, design decisions.
- **Verified skills.** Procedural layer stores tested refactoring recipes and proven patterns; verifier gates admission.
- **Bounded working set.** Composer enforces ≤3,500-token budget with deterministic eviction, freeing the agent to focus on reasoning.
- **Trust propagation.** Multi-agent consolidation takes the minimum trust tier of inputs, blocking poison laundering.

---

## What it doesn't do

- **Reason.** No inference loop, no planning, no goal-seeking.
- **Provide raw autonomy.** Constrained interfaces: agents call `recall`, Dream proposes (never force-writes).
- **Require external services.** Fully self-hosted (SQLite, LanceDB, KuzuDB embedded); optional Ollama for redaction.

---

## Tool surface

| Tool | Purpose | Gated by |
|---|---|---|
| `recall(scope, query, k, layers)` | Hybrid BM25+vector+graph retrieval with reranking | Rate limit only |
| `commit(layer, payload, provenance)` | Write with tier enforcement + bi-temporal tracking | Tier matrix (G1–G4) |
| `update(id, patch, reason)` | Field edits with invalidate-old; never hard-delete | Tier matrix (G1, G4) |
| `skill_invoke(name, args)` | Sandbox verifier for procedural admission | Sandbox contract (G3) |
| `plan_checkpoint(state)` | Execution layer checkpoint (TTL-bound) | Tier matrix (G1) |
| `plan_replan(diff)` | Execution layer replan diff | Tier matrix (G1) |
| `session_end(reason)` | Enqueue Dream immediately (else idle-polled every 60s) | Rate limit only |

All results emit ECL v2.0 envelope sidecars with 11 required fields and SHA-256 integrity.

---

## Configuration

The `crystalium.yaml` file in the container sets runtime defaults. Key tunable knobs:

- `transport: stdio` (HTTP stub raises `NotImplementedError("v0.2")`)
- `idle_threshold_s: 300` (idle detection window)
- `min_dream_gap_s: 1800` (minimum time between Dream runs)
- `k_corroboration: 3` (independent T1+ witnesses for Semantic promotion)
- `human_confirm_default_window_days: 30` (post-install default-ON grace period)
- `skill_invoke.timeout_s: 30` (verifier subprocess timeout)
- `skill_invoke.output_cap_bytes: 8192` (verifier output cap)
- Working-set slot caps: executive (300), procedural (600), semantic (800), episodic (800), execution (1000), buffer (300)
- Importance weights: `[0.25, 0.30, 0.25, 0.20]` (access_frequency, recency, outcome_success, novelty)

See `.spectra/crystalium-v0.1.0-spec.md` §8 (config defaults) for the full list.

---

## Roles

- **Operator:** installs CRYSTALIUM, configures `crystalium.yaml`, runs `docker compose`, reviews human-confirm promotions via `crystalium promote list/review`.
- **Agent (T1+):** calls `recall`, `commit`, `skill_invoke` via MCP. Eidolons hand off memory to each other via ECL envelopes.
- **Environment/tool (T3):** ingests environment facts into Episodic-quarantine; promotion requires operator or T1 review.

---

## Design principles

1. **One chokepoint.** Every write funnels through `enforcement.py` before any store mutation. No distributed guards.
2. **Constrained interfaces.** Pull-based (`recall`), not push-based; Dream proposes, never force-writes.
3. **Pointer-indexed.** Indices hold metadata; payloads live on cheap blob tier (content-addressed, immutable).
4. **Bounded working set.** Composer enforces ≤3,500 tokens with deterministic eviction (importance-first).
5. **Trust tiers propagate.** Consolidated fact takes MIN trust tier of inputs; blocks multi-agent poisoning.
6. **Local-first, container-first.** Fully self-hosted; all toolchain inside Docker; host runs only docker/git/make.

---

## Research foundation

- **Dual-process memory:** Teyler & DiScenna 1986; Teyler & Rudy 2007. Index → pointer → content is hippocampal pattern.
- **Consolidation:** McClelland/McNaughton/O'Reilly 1995; Tononi & Cirelli 2014. Sleep-like offline consolidation is cheaper than online learning.
- **Bounded working set:** Baddeley & Hitch 1974. Slot allocation with deterministic eviction.
- **Write-gating:** O'Reilly & Frank 2006. Mechanical enforcement of access control.
- **Bi-temporal edits:** Zep/Graphiti; arXiv:2501.13956. Invalidate-old, write-new, never hard-delete.
- **Verifier-gated skills:** Voyager (arXiv:2305.16291); ProcMEM (arXiv:2602.01869); SkillGen (arXiv:2605.10999).
- **Poisoning defense:** LTM Security Survey (arXiv:2604.16548); OWASP ASI06.
- **Constrained interfaces:** SWE-agent (arXiv:2405.15793); Agentless.

See `DESIGN-RATIONALE.md` for full citations and `[UNVERIFIED]` markers on claims not verifiable from the workspace.

---

## Repository hierarchy

This repo is a standalone Eidolon (like ATLAS, SPECTRA, APIVR-Δ). It depends on:

- **Rynaro/eidolons-eiis** — install contract (EIIS v1.4 conformance).
- **Rynaro/eidolons-ecl** — runtime communication contract (ECL v2.0 envelopes).
- **Rynaro/atlas-aci** — reference for enforcement.py chokepoint pattern.
- **Rynaro/Junction** — harness used to orchestrate the build.
- **Rynaro/eidolons** — nexus (will publish roster entry post-v0.1).

---

## Hosts

Tested against:
- **Claude Code** — via `docker compose` command in `.mcp.json`
- **Cursor** — via `.cursor/mcp.json` wiring
- **Copilot** — via GitHub Copilot custom agents
- **opencode** — via `hosts/opencode.md` integration

See `hosts/*.md` per build wave W6 for per-host setup guides.

---

## License

Apache-2.0. See LICENSE file.

---

## Development

All commands run inside the container. The `Makefile` is the host-visible test wrapper:

```bash
make test          # docker compose run --rm crystalium pytest
make lint          # docker compose run --rm crystalium ruff check
make schema        # docker compose run --rm crystalium validate schemas
```

See `AGENTS.md` for the full developer onboarding.

---

## References

- **Spec:** `.spectra/crystalium-v0.1.0-spec.md` (decision-ready, frozen for v0.1)
- **Methodology:** `CRYSTALIUM.md` (design principles, research anchors)
- **Design rationale:** `DESIGN-RATIONALE.md` (every D1–D10 decision traced to source)
- **Agent profile:** `agent.md` (always-loaded entry point, ≤1000 tokens)
- **EIIS conformance:** `install.sh` (idempotent, bash 3.2 safe)
- **ECL envelopes:** `.spectra/crystalium-v0.1.0-spec.md` §12 (11 required fields, SHA-256 integrity)
- **Canary suite:** `.spectra/crystalium-v0.1.0-spec.md` §13 (10 missions, memory-on/off A/B, ≥0.80 pass rate)
