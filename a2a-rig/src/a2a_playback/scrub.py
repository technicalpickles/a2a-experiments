"""Making a recording fit to check in.

Narrow and mechanical on purpose: absolute paths leak the machine that made
the recording and are the one redaction that is universal, unambiguous, and
safe to do without asking. Everything else is a human read-through, named as
a step in the runbook rather than left to hope.

No configurable rule surface. For a handful of recordings against a throwaway
app, that would be a config format to design, document, and test for no
benefit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _forms(cwd: str) -> list[str]:
    """Every spelling of the working directory a backend might emit.

    An agent may report the path it was given, or the symlink-resolved one.
    Longest first, so replacing the resolved form never leaves a fragment of
    a longer match behind.
    """
    raw = os.path.expanduser(cwd)
    candidates = {raw.rstrip("/"), str(Path(raw).resolve()).rstrip("/")}
    return sorted((c for c in candidates if c), key=len, reverse=True)


def scrub_cwd(value: Any, cwd: str) -> Any:
    """Replace the working-directory prefix with `.` throughout a structure.

    Walks dicts, lists, and strings; leaves numbers, booleans, and None alone,
    so cost_usd/usage/num_turns come through untouched. Returns new objects
    rather than mutating, because the caller still holds the live events.
    """
    forms = _forms(cwd)

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            for form in forms:
                node = node.replace(form, ".")
            return node
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(value)
