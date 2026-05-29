---
name: apivr-memory-management
description: "Load at APIVR-Δ session start, session end, or when you notice a repeating pattern across tasks. Reflexion-style episodic memory protocol: read memory before planning; write after Verify and Reflect; consolidate at session end. Use to preserve learned patterns, failure classifications, and delta history across sessions."
methodology: APIVR-Δ
methodology_version: "3.0"
---

# Memory Management Skill

Persistent memory protocols for cross-session learning, task tracking, and pattern recognition. Based on Reflexion-style episodic memory with structured consolidation.

---

## Memory Architecture

```
agents/memories/
├── task-log.md          # Completed tasks with outcomes (≤ 30 entries)
├── pattern-registry.md  # Discovered assets and architectural patterns
├── failure-catalog.md   # Root causes and prevention strategies (≤ 30 entries)
├── delta-history.md     # Normalization suggestions with status
└── session-handoff.md   # Checkpoint for session boundaries
```

Each file has a defined schema, size cap, and consolidation strategy.

---

## Session Start Protocol

At the beginning of every coding session:

```
1. Read task-log.md — scan last 5-10 entries for:
   - Work in the same domain as current task
   - Open items or follow-ups from previous tasks
   - Recent patterns that might apply

2. Read failure-catalog.md — scan for:
   - Failures in the same domain or file area
   - Prevention strategies relevant to current task type
   - Known gotchas in the technology stack

3. Read session-handoff.md — if exists:
   - Resume from last checkpoint
   - Verify assumptions are still valid
   - Check if codebase has changed since handoff

4. Read pattern-registry.md — scan for:
   - Known reusable assets in the target domain
   - Architectural decisions and their rationale
   - Team conventions discovered in past sessions
```

**Budget**: Memory recall should take ≤ 1-2K tokens of context. Summarize, do not paste entire files.

---

## Session End Protocol

At the end of every session (or before context would be lost):

### Update Task Log

Add entry to `agents/memories/task-log.md`:

```markdown
### [DATE] — [Task Title]
- **Domain**: [module/area]
- **Outcome**: SUCCESS | PARTIAL | FAILED | ESCALATED
- **Summary**: [one sentence]
- **Key decisions**: [what was chosen and why]
- **Assets**: [discovered or created]
- **Lesson**: [one takeaway for future tasks]
```

### Update Failure Catalog (if failures occurred)

Add entry to `agents/memories/failure-catalog.md`:

```markdown
### [DATE] — [Failure Category]: [Brief description]
- **Context**: [what was being done]
- **Error**: [one-line error summary]
- **Root cause**: [what actually went wrong]
- **Fix applied**: [what resolved it]
- **Prevention**: [how to avoid this in future]
- **Domain**: [area/module for searchability]
```

### Update Pattern Registry (if new patterns discovered)

Add entry to `agents/memories/pattern-registry.md`:

```markdown
### [Asset/Pattern Name]
- **Location**: [file:line or directory]
- **Type**: Model | Service | Component | Utility | Convention | Architecture
- **Purpose**: [what it does]
- **Usage example**: [how to use it, one line]
- **Quality**: Tested | Untested | Deprecated
- **Discovered**: [date]
```

### Write Session Handoff (if work is incomplete)

Write to `agents/memories/session-handoff.md`:

```markdown
## Session Handoff — [DATE]

### Task in Progress
- **Goal**: [acceptance criteria]
- **Current phase**: A | P | I | V | R | Δ
- **Branch**: [git branch name]

### Completed Steps
1. [step] — done
2. [step] — done

### Remaining Steps
3. [step] — next
4. [step] — blocked on [reason]

### Key State
- Files modified: [list]
- Tests: [X passing, Y failing]
- Open questions: [any ambiguities]

### Context to Re-inject
- [Critical decision that must be remembered]
- [Important constraint discovered during work]
```

---

## Memory Consolidation

### When to Consolidate

Run consolidation when any memory file exceeds its cap:

| File | Cap | Consolidation Strategy |
|------|-----|----------------------|
| task-log.md | 30 entries | Merge old entries by domain into summaries, keep recent 15 as-is |
| failure-catalog.md | 30 entries | Deduplicate by root cause pattern, archive resolved patterns |
| pattern-registry.md | No hard cap | Remove entries for deleted/deprecated code, merge duplicates |
| delta-history.md | 20 entries | Remove implemented suggestions, archive rejected ones |
| session-handoff.md | 1 entry | Overwrite on each session end (always current) |

### Consolidation Rules

```
1. Recency bias: Recent entries are always more valuable than old ones
2. Frequency matters: Patterns seen 3+ times get promoted to "key patterns"
3. Stale removal: Entries referencing files that no longer exist → archive
4. Dedup by root cause: Multiple failures with same root cause → single entry with count
5. Never delete silently: Move to an "archived" section, don't delete
```

### Example Consolidation (Task Log)

Before:
```
### 2025-12-01 — Add widget search
- Domain: widgets
- Outcome: SUCCESS
- Summary: Added text search to widget list
...

### 2025-12-03 — Add widget filters
- Domain: widgets
- Outcome: SUCCESS
- Summary: Added date and status filters
...

### 2025-12-05 — Fix widget sort
- Domain: widgets
- Outcome: SUCCESS
- Summary: Fixed sort order on widget list
```

After consolidation:
```
### Widgets Module — Summary (Dec 2025)
- 3 tasks completed (all SUCCESS)
- Added: text search, date/status filters, sort fix
- Key assets: WidgetQuery (app/models/widgets/queries/), WidgetListComponent
- Lesson: WidgetQuery handles all list filtering; extend it rather than adding controller logic
```

---

## In-Session Task Tracking

During implementation, maintain a structured task list in your working context:

```markdown
## Active Task: [title]
Phase: IMPLEMENT (step 3 of 5)

### Progress
- [x] TASK-1: Discover existing assets — DONE
  - Found: WidgetRepository, WidgetQuery, WidgetFactory
- [x] TASK-2: Write test anchors — DONE
  - 4 test cases in spec/models/widget/search_spec.rb
- [ ] TASK-3: Extend WidgetQuery with #search_by_text — IN PROGRESS
  - Using existing #base_scope pattern
- [ ] TASK-4: Add controller action — PENDING
- [ ] TASK-5: Run full verification suite — PENDING

### Blockers
- None currently

### Decisions Made
- Using WidgetQuery#search_by_text over raw SQL (Internal First principle)
- Following existing pattern from OrderQuery#search (see pattern-registry)
```

**Re-inject this checklist** after every tool use / verification cycle to prevent losing track.

---

## Memory Query Patterns

### Finding Relevant Past Work

```
Query: "Have I worked on [module/domain] before?"
Search: task-log.md for domain matches
Return: Last 3 entries + any consolidated summary

Query: "What assets exist in [domain]?"
Search: pattern-registry.md for domain/location matches
Return: All matching entries with locations

Query: "Have I seen this error before?"
Search: failure-catalog.md for error category + domain matches
Return: Matching entries with prevention strategies

Query: "Were there improvement suggestions for [area]?"
Search: delta-history.md for domain matches
Return: Open suggestions with scores
```

### Memory-Informed Decisions

Use memory to shortcut the APIVR-Δ cycle:

```
If memory shows:
  - Same asset used successfully before → Skip re-evaluation, USE directly
  - Known failure pattern in area → Add extra verification step
  - Previous Delta suggestion applies → Consider implementing as part of current task
  - Past escalation in same area → Lower confidence threshold, escalate earlier
```

---

## Anti-Patterns in Memory

### What NOT to Store

- Exact code snippets (they go stale; store patterns and locations)
- Speculative conclusions ("this might be useful" — record only validated patterns)
- Emotional commentary ("this code is terrible" — record objective quality assessment)
- Raw error logs (store classified root causes, not full stack traces)
- Information about files that have been deleted or heavily refactored

### What ALWAYS to Store

- Successful patterns with locations (high reuse value)
- Failure root causes with prevention strategies (avoid repeating mistakes)
- Architectural decisions with rationale (institutional knowledge)
- Asset discovery results (reduces future Analyze phase time)
- Team conventions that aren't documented elsewhere

---

## ECL_VERSION

When restoring a session, query `ECL_VERSION` (single-line file at the Eidolon install root) alongside the Eidolon version. Drift > 1 minor relative to the consumer's expected envelope version triggers a warning surface — see ECL v1.0 §7.2.

---

*Memory Management Skill — episodic, structured, consolidation-aware*
