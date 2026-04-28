# Repository Guidelines

This repo hosts Switchyard an API control plan for managing various model runtimes and model configurations.

## Development Workflow
Always follow `switchyard-internal/process/DEV.md` for the primary development workflow. 

## Coding Standards

Technology-specific coding standards are defined in files. These are auto-loaded by Claude Code but must be read explicitly by other agents:

- **Python**: `switchyard-internal/process/PYTHON.md` — Python 3.12 type system, TDD workflow, environment commands

## Commit & Pull Request Guidelines
Follow governance conventions in `switchyard-internal/process/DEV.md` (branching, commit messages, artifact detail depth). PRs include description, linked issues, test plan, and screenshots/GIFs for UI changes.

## Agent Workflow Preferences
- MCP Playwright: Prefer the MCP Playwright tool for browser checks and app verification. Do not add Playwright configs/tests or modify `package.json` for Playwright without explicit approval.
- E2E Runs: Avoid introducing local Playwright scaffolding by default. Use MCP-driven checks or manual verification.
- Logging: Do not write `.log`/`.pid` files by default.
- Background Processes: Prefer foreground `make` targets (e.g., `make dev`, `make stop`, `make status`).
