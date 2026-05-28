# Canary Mission Template

Copy this file to `evals/canary-missions.md` (append a new section) and to
`evals/missions.py` (add a new `can_N_<slug>` function). Then register the
function in `MISSIONS` list and set `ab_arm` if it participates in the
headline A/B metric.

---

## CAN-N: mission_slug

**Scenario:** [1-3 sentences describing the setup and action. Be precise about
which tool is called, what trust tier the caller has, and what layer is targeted.]

**Oracle:** [What the memory system should do. Include both the expected
success path and the expected failure/rejection path if the test covers both.]

**Pass criterion:** [Boolean assertion. E.g.: "Recall returns crystal in top-3
AND row count unchanged". Must be mechanically checkable in Python.]

**A/B arm:** YES / NO — [brief justification. YES if memory-on should
deterministically beat memory-off on this mission.]

---

## Implementation checklist

- [ ] Add to `evals/canary-missions.md` (narrative)
- [ ] Add `can_N_<slug>(env: CanaryEnv) -> MissionResult` to `evals/missions.py`
- [ ] Register in `MISSIONS` list
- [ ] If A/B arm: add mission ID to `AB_ARM_MISSION_IDS` set
- [ ] If deep scenario: add focused module in `evals/<scenario>.py`
- [ ] Add fixture data to `evals/fixtures/` if needed
- [ ] Verify mission passes on a real store (docker compose run)
