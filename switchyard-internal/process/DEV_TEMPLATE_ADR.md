# ADR Template

**Title**: [Short descriptive name for the decision]
**ID**: SEP-###-0#-ADR-[decision-name] (01 or next available ADR for the given SEP)
**Status**: Proposed | Accepted | Superseded
**Date**: YYYY-MM-DD
**Authors**: David, [AI Agent Name] (Claude, Codex, Gemini, etc.)
**Related Docs**: Link to PRD, CONTEXT, PLAN, or specs

---

## 1. Context
Describe the background and forces at play:
- Why is this decision needed?
- What problem are we solving?
- Which constraints, requirements, or protocol invariants apply?
- Relevant findings from PRD/CONTEXT/Blueprint/Audit

## 2. Decision
State the choice made, clearly and unambiguously:
- What approach is chosen?
- What scope does it cover?
- What is explicitly out of scope?

## 3. Consequences
Explain the impact of the decision:
- **Positive**: Benefits, what this enables, alignment with protocol/workflow
- **Negative**: Tradeoffs, limitations, added complexity
- **Future-proofing**: How easily this can evolve or be replaced

## 4. Alternatives Considered
List options that were considered but not chosen, and why:
- Option A: pros/cons
- Option B: pros/cons
- (etc.)

## 5. Implementation Notes
High-level guidance for implementers:
- Key design patterns
- References to demos or specs
- Any special testing or conformance requirements

## 6. Open Questions
Unresolved items that may need to be decided later.

---

### Usage Notes
- Keep ADRs short (1–2 pages). They capture *decisions* not full designs.
- Link ADR from PRD/PLAN and summarize in PR description.
- Supersede old ADRs if direction changes; don’t overwrite history.

