---
name: apivr-methodology
description: "Full APIVR-Δ cycle reference — A(nalyze) → P(lan) → I(mplement) → V(erify) → Δ(Delta)/R(eflect). Use when the task is a non-trivial feature implementation in a brownfield codebase and you need the complete methodology reference: complexity routing, evidence-grounded planning rules, test-anchoring requirements, and failure-escalation thresholds."
methodology: APIVR-Δ
methodology_version: "3.0"
---

# APIVR-Δ Methodology v3.0

Feature implementation through evidence-grounded planning, test-anchored development, and structured self-improvement in brownfield codebases.

---

## A — ANALYZE Phase

### Step 1: Memory Recall
Query `agents/memories/` for:
- Past tasks in same module or domain
- Known reusable assets
- Previous failure patterns in this area

Score matches by: path proximity → recency → outcome quality. Budget: ≤ 20 entries.

### Step 2: Repo Map Generation
Before reading any file in detail, generate a structural overview:

```
1. List directory tree for the target domain (2-3 levels deep)
2. Identify key files by convention:
   - Models/entities: app/models/DOMAIN/
   - Controllers/handlers: app/controllers/ or equivalent
   - Tests: spec/ or test/ mirroring source structure
   - Configuration: config/, .env patterns
3. For each key file, extract:
   - Public interface (method signatures, exported functions)
   - Dependencies (imports, includes, requires)
   - Test coverage existence (yes/no)
4. Rank by reference frequency: files imported by many others = high leverage
```

Output: Compressed structural summary (~50-100 lines). This is your navigation map.

### Step 3: Requirements Decomposition
State explicitly:
- **Goal**: What problem does this solve? One sentence.
- **Scope IN**: Files, modules, features being changed
- **Scope OUT**: What is explicitly NOT being changed (boundaries)
- **Acceptance criteria**: Observable, testable conditions for "done"

### Step 4: Asset Discovery (MANDATORY)

Search the codebase BEFORE planning. Discover what already exists.

| Asset Type | Search Pattern | Purpose |
|------------|---------------|---------|
| Domain models | `app/models/DOMAIN/` | Business logic, state machines |
| Repositories | `app/models/DOMAIN/repository.*` | Data access patterns |
| Services | `app/services/DOMAIN/` | Orchestration, business rules |
| View components | `app/components/DOMAIN/` | UI building blocks |
| Query objects | `app/models/DOMAIN/queries/` | Complex data retrieval |
| Workers/Jobs | `app/jobs/`, `app/workers/` | Async processing |
| Serializers | `app/serializers/` | API response shaping |
| Shared utilities | `lib/`, `app/lib/` | Cross-cutting helpers |
| Config/Constants | `config/`, `app/constants/` | Feature flags, settings |
| Test factories | `spec/factories/`, `test/factories/` | Test data patterns |

For EACH discovered asset, record:

| Field | Values |
|-------|--------|
| Location | file:line |
| Purpose | One-line description |
| Relevance | HIGH / MED / LOW |
| Quality | Has tests? Recent changes? Known issues? |
| Verdict | USE / EXTEND / WRAP / AVOID |
| Rationale | Why this verdict (one sentence) |

### Step 5: Collision Mapping
Identify risk zones:
- Files to **modify** (existing, may break things)
- Files to **create** (new, may collide with in-flight work)
- **High-risk zones**: Low test coverage, heavily imported, recently changed by others
- **Integration points**: Where new code touches existing code

**Phase output**: Discovery Report (use `agents/templates/discovery-report.md`)

---

## P — PLAN Phase

### Step 1: Test Anchor Generation

BEFORE designing any solution, write the test expectations:

```
For each acceptance criterion:
  1. Describe the test case in plain language
  2. Specify: input state → action → expected outcome
  3. Identify what existing test patterns to follow
  4. Note which test helpers/factories already exist
```

These test anchors become the source of truth for implementation correctness. Implementation is done when these tests pass.

### Step 2: Strategy Generation (Tree-of-Thoughts)

Generate 3-5 genuinely different strategies. Requirements:
- At least ONE strategy maximizes use of discovered internal assets
- At least ONE strategy is the conservative/minimal-change approach
- NO strawmen — every strategy must be plausibly the best choice
- Each strategy must differ in at least one of: architecture, coupling, scope, or risk profile

For each strategy, document:
- **Approach**: 2-3 sentence description
- **Files touched**: List with change type (modify/create)
- **Assets used**: Which discovered assets and how
- **Test impact**: New tests needed, existing tests affected
- **Risk profile**: What could go wrong

### Step 3: Strategy Scoring

Score each strategy on four dimensions (1-3 scale):

| Dimension | 1 (Poor) | 2 (Acceptable) | 3 (Good) |
|-----------|----------|-----------------|----------|
| **Risk** | High blast radius, low coverage in affected areas | Moderate, some coverage gaps | Low blast radius, good coverage |
| **Effort** | 3+ days, multi-team coordination | 1-2 days, single team | < 1 day, contained changes |
| **Alignment** | Ignores internal assets, creates parallel paths | Partial internal asset reuse | Full Internal First compliance |
| **Maintainability** | Adds technical debt, unclear ownership | Neutral to codebase health | Improves patterns, reduces duplication |

**Total: 4-12** (higher = better)

### Step 4: Deep Evaluation of Top 2

Expand the top 2 scoring strategies with:
- Detailed step-by-step implementation plan
- Specific file changes with pseudocode
- Dependency chain (what must be done first)
- Abort conditions (what would make this strategy fail)
- Blockers and unknowns

Re-score after deep evaluation. Hidden issues often emerge here.

### Step 5: Selection with Justification

Document:
- **Selected strategy**: Name + final score + one-paragraph justification
- **Runner-up**: Name + score + why it was rejected
- **Confidence level**: HIGH / MED / LOW
- **Abort conditions**: Specific signals that mean "stop and re-plan"
- **Boundaries**: Files/systems explicitly out of scope for this implementation

**Phase output**: Execution Plan (use `agents/templates/execution-plan.md`)

### ECL emit on FORGE consultation

If Plan-phase reasoning calls for a FORGE consultation (adversarial reasoning, trade-off arbitration), emit a `reasoning-request.envelope.json` next to the question artefact (template at `templates/reasoning-request.envelope.json`). Required: `to.eidolon=forge`, `performative=REQUEST`, `artifact.kind=reasoning-request`. Body validates against `schemas/_base-profile.v1.json`. Skip the envelope when `ECL_VERSION` is absent.

---

## I — IMPLEMENT Phase

### Execution Priority

Follow this order strictly:

1. **USE** — Assets marked USE AS-IS. Wire them in directly.
2. **EXTEND** — Assets marked EXTEND. Add methods/features to existing code.
3. **WRAP** — Assets marked WRAP. Create adapter layer for legacy interfaces.
4. **CREATE** — New code only when Discovery confirmed no suitable internal alternative.

### Architect/Editor Separation

For Complex-tier tasks, separate reasoning from editing:

```
ARCHITECT PASS (reasoning):
  - Describe WHAT needs to change and WHY
  - Specify the interface contracts between components
  - Define the data flow through the change

EDITOR PASS (implementation):
  - Translate architect output into actual code edits
  - Follow existing code style and conventions exactly
  - Produce minimal, targeted diffs (not rewrites)
```

### Implementation Rules

- Write tests for new functionality FIRST (test-anchored from Plan phase)
- One logical change per commit. Each commit should pass linter + existing tests.
- If you discover an asset not found in Analyze, STOP and update the Discovery Report.
- If implementation reveals the plan is wrong, STOP and return to Plan phase.
- Track progress with structured task list:

```
## Task Progress
- [x] TASK-1: Create factory method for Widget — DONE
- [ ] TASK-2: Extend WidgetRepository with #find_active — IN PROGRESS
- [ ] TASK-3: Add WidgetComponent for list view — BLOCKED (needs TASK-2)
- [ ] TASK-4: Wire controller action — PENDING
```

### Targeted Test Execution

Run tests incrementally, not all at once:
1. Run the SINGLE most relevant test after each change
2. Fix that failure before moving to the next change
3. Run the broader test suite only after all individual tests pass
4. This prevents the overcorrection cascade (fixing one thing, breaking another)

### ECL emit on Implement-phase exit

On phase exit, emit `apivr-completion-report.envelope.json` next to the completion artefact (template at `templates/apivr-completion-report.envelope.json`). Required: `to.eidolon=idg`, `performative=PROPOSE`, `artifact.kind=apivr-completion-report`, `integrity.method=sha256` matching the payload bytes. Profile schema: `schemas/apivr-completion-report-profile.v1.json` (required keys: `files_changed_count`, `tests_run`, `tests_passed`). Skip when `ECL_VERSION` is absent.

---

## V — VERIFY Phase

Run and capture output for ALL of these:

| Check | Tool | Pass Criteria |
|-------|------|--------------|
| Linter | Language-specific (Rubocop, ESLint, etc.) | Zero new violations |
| New tests | Test runner | All test anchors from Plan phase pass |
| Regression | Full test suite | No new failures |
| Coverage | Coverage tool | No decrease in affected files |
| Build | Build system | Clean build |
| Type check | If applicable | Zero new type errors |

**Decision**:
- ALL PASS → proceed to **Δ (Delta)**
- ANY FAIL → proceed to **R (Reflect)**

---

## R — REFLECT Phase (Failure Only)

Load skill: `agents/skills/failure-recovery.md`

### Evidence Gate (MANDATORY)

**STOP** if you have no concrete artifacts. You need at least one of:
- Test failure output with assertion details
- Lint error with file:line
- Build error with stack trace
- Runtime error with traceback

**No artifacts = ESCALATE immediately.** Do not guess at fixes.

### ECL emit on 3-failure escalation

When the 3-failure-same-category threshold fires, the escalation MUST be wrapped in a `repair-failed-report.envelope.json` (template at `templates/repair-failed-report.envelope.json`). Required: `to.eidolon=vigil`, `performative=ESCALATE`, `trust_level=high`, `assumptions[0]="trigger: 3-failure-same-category"`. Profile schema: `schemas/repair-failed-report-profile.v1.json` (required keys: `attempts>=3`, `failure_category`, `last_test_command`). See `skills/failure-recovery.md` for the full escalation envelope contract. Skip when `ECL_VERSION` is absent.

### Failure Protocol

See `agents/skills/failure-recovery.md` for the full classification taxonomy and recovery procedures. Quick reference:

| Attempt | Condition | Action |
|---------|-----------|--------|
| 1st failure | HIGH/MED confidence in root cause | Fix with targeted change |
| 2nd failure | Same category as 1st | Different approach required |
| 3rd failure | Same category | **ESCALATE** — summarize attempts |
| Any failure | LOW confidence | **ESCALATE** immediately |
| Any failure | No concrete error artifacts | **ESCALATE** immediately |

### Escalation Format

When escalating, provide:
```
## Escalation: [task description]

### What was attempted
1. [Approach 1]: [what happened]
2. [Approach 2]: [what happened]

### Evidence collected
- [error output, test results, etc.]

### My assessment
- Root cause hypothesis: [best guess with confidence]
- What I need: [specific help required]

### Suggested next steps for human
1. [concrete suggestion]
```

---

## Δ — DELTA Phase (Success Only)

After successful verification, evaluate the touched code for normalization opportunities.

### Candidate Scoring

```
Priority = (Severity + Frequency + Velocity) - Cost
```

Each factor scored 1-3. Threshold: **≥ 3 to suggest**.

| Factor | 1 | 2 | 3 |
|--------|---|---|---|
| Severity | Cosmetic | Moderate coupling | Architectural debt |
| Frequency | Seen once | Seen 2-3 times | Pattern across codebase |
| Velocity | Stable area | Moderate change rate | Active development area |
| Cost | Major refactor | Moderate effort | Quick improvement |

### Anti-Criteria (Reject if ANY match)

- First occurrence only → premature abstraction
- Dormant area (> 6 months since last meaningful change)
- High cost but affects ≤ 2 files
- "Might be useful someday" reasoning
- Would require changes outside current domain

### Output Format

```
## Delta Suggestions

### Δ-1: [Title]
- Pattern: [what was observed]
- Location: [file:line references]
- Score: Severity(X) + Frequency(X) + Velocity(X) - Cost(X) = [total]
- Suggestion: [specific improvement]
- Effort estimate: [hours/days]

Status: SUGGESTION ONLY — Do not implement
```

**CRITICAL**: Delta suggestions are OUTPUT ONLY. Never implement infrastructure suggestions. Log them to `agents/memories/delta-history.md` for future reference.

---

## Post-Task: Memory Update

After every task (success or failure), update memory. See `agents/skills/memory-management.md`.

```
Record:
- Task summary (one line)
- Outcome: SUCCESS / PARTIAL / FAILED / ESCALATED
- Key decisions and why
- Assets discovered or created
- Failure patterns encountered (if any)
- Delta suggestions generated (if any)
```

---

*APIVR-Δ Methodology v3.0 — Flow-engineered, test-anchored, context-aware*
