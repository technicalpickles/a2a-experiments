"""Fixtures: an a2acode server on a free port, and an A2A client pointed at it.

The ``backend`` fixture is the seam this whole harness exists for. Today it
yields ``echo``; when the playback backend lands, pointing it there should turn
every test below green without touching a test body. Override per-run with
``--backend``, or per-test with ``@pytest.mark.backend("claude")``.
"""

from __future__ import annotations

from contextlib import ExitStack

import httpx
import pytest
import pytest_asyncio
from a2a.client import create_client
from a2a.client.client import ClientConfig

from a2a_rig.server import serve


def pytest_addoption(parser):
    parser.addoption(
        "--backend",
        default="echo",
        help="a2acode backend under test (echo, playback, claude, acp).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "backend(name): pin a test to one a2acode backend"
    )


@pytest.fixture(scope="session")
def default_backend(request) -> str:
    return request.config.getoption("--backend")


@pytest.fixture
def backend(request, default_backend) -> str:
    marker = request.node.get_closest_marker("backend")
    return marker.args[0] if marker else default_backend


@pytest.fixture
def server_args() -> list[str]:
    """Extra `a2acode serve` flags. Override in a test module to customize."""
    return []


@pytest.fixture(scope="session")
def _server_pool():
    """One server per distinct (backend, args), reused across tests.

    Booting a2acode costs ~0.5s, which dominates the suite if every test pays
    it. Tasks are isolated by id, so sharing a server between tests is safe;
    anything that genuinely needs a pristine process can depend on
    `fresh_server_url` instead.
    """
    pool: dict[tuple, object] = {}
    stack = ExitStack()
    try:

        def get(backend: str, args: list[str]) -> str:
            key = (backend, tuple(args))
            if key not in pool:
                pool[key] = stack.enter_context(serve(backend=backend, extra_args=args))
            return pool[key]

        yield get
    finally:
        stack.close()


@pytest.fixture
def server_url(_server_pool, backend, server_args) -> str:
    return _server_pool(backend, server_args)


@pytest.fixture
def fresh_server_url(backend, server_args):
    """A server nobody else has touched, for tests that care about process state."""
    with serve(backend=backend, extra_args=server_args) as url:
        yield url


@pytest_asyncio.fixture
async def http_client():
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        yield client


@pytest_asyncio.fixture
async def client(server_url, http_client):
    a2a_client = await create_client(
        server_url, ClientConfig(streaming=True, httpx_client=http_client)
    )
    yield a2a_client


# --- Backend-specific stimuli -------------------------------------------------
#
# The test bodies below assert protocol behavior, which is backend-independent.
# What differs per backend is which prompt provokes it. Keeping that in fixtures
# is what lets Phase 4 point the suite at `playback` by authoring a scenario
# that answers these prompts, without editing a single test.


@pytest.fixture
def simple_prompt(backend) -> str:
    """A prompt whose text comes back in the response artifact."""
    return "hello world"


@pytest.fixture
def permission_prompt(backend) -> str:
    """A prompt that parks the task on a tool-permission request."""
    if backend == "echo":
        return "sudo rm -rf /tmp/nothing"
    raise NotImplementedError(f"no permission-provoking prompt for backend {backend!r}")


@pytest.fixture
def permission_tool(backend) -> str:
    """The tool name the parked permission request should name."""
    return "Bash"


@pytest.fixture
def denied_marker(backend) -> str:
    """Text the final artifact carries when a tool was denied."""
    return "permission denied"


@pytest.fixture
def tool_marker(backend) -> str:
    """A tool name expected to show up in working-status text."""
    return "Echo" if backend == "echo" else "Bash"


@pytest.fixture
def card(server_url) -> dict:
    url = f"{server_url.rstrip('/')}/.well-known/agent-card.json"
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    return response.json()
