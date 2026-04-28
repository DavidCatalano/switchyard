# Development Workflow

## Overview

This document defines the development workflow for the Switchyard project. It covers phase sequencing, governance conventions, quality gates, and artifact management.

> **User** holds the singular authority to advance to the next development phase.

### Two-Track Workflow

Switchyard supports two workflow tracks. Choose based on the scope and risk of the effort.

| Track | When to Use | Artifacts Produced |
|-------|-------------|--------------------|
| **Lightweight** | Bug fixes, single-feature additions, small refactors, documentation updates | PLAN only (requirements inlined) |
| **Standard** | Multi-component features, protocol changes, new services, architectural shifts | PRD → [CONTEXT] → [SPEC] → PLAN → [PR] |

The Lightweight track collapses Initiation, Research, and Planning into a single scoping conversation that produces a PLAN with inlined requirements. The Standard track follows the full phase sequence below. Both tracks share the same governance conventions (branching, commits, PRs, quality gates).

### Choosing a Track

- Default to **Lightweight**. Escalate to Standard when:
  - The change spans multiple modules or services
  - External research is needed (new protocols, standards, libraries)
  - Architecture tradeoffs require explicit decisions beyond the PLAN
  - The user requests it

## Workflow Phases

### Lightweight Track

```
Phase 1  Scoping ......... Brief PLAN with inlined requirements
Phase 2  Implementation .. TDD against PLAN
Phase 3  Closeout ........ User review, commit, PR
```

1. **Scoping** — Discuss the need with the user, produce a PLAN (`SEP-###-02-PLAN-...`) with requirements, work breakdown, and test strategy inlined. Skip the PRD and CONTEXT. User approves the PLAN.
2. **Implementation** — Execute the PLAN using TDD (see Test Design & Implementation below). Run linters and type checkers. Mark off completed items in the PLAN.
3. **Closeout** — User reviews deliverables. Commit, push, create PR (see governance conventions below).

### Standard Track

```
Phase A  Initiation .............. PRD stub
Phase B  Research ................ Internal code, External web search
Phase C  Requirements & Planning . Spec draft as needed then -> PLAN
Phase D  Implementation .......... TDD against PLAN
Phase E  Final Review ............ Smoke tests and final reviews
Phase F  Closeout ................ User review, commit
```

#### 1. Initiation (PRD)

Collaborate with the user to articulate a **statement of need**, forming the stub of a PRD (`SEP-###-01-PRD-...`). "SEP" stands for "Switchyard Enhancement Proposal".

#### 2. Research & Context Gathering

Create **CONTEXT documents** (`SEP-###-0#-CONTEXT-...`) for non-trivial efforts. Each research agent writes to its own CONTEXT file with a unique slug. Increment document counter `-0#-` as next available number 04 or greater. Launch these agents in parallel if possible:

- **Internal research** — Launch a sub agent or directly research and populate the CONTEXT document. Surface file paths, relevant sections, constraints, contradictions, and impacted artifacts. Non-prescriptive; defers architecture tradeoffs to Phase 3.
- **External research** — Populate external research CONTEXT document with relevant protocols, standards, and patterns.

Consolidate and finalize the CONTEXT documents after research inputs complete.

#### 3. Requirements & Planning

Synthesize research findings into actionable deliverables:

- Refine the PRD: requirements, non-goals, risks, and success criteria. Preserve invariants and existing governance decisions. Escalate unresolved tradeoffs to user decisions.

**Spec-first work (protocol SEP):** If warranted, draft the spec document(s) in `switchyard-internal/docs/v0/` *before* building the PLAN. The spec draft defines interfaces, state transitions, formats and invariants. Draft conformance fixtures (valid + invalid) alongside the spec to validate the design as needed. Validate the spec draft before proceeding. The PLAN then organizes the implementation work against those specs.

- Build a **PLAN document** (`SEP-###-02-PLAN-...`) with work breakdown, dependencies, test strategy, and validation expectations.
- User reviews and collaborates on revisions (may include external consultants).
- Finalize both PRD and PLAN, marking them ready for implementation.

#### 4. Test Design & Implementation

Execute the PLAN using TDD:

1. Design comprehensive test coverage; user reviews test approach.
2. Write failing tests first (unit, integration, E2E).
3. Implement to pass tests.
4. Run linters and type checkers.
5. Update implementation guides with new functionality and examples.
6. Mark off completed items directly in the PLAN.

#### 5. Final Review & Documentation

- Update impacted specifications, cross-references, and SEP artifacts.
- Synchronize examples with implementation; draft PR summary artifact when needed (`SEP-###-##-PR-...`).

#### 6. Closeout

1. User reviews deliverables, performs code review and UAT.
2. Commit with a conventional commit message (see Commit Message Conventions).
3. Push branch and create PR (see Pull Request Workflow).
4. User approves and merges PR on GitHub.
5. Post-merge: switch to `main`, pull, delete local branch.

## Governance & Conventions

### Branching Conventions
- All SEP-aligned work happens on a branch, never directly on `main`.
- Primary branch format: `sep/XXX` where `XXX` matches the SEP number (zero-padded to 3 digits).

### Commit Message Conventions
- Use Conventional Commit style (`feat`, `fix`, `docs`, `chore`, etc.).
- Scope reflects the monorepo area that changed. Use the table below to select scope:

| Directory | Scope |
|-----------|-------|
| `switchyard-api/` | `api` |
| `switchyard-internal/` | `planning` |

- For changes spanning multiple areas, comma-separate: `feat(api,planning)`.
- **SEP artifacts** (`switchyard-internal/sep/`): scope to the primary affected area. The `process` scope is reserved for changes to process artifacts (AGENTS.md, DEV.md, templates, etc.).
- Do not use SEP IDs in commit scope or subject.
- Keep the subject concise and outcome-focused.
- If a commit body is used, target 3-5 bullets; hard cap at 8 bullets.
- Put operational detail in SEP artifacts, not in commit bodies.
- Do not add `Co-Authored-By`, `Generated with`, or any other self-attribution trailers.

**Examples:**
```
feat(api): apply endpoint migration
feat(process): revise cold start instructions
```

### Pull Request Workflow
- Every SEP branch merges to `main` via pull request — no direct pushes to `main`.
- Draft a PR artifact (`SEP-###-##-PR-...`) using `DEV_TEMPLATE_PR.md` for non-trivial efforts. This artifact is the canonical draft for the GitHub PR body.
- PR artifact drafts are written during Phase 5 (Final Review). Use the artifact content directly in `gh pr create` (for example, `--body-file`) or copy/paste.
- Keep PR body content concise and reviewer-focused: summarize changes, validation, and risks.
- Do not link or reference PRD/PLAN in the PR body - this is assumed context.
- Create PRs in **draft mode** when early visibility is useful; otherwise create as ready for review.
- PR title follows the same conventions as commit messages (Conventional Commit style with monorepo scope).
- User reviews, approves, and merges the PR on GitHub.

**Post-merge checklist** (user performs after merge):
1. Switch to `main`: `git checkout main`
2. Pull merged changes: `git pull origin main`
3. Delete the local SEP branch: `git branch -d sep/XXX`

### Artifact Detail Depth
- `DEVLOG` is concise orientation only: enough context to quickly understand current project state.
- `CONTEXT` is the canonical location for deep findings, evidence, and research notes.
- `PLAN` is the canonical location for decision logging and task-state tracking.

### Artifact Referencing Conventions
- Never embed developer PII (local usernames, home directory paths) in artifact documents. All file references must be repo-relative from the repository root (e.g., `switchyard-api/pyproject.toml`, not `/Users/jane/Projects/Switchyard/switchyard-api/pyproject.toml`).
- SEP artifact documents (SEP-*.md) are referenced by filename only, wrapped in backticks, not markdown links and live in `switchyard-internal/sep/`.
- All other file references (code, specs, schemas, process docs) use repo-relative paths from repository root, wrapped in backticks, and must not be markdown links.

### GitHub Issues as Backlog

GitHub Issues are the canonical backlog. When you encounter out-of-scope items during any phase — bugs, tech debt, design gaps, follow-on work — propose a GitHub issue to the user rather than deferring silently or embedding TODOs in code.

**When to propose an issue:**
- Out-of-scope findings surfaced in CONTEXT, CONSULT, or PLAN artifacts
- Tech debt or spec inconsistencies discovered during implementation
- Design questions that don't need to be resolved in the current SEP
- Follow-on work identified during review

**Labels:** Apply scope labels matching commit scopes (`api`, `process`) and type labels (`bug`, `enhancement`, `tech-debt`, `question`). The `SEP-candidate` label is reserved for user consent prior to assigning so ask first.

**Issue detail:** Match the detail to the complexity. A simple bug or rename can be a one-liner. A design gap that surfaced from extended discussion should capture the problem statement, related artifacts, and open questions — enough context to cold-start a future SEP without re-discovering the problem.

### Decision Logging and ADR Threshold
- Use the PLAN decision log for project-local execution decisions.
- Decision entries use this schema:
  - `decision`: what was decided
  - `reason`: why this choice was made
  - optional `links`: related artifacts
  - optional `notes`: additional context
- Decision progression states: `Draft` -> `Pending` -> `Agreed`
  - `Draft`: proposed, not yet discussed
  - `Pending`: discussed, awaiting user sign-off
  - `Agreed`: accepted by user (record date or reference)
- ADRs are reserved for high-impact cross-effort architecture decisions.
- Use PLAN decision logs for effort-local execution decisions.

## Quality Gates

```bash
# Python SDK (run from switchyard-api/)
uv run pytest                      # All tests pass
uv run ruff check src --fix        # Code formatting
uv run mypy src/switchyard         # Type checking
```

- No regressions in existing functionality
- All cross-references updated and valid

## Project Structure & Artifact File Naming

### Repository Layout
```
switchyard/
├── switchyard-api/
│   ├── TBD/          ← tbd description
│   └── TBD/          ← tbd description
└── switchyard-internal/
    ├── DEV*.md          ← This doc and templates
    └── sep/             ← SEP planning artifacts (PRD/PLAN/DEVLOG/CONTEXT/CONSULT/PR)
```

#### Project Doc Directory Structure
```
switchyard-internal/
└── sep/
    ├── SEP-000-01-PRD-feature-name.md       # Product Requirements Document (required)
    ├── SEP-000-02-PLAN-feature-name.md      # Project Plan (required)
    ├── SEP-000-03-DEVLOG-feature-name.md    # Devlog (optional, user-initiated)
    ├── SEP-000-04-[CONTEXT|CONSULT|PR|REVIEW]-feature-name.md   # Additional as-needed
    └── SEP-000-##-[CONTEXT|CONSULT|PR|REVIEW]-feature-name.md   # Additional as-needed
```

#### File Naming Convention
Format: `SEP-[SEQ]-[DOC#]-[DOC-TYPE]-[slug].md`

- **SEQ**: 000, 001, etc. — Sequential SEP number
- **DOC#**: Two-digit document index within the SEP
- **DOC-TYPE**: PRD, PLAN, DEVLOG, CONTEXT, CONSULT, ADR, PR, BUG
- **slug**: Concise kebab-case name (feature name, topic, or focus area)

Fixed positions: `01-PRD`, `02-PLAN`, `03-DEVLOG`. All other document types use `04` or the next available index. Multiple documents of the same type are expected (e.g., several CONTEXT or CONSULT docs). The index sequence and unique slug tells the story of the effort.

#### Artifact Templates

| Artifact | Template | Lightweight | Standard | Notes |
|----------|----------|-------------|----------|-------|
| PRD | `DEV_TEMPLATE_PRD.md` | — | Required | Always before PLAN in Standard track |
| PLAN | `DEV_TEMPLATE_PLAN.md` | Required | Required | Inlines requirements on Lightweight track |
| DEVLOG | `DEV_TEMPLATE_DEVLOG.md` | Optional | Optional | User-initiated breadcrumb trail |
| CONTEXT | `DEV_TEMPLATE_CONTEXT.md` | — | As-needed | Each research agent writes its own file with unique slug |
| CONSULT | `DEV_TEMPLATE_CONSULT.md` | — | As-needed | For 3rd-party consultation |
| ADR | `DEV_TEMPLATE_ADR.md` | — | As-needed | High-impact cross-effort decisions only |
| PR | `DEV_TEMPLATE_PR.md` | As-needed | As-needed | Draft summary; final PR lives in GitHub |
