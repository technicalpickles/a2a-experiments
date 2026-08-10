"""Fixtures: a playback rig serving the fake repos, driven over the wire.

The pattern is a2a-rig's own harness (spawn a real subprocess, poll ready,
share it session-wide) — booting costs ~0.5s and tasks are isolated by id,
so sharing is safe. The service fixture joins this file in the missions-api
task; this half is just the substrate.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from a2a_rig.server import free_port, serve as rig_serve

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RIG_REPOS = PROJECT_ROOT.parent / "a2a-rig" / "repos"

SERVICE_STARTUP_TIMEOUT_S = 30.0


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


@pytest.fixture(scope="session")
def service_url(rig_url, tmp_path_factory) -> str:
    """orch-serve as a real subprocess, cataloged against the session rig."""
    workdir = tmp_path_factory.mktemp("orchestrator")
    catalog = workdir / "catalog.yaml"
    catalog.write_text(f"provider: index\nurl: {rig_url}\n")
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "a2a_orchestrator.serve",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--db", str(workdir / "orchestrator.db"),
            "--catalog", str(catalog),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + SERVICE_STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"orch-serve exited early:\n{proc.stdout.read()}")
        try:
            if httpx.get(f"{url}api/missions", timeout=2.0).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        proc.terminate()
        raise TimeoutError("orch-serve did not come up in time")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest_asyncio.fixture
async def mission(service_url, http) -> dict:
    response = await http.post(f"{service_url}api/missions", json={})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def open_chat(service_url, http):
    async def _open(mission_id: str, agent: str) -> dict:
        response = await http.post(
            f"{service_url}api/missions/{mission_id}/chats", json={"agent": agent}
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _open
