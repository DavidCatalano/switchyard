"""Deep merge utility for cascade configuration resolution.

Provides a shared deep-merge function used by both the config loader
(cascade resolution) and the app bootstrap (test config overrides).

Extracted from ``app.py`` to avoid import cycles between ``app.py``
and ``config/loader.py`` (SEP-004 Phase 2).

This is a **pure structural merge** with no null semantics. The caller
decides how to handle ``None`` values before or after merging.
"""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*.

    Rules:
      - Nested dicts are recursively merged.
      - All other values (including ``None``) from *override* replace *base*.
      - No value inspection or null semantics — the caller controls policy.

    Returns a new dict (neither input is mutated).
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
