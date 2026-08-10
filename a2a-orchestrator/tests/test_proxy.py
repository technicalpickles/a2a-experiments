"""The conversation plane: genuine A2A through the proxy, against real fakes.

The client is the python a2a-sdk — the same protocol surface the browser's
a2a-js client drives — and it follows the same two-step the browser does:
fetch the card from the proxied base, then speak JSON-RPC+SSE to whatever URL
the card advertises. If the card rewrite is wrong, every test below escapes
the proxy and fails; that is the point.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from a2a.client import create_client
from a2a.client.client import ClientConfig
from a2a_rig.events import send


@pytest.fixture
def connect(service_url, http):
    async def _connect(chat: dict):
        base = f"{service_url.rstrip('/')}{chat['a2a_url']}"
        return await create_client(
            base, ClientConfig(streaming=True, httpx_client=http)
        )

    return _connect


async def test_card_is_rewritten_to_the_proxy(
    service_url, http, mission, open_chat, rig_url
):
    chat = await open_chat(mission["id"], "billing-api")

    card_url = f"{service_url.rstrip('/')}{chat['a2a_url']}.well-known/agent-card.json"
    response = await http.get(card_url)

    assert response.status_code == 200
    rig_port = urlsplit(rig_url).port
    assert f"127.0.0.1:{rig_port}" not in response.text
    assert f"localhost:{rig_port}" not in response.text
    assert chat["a2a_url"] in response.text


async def test_free_text_round_trips_over_a2a(mission, open_chat, connect):
    chat = await open_chat(mission["id"], "billing-api")
    client = await connect(chat)

    capture = await send(client, "hello from the cockpit")

    assert capture.final_state == "completed"
    assert "Ready when you are" in capture.artifact_text()


async def test_upstream_adopts_the_service_minted_context(
    mission, open_chat, connect
):
    """The wire check the spec's open question asked for: the service mints
    the contextId at chat-open, the client sends it on turn one, and the
    upstream adopts it rather than replacing it."""
    chat = await open_chat(mission["id"], "billing-api")
    client = await connect(chat)

    capture = await send(client, "hello", context_id=chat["context_id"])

    assert capture.final_state == "completed"
    assert capture.context_id == chat["context_id"]


async def test_turns_share_the_context_across_sends(mission, open_chat, connect):
    chat = await open_chat(mission["id"], "billing-api")
    client = await connect(chat)

    first = await send(client, "hello", context_id=chat["context_id"])
    second = await send(client, "hello again", context_id=chat["context_id"])

    assert first.context_id == chat["context_id"]
    assert second.context_id == chat["context_id"]
    assert first.task_id != second.task_id


async def test_approval_round_trips_through_the_proxy(mission, open_chat, connect):
    chat = await open_chat(mission["id"], "billing-api")
    client = await connect(chat)

    parked = await send(
        client, "please run the tests", context_id=chat["context_id"]
    )

    assert parked.final_state == "input_required"
    assert parked.permission is not None
    assert parked.permission["tool"] == "Bash"

    resumed = await send(
        client, "allow", task_id=parked.task_id, context_id=parked.context_id
    )

    assert resumed.final_state == "completed"


async def test_two_chats_route_to_their_own_repos(mission, open_chat, connect):
    billing = await open_chat(mission["id"], "billing-api")
    checkout = await open_chat(mission["id"], "checkout-web")

    billing_reply = await send(
        await connect(billing), "what is this repo?",
        context_id=billing["context_id"],
    )
    checkout_reply = await send(
        await connect(checkout), "what is this repo?",
        context_id=checkout["context_id"],
    )

    assert "Ready when you are" in billing_reply.artifact_text()
    assert "checkout flow" in checkout_reply.artifact_text()


async def test_upstream_failure_relays_as_a_failed_task(
    mission, open_chat, connect
):
    chat = await open_chat(mission["id"], "infra-terraform")

    capture = await send(
        await connect(chat), "status check please", context_id=chat["context_id"]
    )

    assert capture.final_state == "failed"


async def test_unbound_context_404s(service_url, http):
    response = await http.post(
        f"{service_url}a2a/chats/deadbeef/",
        json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {}},
    )
    assert response.status_code == 404
    assert "deadbeef" in response.json()["error"]
