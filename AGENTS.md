# AGENTS.md — Development Standard

This file defines the development standard for CRYSTALIUM. All work on this repo follows the open-standard pattern: build commands, test commands, lint, project structure, code style, and commit conventions are uniform across all Eidolons.

---

## Container-first (non-negotiable)

**All Python toolchain runs inside the docker compose service `crystalium`. Do NOT invoke `python`, `pip`, `uv`, `pytest`, or `node` directly on the host.**

- Host tools: `docker`, `git`, `make` only.
- All dev commands via `docker compose run --rm crystalium <cmd>` or `make` targets.
- Dependencies install during `docker build`, not on the host.
- Runtime artefacts (SQLite DBs, vector indices, blobs) live in a named volume `crystalium_data` mounted at `~/.crystalium/` inside the container.

---

## Build command

```bash
docker compose build
```

Rebuilds the `crystalium` service image. Python ≥3.11 with `uv` package manager. Dependencies installed via `pyproject.toml` (inside container, not on host).

---

## Test command

```bash
make test
```

Expands to:

```bash
docker compose run --rm crystalium pytest mcp-server/tests/ -v
```

Run the full pytest suite inside the container. Exit code 0 = all passing; non-zero = failure. All 8 gates (G1–G8) must pass before committing.

### Single test file

```bash
make test-file F=mcp-server/tests/test_enforcement.py
```

Expands to:

```bash
docker compose run --rm crystalium pytest mcp-server/tests/test_enforcement.py -v
```

### Single test by pattern

```bash
make test-file F=mcp-server/tests/test_enforcement.py P="test_g1"
```

Runs tests matching the pattern inside the file.

---

## Lint command

```bash
make lint
```

Expands to:

```bash
docker compose run --rm crystalium ruff check mcp-server/
docker compose run --rm crystalium ruff format --check mcp-server/
```

Ruff (fast linter + formatter) is the style enforcer. Configuration lives in `pyproject.toml` `[tool.ruff]` section. Fix lint errors with:

```bash
docker compose run --rm crystalium ruff format mcp-server/
```

---

## Schema validation

```bash
make schema
```

Validates all JSON Schema files against JSON Schema Draft 2020-12. Also validates `install.manifest.json` shape and Pydantic model round-trips.

---

## Project structure

```
crystalium/
├── agent.md                        # always-loaded entry point
├── SPEC.md                         # EIIS v1.4 install-target spec
├── MISSION.md                      # frozen bootstrap (not shipped)
├── DESIGN-RATIONALE.md             # D1–D10 decisions + citations
├── CRYSTALIUM.md                   # methodology + research
├── README.md                       # human-readable intro
├── AGENTS.md                       # this file (dev standard)
├── CLAUDE.md                       # Claude Code integration
├── CHANGELOG.md                    # Keep-a-Changelog format
├── LICENSE                         # Apache-2.0
├── EIIS_VERSION                    # "1.4"
├── ECL_VERSION                     # "2.0"
├── Dockerfile                      # Python ≥3.11 + uv
├── docker-compose.yml              # service: crystalium
├── docker-compose.dev.yml          # adds dev tools
├── Makefile                        # test, lint, schema, build
├── pyproject.toml                  # uv-managed dependencies
├── schemas/
│   ├── crystal.v1.json
│   ├── skill.v1.json
│   ├── recall-request.v1.json
│   ├── recall-result.v1.json
│   ├── commit-request.v1.json
│   ├── commit-result.v1.json
│   └── install.manifest.v1.json
├── mcp-server/
│   ├── pyproject.toml
│   └── src/crystalium/
│       ├── __init__.py             # __version__ = "0.1.0"
│       ├── __main__.py             # CLI entry
│       ├── server.py               # MCP server (mirrors atlas-aci)
│       ├── enforcement.py          # chokepoint
│       ├── config.py               # Pydantic models
│       ├── importance.py           # importance_score (frozen sig)
│       ├── composer.py             # working-set composer
│       ├── ecl_envelope.py         # envelope sidecar helper
│       ├── layers/
│       │   ├── episodic.py
│       │   ├── semantic.py
│       │   ├── procedural.py
│       │   └── execution.py
│       ├── aetheryte/
│       │   ├── recall.py
│       │   └── redact.py
│       ├── dream/
│       │   ├── scheduler.py
│       │   └── worker.py
│       ├── gate.py
│       ├── storage/
│       │   ├── sqlite.py
│       │   ├── lance.py
│       │   ├── kuzu.py
│       │   └── blob.py
│       └── telemetry.py
└── mcp-server/tests/
    ├── conftest.py
    ├── test_enforcement.py         # G1, G2, G4
    ├── test_trust_propagation.py   # G4
    ├── test_skill_invoke.py        # G3
    ├── test_promotion_gate.py      # G5
    ├── test_composer.py            # G6
    ├── test_ecl_conformance.py     # G7
    ├── test_dream_scheduler.py     # G8
    ├── test_schemas.py
    ├── test_storage_sqlite.py
    ├── test_storage_lance.py
    ├── test_storage_kuzu.py
    ├── test_storage_blob.py
    └── canary/
        └── test_canary_*.py        # 10 missions
```

---

## Code style (Python)

- **Language:** Python ≥3.11.
- **Package manager:** `uv`. Lock file: `uv.lock` (vendored, checked in).
- **Type hints:** mandatory on all function signatures (enforced by Ruff `typing-unused-all`).
- **Docstrings:** Google-style for modules, classes, and public functions. Summarize the chokepoint path for complex functions.
- **Imports:** absolute (no relative); group stdlib, third-party, local (isort order).
- **Naming:** snake_case for functions/variables; PascalCase for classes.
- **Line length:** 100 characters (Ruff default).
- **No F-strings with > 1 expr:** Use `.format()` or concatenation if logic is complex.

### Pydantic models

- All request/response types are Pydantic v2 models mirroring JSON Schemas.
- Config: `model_config = ConfigDict(extra="forbid")` (reject unknown fields).
- Validators: use `@field_validator` for custom logic.
- Never `json()` (deprecated in v2); use `model_dump()` + `json.dumps()`.

---

## Commit conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

- `feat`: new feature
- `fix`: bug fix
- `test`: test additions/changes
- `docs`: documentation
- `refactor`: code refactor without feature change
- `style`: formatting, linting (no functional change)
- `chore`: dependency bump, CI config, tooling

### Scopes

- `schemas`: JSON Schema files
- `enforcement`: `enforcement.py` + tier matrix
- `storage`: storage adapters (SQLite, LanceDB, KuzuDB, blob)
- `layers`: layer modules (episodic, semantic, procedural, execution)
- `aetheryte`: recall + redactor
- `dream`: scheduler + worker
- `composer`: working-set composer
- `server`: MCP server + CLI
- `ecl`: ECL envelope sidecar
- `test`: test suite (when not a specific scope)
- `install`: install.sh + EIIS conformance
- `ci`: CI workflows
- `docs`: README, DESIGN-RATIONALE, CHANGELOG

### Examples

```
feat(enforcement): add assert_tier_allowed with full matrix

Implement the tier × layer × operation matrix per FORGE D1.
- T3 cannot commit above Episodic
- T2 procedural commits land as candidate
- Only T0 can force_promote
- All three guards (tier, path, rate) run before any store write

Relates to G1, G2 gates.
```

```
test(composer): add test_g6_working_set_budget_invariant

Verify composer respects slot caps and deterministic eviction.
- Per-slot enforcement: executive ≤300, procedural ≤600, …
- Total ≤3,500 tokens
- Same inputs → same kept set, same order
```

---

## CI / GitHub Actions

CI workflows live in `.github/workflows/`:

- `test.yml`: runs `make test` inside container; all gates must pass.
- `lint.yml`: runs `make lint`; ruff must pass.
- `schema.yml`: runs `make schema`; JSON Schema validation.
- `eiis.yml`: runs EIIS v1.4 conformance checks (install-target whitelist, agent.md token cap, second-run idempotency).

All workflows use `docker compose` (no host `python` or `pip`).

---

## Release process (v0.2+)

v0.1.0 is standalone; roster publication is deferred. When v0.2 stabilizes:

1. Merge the working branch to `main`.
2. Tag the commit: `git tag v0.2.0`.
3. Push: `git push origin main --tags`.
4. GitHub Actions Release workflow creates the archive + attestation.
5. Update the parent nexus roster entry: `Rynaro/eidolons` PR to bump `versions.latest` and `versions.pins.stable`.

Until v0.2, no versioned releases are published; the repo is a development tree.

---

## Questions / debugging

- **"ModuleNotFoundError: No module named 'crystalium'"** — The container mount is wrong. Check `docker compose config | grep -A5 crystalium` for volume paths.
- **"pytest: command not found"** — You ran pytest on the host. Use `docker compose run --rm crystalium pytest` or `make test`.
- **"ruff: command not found"** — Same. Use `docker compose run --rm crystalium ruff` or `make lint`.
- **Tests pass locally but fail in CI** — CI may run with different Python patch version or environment. Check the CI log for `python --version`.

---

## Further reading

- **MISSION.md** — frozen P0 brief; immutable until v0.2.0.
- **DESIGN-RATIONALE.md** — decisions D1–D10 + research anchors.
- **SPEC.md** + `.spectra/crystalium-v0.1.0-spec.md` — full spec with gates + waves.
- **atlas-aci** (Rynaro/atlas-aci) — reference implementation of enforcement.py pattern.
- **EIIS v1.4** (Rynaro/eidolons-eiis) — install contract conformance.
- **ECL v2.0** (Rynaro/eidolons-ecl) — envelope wire format.
