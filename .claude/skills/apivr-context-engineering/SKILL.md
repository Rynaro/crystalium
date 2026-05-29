---
name: apivr-context-engineering
description: "Load at the start of the APIVR-Δ Analyze phase. Techniques for building a repo map, running progressive disclosure, and maximising context relevance for coding tasks. Use whenever you need to understand unfamiliar code before editing it, or when an existing Discovery Report feels under-grounded."
methodology: APIVR-Δ
methodology_version: "3.0"
---

# Context Engineering Skill

Techniques for maximizing the quality and relevance of information available during coding tasks. The primary constraint on agent performance is not reasoning capability but context quality.

---

## Principle: Progressive Disclosure

Do NOT front-load context. Discover incrementally:

```
1. Directory structure (cheap: ~100 tokens)
2. Repo map / structural summary (moderate: ~500-1K tokens)
3. Interface signatures of relevant files (moderate: ~200 tokens/file)
4. Full file contents ONLY for files being modified (expensive: varies)
```

Never read full file contents "just in case." Every token of context displaces reasoning capacity.

---

## Repo Map Generation

### Purpose
Create a compressed structural overview of the codebase that fits in ~500-1K tokens. This gives you a bird's-eye navigation map before diving into details.

### Procedure

```
Step 1: Directory scan
  - List the project root 2-3 levels deep
  - Identify top-level organization pattern (by domain? by layer? hybrid?)
  - Note: config/, lib/, app/, spec/test/ locations

Step 2: Structural extraction (for target domain + adjacent domains)
  - For each key file, extract ONLY:
    - Class/module name
    - Public method signatures (name + params, not body)
    - Key imports/dependencies
    - Inheritance/mixin chain
  - Skip: private methods, implementation details, comments

Step 3: Reference ranking
  - Which files are imported by the most other files? (high leverage, high risk)
  - Which files import the most other files? (integration points)
  - Which files have the most recent changes? (active development)

Step 4: Compress to summary
  Format:
    DOMAIN/
      Model (inherits BaseModel) — #create, #update, #archive [tested]
      Repository — #find_by_id, #search, #count [tested]
      Service — #execute, #validate [untested ⚠️]
      Component — renders list, detail views [tested]
```

### Output
A concise map you reference throughout the task. Update it if you discover new relevant files.

---

## Hierarchical Localization

When searching for where to make changes, narrow progressively:

```
Level 1 — Domain identification
  "Which top-level domain/module is this feature in?"
  → Scan directory names, README, route definitions
  → Output: 1-3 candidate domains

Level 2 — File identification
  "Which files within the domain are relevant?"
  → Scan file names, class definitions, public interfaces
  → Output: 3-8 candidate files with relevance ranking

Level 3 — Symbol identification
  "Which specific classes, methods, or functions need to change?"
  → Read only the relevant sections of identified files
  → Output: Specific file:line targets for modification

Level 4 — Context gathering
  "What do I need to understand about these specific symbols?"
  → Read implementations, tests, callers of identified symbols
  → Output: Full understanding of change targets + their contracts
```

**Rule**: Do not jump to Level 4 without completing Levels 1-3. Premature deep reading wastes context budget on irrelevant code.

---

## Context Budget Management

### Token Budget Allocation (approximate for 128K context window)

| Category | Budget | Purpose |
|----------|--------|---------|
| System instructions + methodology | ~4K | Always loaded |
| Active skill (current phase) | ~2-3K | Loaded per-phase |
| Repo map | ~1K | Generated in Analyze |
| Memory recall | ~1-2K | Queried in Analyze |
| Discovery report | ~1-2K | Produced in Analyze |
| Execution plan | ~1-2K | Produced in Plan |
| Active file contents | ~10-30K | During Implement |
| Test output / error logs | ~2-5K | During Verify/Reflect |
| Conversation history | Remainder | Slides as session progresses |

### Context Pressure Signals

Watch for these signs that context is becoming stale or overloaded:

| Signal | Response |
|--------|----------|
| Forgetting earlier decisions | Re-inject execution plan summary |
| Repeating a search already done | Check if repo map is still in context |
| Hallucinating file contents | Re-read the actual file; do not rely on memory |
| Losing track of task progress | Re-inject task progress checklist |
| Contradicting earlier analysis | Summarize current state and restart from checkpoint |

### Context Refresh Protocol

When context pressure is detected:

```
1. Summarize current state in a structured checkpoint:
   - What has been accomplished
   - What remains
   - Key decisions made and why
   - Current blockers

2. Drop stale context:
   - File contents from already-completed steps
   - Superseded plan versions
   - Resolved error logs

3. Re-inject essential context:
   - Task goal and acceptance criteria
   - Execution plan (current version)
   - Task progress checklist
   - Repo map (if still relevant)
```

---

## Asset Search Strategies

### For Internal Asset Discovery

Use multiple search strategies in order of efficiency:

```
1. Convention-based search (fastest)
   - Look in expected locations based on project structure
   - e.g., model for "Widget" → app/models/widget/ or app/models/widget.rb

2. Naming pattern search
   - Search for related terms: grep -r "widget" --include="*.rb" -l
   - Search for interface patterns: grep -r "def.*widget" --include="*.rb"

3. Dependency chain traversal
   - Start from known related code and follow imports/requires
   - "What does the existing WidgetController already use?"

4. Test-based discovery
   - Search test files for how existing features are tested
   - Test factories reveal data patterns and relationships
   - Test helpers reveal shared utility functions
```

### For Understanding Existing Code

Before reading a full file, try these cheaper approaches:

```
1. Read ONLY the class/module definition + public interface
2. Read the corresponding test file (often more informative than source)
3. Read the most recent git log entries for the file (reveals intent)
4. Read callers of the code (reveals actual usage patterns)
```

---

## Language-Specific Context Hints

### Typed Languages (TypeScript, Go, Rust, Java)
- Interface/type definitions are extremely high-value context
- Read type signatures BEFORE implementations
- Types serve as self-documenting contracts

### Dynamic Languages (Ruby, Python, JavaScript)
- Test files are MORE important than source (they document behavior)
- Look for type annotations, YARD docs, JSDoc, or type stubs
- Pay special attention to framework conventions (Rails, Django)
- Grep for method calls to understand actual usage (duck typing hides interfaces)

### Configuration-Heavy Frameworks (Rails, Django, Spring)
- Route files map URLs to handlers (high-value context)
- Database schema/migrations define data model
- Initializers and middleware define cross-cutting behavior
- These are often more informative than application code

---

## Integration with APIVR-Δ Phases

| Phase | Context Engineering Action |
|-------|--------------------------|
| **A** Analyze | Generate repo map → hierarchical localization → asset discovery |
| **P** Plan | Ensure test patterns are in context → load relevant examples of similar features |
| **I** Implement | Keep only active files + plan in context → refresh when switching focus areas |
| **V** Verify | Load error output → cross-reference with implementation → drop implementation details |
| **R** Reflect | Load failure history from memory → ensure original requirements still in context |
| **Δ** Delta | Scan touched files + neighbors for patterns → use repo map for broader view |

---

## Verify Upstream Envelopes (ECL v1.0)

When the Analyze phase ingests an artefact handed off by ATLAS (`scout-report`), SPECTRA (`spec`), VIGIL (`root-cause-report`), or FORGE (`reasoning-report`), check for a sibling `${P%.*}.envelope.json` next to the payload.

If present:

1. Load `skills/verify-incoming.md`.
2. Run the validation pipeline (schema → integrity → contract match).
3. On `verify_pass`, proceed.
4. On `verify_fail`, emit the warning to stderr, append the failure code to `.eidolons/.trace/<thread_id>.jsonl`, **and continue** — verification is opt-in / warn-only at ECL v1.0.

If the sibling envelope is absent, proceed without verification. This is expected during the ECL rollout window when upstream Eidolons may not yet have adopted.

---

*Context Engineering Skill — progressive disclosure, hierarchical localization, budget-aware*
