# Project Plan (PLAN)

**Title**: [Short descriptive name for the feature or fix]
**ID**: SEP-###-02-PLAN-[feature-name]
**Status**: Draft | In Progress | Complete
**Date**: YYYY-MM-DD
**PRD**: SEP-###-01-PRD-[feature-name].md (Optional for Lightweight. Delete if not needed.)

---

## Implementation Approach

High-level summary of how this PLAN executes the PRD objectives and respects ADR decisions.

See `AGENTS.md` for workflow phases, decision logging, quality gates, and specialized track references.

## Task Breakdown

### Phase 1: [Phase Name]

**Goal**: [What this phase achieves]

#### Tasks
- [ ] **[TASK-ID]**: [Task description]
- [ ] **[TASK-ID]**: [Task description]

### Phase 2: [Phase Name]

**Goal**: [What this phase achieves]

#### Tasks
- [ ] **[TASK-ID]**: [Task description]
- [ ] **[TASK-ID]**: [Task description]

_(Add additional phases as needed)_

---

## Dependencies

### Critical Path
1. [Task dependencies and ordering]
2. [Blocking relationships]

### Parallel Work Streams
- [Tasks that can be done concurrently]

---

## Technical Architecture Decisions

**IMPORTANT:**
- **Pre-Code Development** Prior to development no technical architectural decisions should be outlined in this PLAN document. If items surface pause and raise it to the user for immediate incorporation into the PRD or optional ADR document.
- **Active Code Development** Technical architectural decisions not already specified in the PRD or optional ADR document must be captured as they are made in the draft Pull Request (PR) document.

---

## Risk Mitigation

### Risk 1: [Risk Description]
- **Mitigation**: [How to handle it]
- **Owner**: [Who addresses it]
- **Timeline**: [When to address]

---

## Validation Plan

**Validation Commands**: See `AGENTS.md` Section "Quality Gates" for complete validation command list.

### Success Criteria Validation
- [ ] [Specific success criterion from PRD]
- [ ] [Another success criterion]
- [ ] All quality gates pass (see `AGENTS.md`)
- [ ] No regressions in existing functionality

---

This plan serves as the single source of truth during implementation. Update status as work progresses.
