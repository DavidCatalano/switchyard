# Product Requirements Document (PRD) Template v2

**Title**: [Short descriptive name for the feature or fix]
**ID**: SEP-###-01-PRD-[feature-name] (### is next available SEP number)
**Status**: Draft | In Progress | Complete
**Date**: YYYY-MM-DD
**Related Docs**: Link to CONTEXT, PLAN, ADRs, relevant specs
**Dev Track**: [Track name] (`DEV_TRACK_*.md`) — note primary and supplemental if multiple apply

---

## 1. Background

- What triggered this work? (bug, feature request, protocol gap, tech debt)
- Why now? What patterns are stale, what's blocked, what's the cost of inaction?
- Link to audits, prior SEPs, or external constraints that inform the decision.

## 2. Scope

Organize by phase when the work has a natural sequence. Each phase should state what it delivers (artifacts, refactors, schemas) concretely enough to build a PLAN from. Reference GitHub issues inline.

### Phase A: [Name] — GH #...
- [Work items with enough detail to understand what changes and what's produced]

### Phase B: [Name] — GH #...
- [Work items]

### Phase N: [Name] — GH #...
- [Work items]

### Non-Goals
- What will NOT be addressed (prevent scope creep)
- Reference GitHub issues for deferred work

### Dependencies
- What must already exist for this work to proceed
- Note whether dependencies are shipped or still in progress

## 3. Implementation Considerations

- High-level approach, sequencing rationale, or architectural constraints
- References to ADRs, prior SEP patterns, or reference implementations
- Protocol alignment: which specs, content types, or conventions apply

## 4. Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| [What could go wrong] | Low/Medium/High | [How to prevent or recover] |

## 5. Validation & Done

**Quality gates** — the verification steps that run during implementation:
- Test suites, conformance fixtures, linters, type checkers
- Manual smoke tests or demo validation

**Done when** — the exit criteria for the SEP:
- [Concrete, observable outcomes — not a restatement of scope]
- [Focus on properties that aren't obvious from "the scope was delivered"]

---

### Usage Notes
- Keep PRD concise (ideally under 100 lines of content). Details go into PLAN and ADRs.
- PRD is the "what & why"; PLANs carry the "how" with work breakdown and task tracking.
- Each concern should appear once. Scope defines what's delivered. Validation defines how you know it worked. Avoid restating scope as objectives, requirements, deliverables, and success criteria separately.
- Phase structure replaces separate Timeline, Deliverables, and In Scope sections — phases *are* the scope, organized by sequence.
- Non-functional requirements (quality, conformance) belong in Validation, not as a separate requirements section.
