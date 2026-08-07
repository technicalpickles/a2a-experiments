"""Lifespan propagation for mounted sub-apps.

Starlette runs the lifespan of the app being served and of nothing mounted
inside it. a2acode's `build_app()` initializes its task and push-notification
stores in a lifespan, so a mounted repo whose lifespan never ran would answer
requests against uninitialized stores. Serving N repos from one process
therefore means running N lifespans by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any


def mount_lifespans(children: list[Any]) -> Callable:
    """A parent lifespan that runs every child app's lifespan.

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
