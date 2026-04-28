# Future Idea — vLLM Flag Audit Tooling

**Status**: Not planned — future consideration
**Relates to**: SEP-001, `VLLMAdapter`, vLLM config tier system

---

## Idea

Use vLLM's `EngineArgs` class (the backing model for `vllm serve`) as a reference source to diff flag surfaces across vLLM versions. This is a **maintenance audit tool**, not a runtime or build-time dependency.

`EngineArgs` is effectively vLLM's public CLI API — breaking it is a user-facing regression. It is stable enough to reference for this purpose.

---

## What It Solves

When bumping the supported vLLM baseline (e.g. `v0.9.0` → `v0.12.0`), today the process is manual: read the changelog, hope nothing was missed. The audit tool makes this mechanical:

- **New flags** in the target version not in Switchyard's schema → candidates for promotion from `extra_args` to named Pydantic fields
- **Missing flags** in Switchyard's schema not present in the target version → deprecation risks, emit warnings

---

## Sketch

```python
# scripts/audit_vllm_flags.py
# Run manually when bumping the supported vLLM baseline.
# Not part of the application; not a CI gate.
#
# Usage:
#   uv run scripts/audit_vllm_flags.py --from 0.9.0 --to 0.12.0
#
# Requires: vllm installed in a throwaway venv at the target version.
# Suggested: run inside a container matching the target image.

import argparse
from vllm.engine.arg_utils import EngineArgs
from switchyard.adapters.vllm.schema import KNOWN_FIELDS  # Switchyard's named fields

def get_vllm_flags() -> set[str]:
    import dataclasses
    return {f.name for f in dataclasses.fields(EngineArgs)}

def audit(known: set[str], vllm: set[str]) -> None:
    new = vllm - known
    removed = known - vllm

    if new:
        print(f"\nNew flags ({len(new)}) — review for promotion from extra_args:")
        for f in sorted(new):
            print(f"  + {f}")

    if removed:
        print(f"\nRemoved flags ({len(removed)}) — check for deprecated named fields:")
        for f in sorted(removed):
            print(f"  - {f}")

    if not new and not removed:
        print("No drift detected.")
```

---

## Key Constraints

- **Dev tooling only** — vLLM is never a dependency of the Switchyard application
- **Run in a matching container** to ensure the inspected `EngineArgs` matches the target image exactly
- **Output is advisory** — new flags are candidates, not automatic promotions; human review required before adding named Pydantic fields
- **Trigger**: only when you decide to move a model to a newer vLLM image; not on every release

---

## When This Becomes Worth Building

When the cost of manual changelog review exceeds the cost of building and maintaining the script. Given image pinning per model, vLLM bumps are infrequent and deliberate — the manual process is probably sufficient until the adapter count grows or vLLM's release cadence accelerates further.
