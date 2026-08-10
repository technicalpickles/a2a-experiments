"""Fixtures: a playback rig serving the fake repos, driven over the wire.

The pattern is a2a-rig's own harness (spawn a real subprocess, poll ready,
share it session-wide) — booting costs ~0.5s and tasks are isolated by id,
so sharing is safe. The service fixture joins this file in the missions-api
task; this half is just the substrate.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from a2a_rig.server import serve as rig_serve

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RIG_REPOS = PROJECT_ROOT.parent / "a2a-rig" / "repos"


@pytest.fixture(scope="session")
def rig_url() -> str:
    """One playback rig serving a2a-rig's repo directory, shared session-wide."""
    with rig_serve(backend="playback", repos=RIG_REPOS) as url:
        yield url


@pytest_asyncio.fixture
async def http():
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        yield client
