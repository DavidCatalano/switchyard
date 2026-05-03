# Repository Guidelines

This repo hosts Switchyard, an API control plane for managing model runtimes,
model configuration, and deployment lifecycle operations.

## Agent Startup Requirements

Read this file at session start and treat it as the primary development
workflow for the repository.

## North Star

Switchyard is a local inference control plane. It helps a user launch, tune,
inspect, and route requests to model runtime containers running on hardware they
control.

The project should stay centered on model deployment ergonomics:

- Make model/runtime/host choices explicit and inspectable.
- Keep high-value runtime settings typed and validated.
- Preserve escape hatches for backend-specific flags and container options.
- Support local and remote Docker-backed hosts without hardcoding one machine.
- Optimize for custom management of known inference hardware, not generic
  container orchestration.

Switchyard should not drift into becoming Kubernetes, Podman, or a general
container-management UI. Docker and container details are implementation tools;
the product language should remain about models, runtimes, hosts, deployments,
accelerators, ports, model stores, health, and logs.

## Terminology

Use these terms consistently in code, docs, APIs, and planning artifacts:

- **Host**: A machine or environment that can run inference containers. A host
  owns machine-specific facts such as Docker connectivity, backend reachability,
  port ranges, accelerator inventory, model/cache stores, and container
  defaults.
- **Runtime**: A backend engine and its launch defaults, such as vLLM, SGLang,
  koboldcpp, or llama.cpp. Runtime settings describe how an engine is invoked,
  not where a specific model is stored.
- **Model**: A logical model source and model-family defaults. A model should be
  portable across hosts by referring to named stores rather than hardcoded host
  paths.
- **Deployment**: The concrete lifecycle unit: run this model with this runtime
  on this host with these placement, storage, runtime, and container overrides.
- **Placement**: A nested deployment concern describing hardware allocation,
  such as selected accelerator IDs. Placement is not a top-level managed entity.
- **Store**: A named host storage mapping, such as `models` or `hf_cache`, with
  host/container paths and access mode. Models refer to stores by name.
- **Resolved Deployment**: The fully assembled launch configuration produced by
  resolving a deployment's model, runtime, host, stores, cascaded defaults, and
  overrides.
- **Adapter**: Backend-specific code that translates Switchyard's resolved
  deployment intent into runtime/container operations.

Technology-specific coding standards are defined separately:

- **Python**: `switchyard-internal/process/PYTHON.md` — Python 3.12 type system,
  TDD workflow, and environment commands.

- Background Processes: Prefer foreground `make` targets such as `make dev`,
  `make stop`, and `make status`.

## Development Workflow

This section defines the Switchyard development workflow. It covers phase
sequencing, governance conventions, quality gates, and artifact management.

> **User** holds the singular authority to advance to the next development
> phase.

### Two-Track Workflow

Switchyard supports two workflow tracks. Choose based on the scope and risk of
the effort.

| Track | When to Use | Artifacts Produced |
|-------|-------------|--------------------|
| **Lightweight** | Bug fixes, single-feature additions, small refactors, documentation updates | PLAN only (requirements inlined) |
| **Standard** | Multi-component features, protocol changes, new services, architectural shifts | PRD -> [CONTEXT] -> [SPEC] -> PLAN -> [PR] |

The Lightweight track collapses Initiation, Research, and Planning into a
single scoping conversation that produces a PLAN with inlined requirements. The
Standard track follows the full phase sequence below. Both tracks share the same
governance conventions: branching, commits, PRs, and quality gates.

### Choosing a Track

- Default to **Lightweight**.
- Escalate to Standard when:
  - The change spans multiple modules or services.
  - External research is needed for new protocols, standards, or libraries.
  - Architecture tradeoffs require explicit decisions beyond the PLAN.
  - The user requests it.

## Workflow Phases

### Lightweight Track

```text
Phase 1  Scoping ......... Brief PLAN with inlined requirements
Phase 2  Implementation .. TDD against PLAN
Phase 3  Closeout ........ User review, commit, PR
```

1. **Scoping** — Discuss the need with the user. Produce a PLAN
   (`SEP-###-02-PLAN-...`) with requirements, work breakdown, and test strategy
   inlined. Skip the PRD and CONTEXT. User approves the PLAN.
2. **Implementation** — Execute the PLAN using TDD. Run linters and type
   checkers. Mark off completed items in the PLAN.
3. **Closeout** — User reviews deliverables. Commit, push, and create a PR.

### Standard Track

```text
Phase A  Initiation .............. PRD stub
Phase B  Research ................ Internal code, external web search
Phase C  Requirements & Planning . Spec draft as needed, then PLAN
Phase D  Implementation .......... TDD against PLAN
Phase E  Final Review ............ Smoke tests and final reviews
Phase F  Closeout ................ User review, commit
```

#### 1. Initiation (PRD)

Collaborate with the user to articulate a statement of need, forming the stub
of a PRD (`SEP-###-01-PRD-...`). "SEP" stands for "Switchyard Enhancement
Proposal".

#### 2. Research & Context Gathering

Create CONTEXT documents (`SEP-###-0#-CONTEXT-...`) for non-trivial efforts.
Each research agent writes to its own CONTEXT file with a unique slug. Increment
the document counter `-0#-` as the next available number 04 or greater. Launch
research agents in parallel when useful.

- **Internal research**: Launch a sub-agent or directly research and populate
  the CONTEXT document. Surface file paths, relevant sections, constraints,
  contradictions, and impacted artifacts. Keep it non-prescriptive; defer
  architecture tradeoffs to Phase 3.
- **External research**: Populate an external research CONTEXT document with
  relevant protocols, standards, and patterns.

Consolidate and finalize CONTEXT documents after research inputs complete.

#### 3. Requirements & Planning

Synthesize research findings into actionable deliverables:

- Refine the PRD: requirements, non-goals, risks, and success criteria. Preserve
  invariants and existing governance decisions. Escalate unresolved tradeoffs to
  user decisions.
- **Spec-first work (protocol SEP)**: If warranted, draft spec documents in
  `switchyard-internal/docs/v0/` before building the PLAN. The spec draft
  defines interfaces, state transitions, formats, and invariants. Draft
  conformance fixtures (valid and invalid) alongside the spec to validate the
  design as needed. Validate the spec draft before proceeding. The PLAN then
  organizes implementation work against those specs.
- Build a PLAN document (`SEP-###-02-PLAN-...`) with work breakdown,
  dependencies, test strategy, and validation expectations.
- User reviews and collaborates on revisions, which may include external
  consultants.
- Finalize both PRD and PLAN, marking them ready for implementation.

#### 4. Test Design & Implementation

Execute the PLAN using TDD:

1. Design comprehensive test coverage; user reviews the test approach.
2. Write failing tests first: unit, integration, and E2E as appropriate.
3. Implement to pass tests.
4. Run linters and type checkers.
5. Update implementation guides with new functionality and examples.
6. Mark off completed items directly in the PLAN.

#### DEVLOG Maintenance

DEVLOGs are concise orientation for future sessions, not implementation
journals. Update them only with context that is not already obvious from the
PLAN checklist.

- Entries should be short phase/session notes. If nothing notable happened,
  record only the completed PLAN section(s).
- Capture information that would otherwise be lost between sessions: surprises,
  validation results, changed assumptions, blockers, or handoff-relevant context.
- Do not repeat task progress already represented by checked items in the PLAN.
- Keep `## Cold Start / Handoff` brief. It should name only the critical files
  needed to continue correctly: usually the PRD, PLAN, and at most one or two
  essential CONTEXT/CONSULT, config, or code files.
- The handoff section routes readers to context; it does not replace the PRD,
  PLAN, CONTEXT, or CONSULT.

#### 5. Final Review & Documentation

- Update impacted specifications, cross-references, and SEP artifacts.
- Synchronize examples with implementation.
- Draft a PR summary artifact when needed (`SEP-###-##-PR-...`).

#### 6. Closeout

1. User reviews deliverables, performs code review, and performs UAT.
2. Commit with a conventional commit message.
3. Push branch and create a PR.
4. User approves and merges PR on GitHub.
5. Post-merge: switch to `main`, pull, and delete the local branch.

## Governance & Conventions

### Branching Conventions

- All SEP-aligned work happens on a branch, never directly on `main`.
- Primary branch format: `sep/XXX` where `XXX` matches the SEP number
  (zero-padded to 3 digits).

### Commit Message Conventions

- Use Conventional Commit style: `feat`, `fix`, `docs`, `chore`, etc.
- Scope reflects the monorepo area that changed:

| Directory | Scope |
|-----------|-------|
| `switchyard-api/` | `api` |
| `switchyard-internal/` | `planning` |
| `AGENTS.md`, `switchyard-internal/process/` | `process` |

- For changes spanning multiple areas, comma-separate scopes:
  `feat(api,web,planning)`.
- SEP artifacts (`switchyard-internal/sep/`) scope to the primary affected area.
- Do not use SEP IDs in commit scope, subjects, or PR titles.
- Keep the subject concise and outcome-focused.
- If a commit body is used, target 3-5 bullets; hard cap at 8 bullets.
- Put operational detail in SEP artifacts, not in commit bodies.
- Do not add `Co-Authored-By`, `Generated with`, or other self-attribution
  trailers.

Examples:

```text
feat(api): apply endpoint migration
feat(web): revise cold start instructions
```

### Pull Request Workflow

- Every SEP branch merges to `main` via pull request; no direct pushes to
  `main`.
- Draft a PR artifact (`SEP-###-##-PR-...`) using
  `switchyard-internal/process/DEV_TEMPLATE_PR.md` for non-trivial efforts. This
  artifact is the canonical draft for the GitHub PR body.
- PR artifact drafts are written during Phase 5 (Final Review). Use the artifact
  content directly in `gh pr create`, for example with `--body-file`, or copy it
  into GitHub.
- Keep PR body content concise and reviewer-focused: summarize changes,
  validation, and risks.
- Do not link or reference PRD/PLAN in the PR body; this is assumed context.
- Create PRs in draft mode when early visibility is useful; otherwise create as
  ready for review.
- PR title follows the same conventions as commit messages.
- User reviews, approves, and merges the PR on GitHub.

Post-merge checklist for the user:

1. Switch to `main`: `git checkout main`.
2. Pull merged changes: `git pull origin main`.
3. Delete the local SEP branch: `git branch -d sep/XXX`.

### Artifact Detail Depth

- `DEVLOG` is concise orientation only: enough context to quickly understand
  current project state.
- `CONTEXT` is the canonical location for deep findings, evidence, and research
  notes.
- `PLAN` is the canonical location for decision logging and task-state tracking.

### Artifact Referencing Conventions

- Never embed developer PII, including local usernames or home directory paths,
  in artifact documents. All file references must be repo-relative from the
  repository root, such as `switchyard-api/pyproject.toml`.
- SEP artifact documents (`SEP-*.md`) are referenced by filename only, wrapped in
  backticks, not markdown links, and live in `switchyard-internal/sep/`.
- All other file references use repo-relative paths from the repository root,
  wrapped in backticks, and must not be markdown links.

### GitHub Issues as Backlog

GitHub Issues are the canonical backlog. When you encounter out-of-scope items
during any phase, such as bugs, tech debt, design gaps, or follow-on work,
propose a GitHub issue to the user rather than deferring silently or embedding
TODOs in code.

When to propose an issue:

- Out-of-scope findings surfaced in CONTEXT, CONSULT, or PLAN artifacts.
- Tech debt or spec inconsistencies discovered during implementation.
- Design questions that do not need to be resolved in the current SEP.
- Follow-on work identified during review.

Labels: Apply scope labels matching commit scopes (`api`, `process`) and type
labels (`bug`, `enhancement`, `tech-debt`, `question`). The `sep-candidate`
label is reserved for user consent prior to assigning, so ask first unless the
user has already approved it.

Issue detail should match complexity. A simple bug or rename can be a one-liner.
A design gap that surfaced from extended discussion should capture the problem
statement, related artifacts, and open questions with enough context to
cold-start a future SEP without rediscovering the problem.

### Decision Logging

- Use the PLAN decision log for project-local execution decisions.
- Decision entries use this schema:
  - `decision`: what was decided.
  - `reason`: why this choice was made.
  - optional `links`: related artifacts.
  - optional `notes`: additional context.
- Decision progression states: `Draft` -> `Pending` -> `Agreed`.
  - `Draft`: proposed, not yet discussed.
  - `Pending`: discussed, awaiting user sign-off.
  - `Agreed`: accepted by user; record date or reference.

## Quality Gates

Python commands run from `switchyard-api/`:

```bash
uv run pytest
uv run ruff check src tests --fix
uv run mypy src/switchyard
```

Additional requirements:

- No regressions in existing functionality.
- All cross-references updated and valid.

## Project Structure & Artifact File Naming

### Repository Layout

```text
switchyard/
├── AGENTS.md
├── Makefile
├── README.md
├── deploy/
│   ├── lxd-profiles/
│   └── systemd/
├── reference-then-delete/
├── switchyard-api/
│   ├── config.yaml
│   ├── pyproject.toml
│   ├── src/switchyard/
│   │   ├── adapters/
│   │   ├── config/
│   │   └── core/
│   └── tests/
└── switchyard-internal/
    ├── planning/
    ├── process/
    │   ├── DEV_TEMPLATE_*.md
    │   └── PYTHON.md
    └── sep/
```

### Project Doc Directory Structure

```text
switchyard-internal/
└── sep/
    ├── SEP-000-01-PRD-feature-name.md
    ├── SEP-000-02-PLAN-feature-name.md
    ├── SEP-000-03-DEVLOG-feature-name.md
    ├── SEP-000-04-[CONTEXT|CONSULT]-feature-name.md
    └── SEP-000-##-[CONTEXT|CONSULT]-feature-name.md
```

### File Naming Convention

Format: `SEP-[SEQ]-[DOC#]-[DOC-TYPE]-[slug].md`

- **SEQ**: `000`, `001`, etc.; the sequential SEP number.
- **DOC#**: Two-digit document index within the SEP.
- **DOC-TYPE**: `PRD`, `PLAN`, `DEVLOG`, `CONTEXT`, `CONSULT`.
- **slug**: Concise kebab-case name for the feature, topic, or focus area.

Fixed positions: `01-PRD`, `02-PLAN`, and `03-DEVLOG`. All other document types
use `04` or the next available index. Multiple documents of the same type are
expected, such as several CONTEXT or CONSULT documents. The index sequence and
unique slug tell the story of the effort.

### Artifact Templates

| Artifact | Template | Lightweight | Standard | Notes |
|----------|----------|-------------|----------|-------|
| PRD | `switchyard-internal/process/DEV_TEMPLATE_PRD.md` | - | Required | Always before PLAN in Standard track |
| PLAN | `switchyard-internal/process/DEV_TEMPLATE_PLAN.md` | Required | Required | Inlines requirements on Lightweight track |
| DEVLOG | `switchyard-internal/process/DEV_TEMPLATE_DEVLOG.md` | Optional | Optional | User-initiated breadcrumb trail |
| CONTEXT | `switchyard-internal/process/DEV_TEMPLATE_CONTEXT.md` | - | As-needed | Each research agent writes its own file with unique slug |
| CONSULT | `switchyard-internal/process/DEV_TEMPLATE_CONSULT.md` | - | As-needed | For third-party consultation |
