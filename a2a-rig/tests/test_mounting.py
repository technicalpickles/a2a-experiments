"""Mounted sub-apps do not get their lifespans run for free.

Starlette runs the lifespan of the app it is serving, not of anything mounted
inside it. a2acode's task and push-notification stores are constructed
eagerly, not in the lifespan, so a mounted repo whose lifespan never ran still
answers requests fine — what it never gets is a clean shutdown: the
lifespan's `finally` block is what closes the backend and the
push-notification client. This pins the propagation helper that fixes that.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.applications import Starlette

from a2a_playback.mounting import mount_lifespans


def _recording_app(log: list[str], name: str) -> Starlette:
    @asynccontextmanager
    async def lifespan(_app):
        log.append(f"start:{name}")
        try:
            yield
        finally:
            # try/finally, not a bare `yield` then append: unwinding throws the
            # exception in *at* the yield, so anything after it is skipped and
            # the shutdown this test is checking for would never be recorded.
            log.append(f"stop:{name}")

    return Starlette(lifespan=lifespan)


async def test_a_parent_runs_every_child_lifespan():
    log: list[str] = []
    children = [_recording_app(log, "a"), _recording_app(log, "b")]
    parent = Starlette(lifespan=mount_lifespans(children))

    async with parent.router.lifespan_context(parent):
        assert log == ["start:a", "start:b"]

    assert log == ["start:a", "start:b", "stop:b", "stop:a"]


async def test_a_child_that_fails_to_start_does_not_strand_its_siblings():
    """Otherwise a bad repo leaves the already-started ones un-shut-down."""
    log: list[str] = []

    @asynccontextmanager
    async def boom(_app):
        raise RuntimeError("child refused to start")
        yield  # pragma: no cover

    children = [_recording_app(log, "a"), Starlette(lifespan=boom)]
    parent = Starlette(lifespan=mount_lifespans(children))

    try:
        async with parent.router.lifespan_context(parent):
            pass
    except RuntimeError:
        pass

    assert log == ["start:a", "stop:a"]
