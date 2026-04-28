---
globs:
  - "switchyard-api/**"
---

# Python Development Standards (Python 3.12)

## Type System Requirements
**Use native syntax**: `str | None`, `dict[str, Any]`, `list[str]`, `set[str]`
**Import `Callable`** from `collections.abc`, not `typing`
**Allowed from typing**: Only `Any`, `Protocol`, `TypeVar`, `Generic`, `Literal`, `Final`, `overload`
**Forbidden imports**: `Optional`, `Union`, `Dict`, `List`, `Set`, `Tuple`, `Type` from typing

### Pydantic Model Discipline
- **`dict[str, Any]` is only for genuinely open payloads** — application-defined state, opaque metadata, command params, result data. If a JSON Schema defines required fields, enums, nested objects, or `additionalProperties: false`, model it with a Pydantic class.
- **Reuse existing models for symmetric types** — when building parallel namespaces (e.g., `service/*` mirroring `client/*`), import and reuse the existing typed models (e.g., `ToolDefinition`) rather than creating looser duplicates.
- **Schema `required` → Pydantic `...`** — if a field is required in the JSON Schema, use `Field(...)` not `Field(None)`. If the schema says `uniqueItems: true`, add a `field_validator` to enforce it.
- **Validate by building** — when testing payload models, construct them with invalid nested data and assert `ValidationError`. If a malformed nested object is silently accepted, the model is too permissive.

## Development Process
1. **Type-first development**: Define type annotations before implementation
2. **Library verification**: Consult documentation for generic type requirements and optional parameter behavior
3. **Null safety**: Add proper null checks for all optional parameters before method calls
4. **Continuous validation**: Run mypy after every significant code change

## Test-Driven Development
1. Write failing test first
2. Write minimal code to pass
3. Refactor while keeping tests green

Every project requires: unit tests, integration tests, end-to-end tests. Test output must be pristine to pass.

## Code Quality Standards
- Comments describe what is, not what was (evergreen)
- Start files with docstrings explaining purpose
- Fix bugs incrementally, never rewrite without permission
- Use evergreen naming (avoid 'improved', 'new', 'enhanced')
- Validate assumptions about library behavior with documentation

## Environment
- All Python commands use `uv run` from the specific subproject directory
- API: run from `switchyard-api/`
