"""The multi-repo rig: one process, N repos, one index.

The index is the contract. A consumer that reads a list of card URLs cannot
tell N mounted paths from N standalone ports, which is what lets the rig
change topology without breaking anything built on it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from a2a.client import create_client
from a2a.client.client import ClientConfig

from a2a_playback.repo import load_repos
from a2a_playback.serve import index_document
from a2a_rig.events import send
from a2a_rig.server import serve

REPOS = Path(__file__).parent / "repos"

pytestmark = pytest.mark.backend("playback")


@pytest.fixture(scope="session")
def rig_url():
    with serve(backend="playback", repos=REPOS) as url:
        yield url


@pytest_asyncio.fixture
async def index(rig_url, http_client):
    response = await http_client.get(rig_url)
    return response.json()


def test_the_index_lists_every_repo_in_the_directory():
    repos = load_repos(REPOS)
    document = index_document(repos, "http://127.0.0.1:9200/")

    assert [entry["name"] for entry in document["repos"]] == [
        r.repo_id for r in repos
    ]


def test_card_urls_are_absolute():
    """So the same document describes N ports as easily as N paths."""
    document = index_document(load_repos(REPOS), "http://127.0.0.1:9200/")

    for entry in document["repos"]:
        assert entry["card_url"].startswith("http://127.0.0.1:9200/repos/")
        assert entry["card_url"].endswith("/.well-known/agent-card.json")


async def test_the_served_index_names_the_test_repos(index):
    assert {"vocabulary", "strict"} <= {e["name"] for e in index["repos"]}


async def test_every_advertised_card_is_reachable(index, http_client):
    """The mounted topology's load-bearing assumption, over the wire."""
    for entry in index["repos"]:
        card = (await http_client.get(entry["card_url"])).json()
        assert card["name"] == entry["name"]


async def test_the_rig_itself_is_not_an_agent(rig_url, http_client):
    """No card at the root: the rig is a directory of agents, not one."""
    response = await http_client.get(
        f"{rig_url.rstrip('/')}/.well-known/agent-card.json"
    )

    assert response.status_code == 404


async def test_a_mounted_repo_answers_a_real_turn(rig_url, http_client):
    """Proves a card carrying an absolute mounted url round-trips: the client
    reads it and posts JSON-RPC back to the mounted path, not the host root."""
    base = f"{rig_url.rstrip('/')}/repos/vocabulary/"
    client = await create_client(
        base, ClientConfig(streaming=True, httpx_client=http_client)
    )

    capture = await send(client, "hit the ceiling")

    assert capture.completion_metadata.get("stop_reason") == "max_tokens"


async def test_two_repos_serve_different_content(rig_url, http_client):
    """The actual claim of the milestone. Everything else is plumbing."""
    vocabulary = await create_client(
        f"{rig_url.rstrip('/')}/repos/vocabulary/",
        ClientConfig(streaming=True, httpx_client=http_client),
    )
    strict = await create_client(
        f"{rig_url.rstrip('/')}/repos/strict/",
        ClientConfig(streaming=True, httpx_client=http_client),
    )

    answered = await send(strict, "the known question")
    unmatched = await send(vocabulary, "the known question")

    assert "A scripted answer." in answered.artifact_text()
    assert unmatched.final_state == "failed"
