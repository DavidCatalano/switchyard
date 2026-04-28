# Consultation Document Template

**Title**: [One-line description of what's being consulted on]
**ID**: SEP-###-0#-CONSULT-[topic-slug] (04 or next available doc index for SEP-###)
**Date**: YYYY-MM-DD
**Consultant**: [Who is being consulted: Claude, Codex, ChatGPT, human name, etc.]
**PRD**: SEP-###-01-PRD-[feature-name].md
**Predecessor**: [Link to prior CONSULT if this is a follow-up, or "None"]

---

## Question

State the specific question or decision point. Be precise — the consultant should understand what you need from this section alone.

## Context

Background the consultant needs beyond what's in the PRD. Include:
- What has been tried or discussed so far
- Constraints or tradeoffs that have surfaced
- Links to specific artifacts if relevant (ADRs, spec sections, CONTEXT findings)

## Response

> Consultant's response goes here.

---

### Usage Notes
- The PRD + this document should be sufficient for the consultant to engage. Avoid requiring them to read the full PLAN or CONTEXT.
- For multi-round consultations on the same topic, chain via the Predecessor field.
- CONSULTs are snapshots — once the response is captured, the document is complete.
