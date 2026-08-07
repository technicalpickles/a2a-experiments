"""Lifespan propagation for mounted sub-apps.

Starlette runs the lifespan of the app being served and of nothing mounted
inside it. a2acode's `build_app()` constructs its task and push-notification
stores eagerly — the lifespan only calls `.initialize()` on them when a
database engine is configured (`--task-db`), which this rig never passes, so
an un-run child lifespan costs nothing at startup. What it does cost is
shutdown: the lifespan's `finally` block is what closes the push-notification
client and the backend, and a lifespan that never runs never runs that either.
Serving N repos from one process therefore means running N lifespans by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any


def mount_lifespans(children: list[Any]) -> Callable:
    """A parent lifespan that runs every child app's lifespan.

    Runs each child's startup for symmetry with a normally-served app, but
    what actually matters is shutdown — see the module docstring for why.

    An `AsyncExitStack` rather than a loop of `__aenter__` calls: if one child
    raises on startup, the stack unwinds the ones already started instead of
    leaving them running with nobody to shut them down.
    """

    @asynccontextmanager
    async def lifespan(_app):
        async with AsyncExitStack() as stack:
            for child in children:
                await stack.enter_async_context(
                    child.router.lifespan_context(child)
                )
            yield

    return lifespan
