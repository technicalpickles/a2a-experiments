"""Service-side A2A conversations, driven straight against the rig (no service)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from a2a_orchestrator.a2a_client import Conversations
from a2a_orchestrator.translate import Turn


@dataclass
class FakeChat:
    context_id: str
    upstream_url: str


@pytest.fixture
async def billing_chat(rig_url, http):
    index = (await http.get(rig_url)).json()
    entry = next(e for e in index["repos"] if e["name"] == "billing-api")
    base = entry["card_url"].removesuffix(".well-known/agent-card.json")
    return FakeChat(context_id=uuid.uuid4().hex, upstream_url=base)


async def drain(events):
    return [event async for event in events]


async def test_free_text_round_trips(billing_chat, http):
    conversations = Conversations(http)
    events = await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="hello from the cockpit"))
    )
    kinds = [e.WhichOneof("payload") for e in events]
    assert "task" in kinds
    assert "artifact_update" in kinds


async def test_upstream_adopts_the_minted_context(billing_chat, http):
    conversations = Conversations(http)
    events = await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="hello"))
    )
    task = next(e.task for e in events if e.WhichOneof("payload") == "task")
    assert task.context_id == billing_chat.context_id


async def test_resume_targets_the_parked_task(billing_chat, http):
    conversations = Conversations(http)
    parked = await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="please run the tests"))
    )
    task_id = next(e.task.id for e in parked if e.WhichOneof("payload") == "task")
    conversations.park(billing_chat.context_id, task_id)
    assert conversations.parked_task(billing_chat.context_id) == task_id

    resumed = await drain(
        conversations.run_turn(billing_chat, Turn(kind="resume", text="allow"))
    )
    resumed_task_ids = {
        e.status_update.task_id
        for e in resumed
        if e.WhichOneof("payload") == "status_update"
    }
    assert resumed_task_ids == {task_id}
    conversations.clear(billing_chat.context_id)
    assert conversations.parked_task(billing_chat.context_id) is None


async def test_resume_with_nothing_parked_refuses(billing_chat, http):
    conversations = Conversations(http)
    with pytest.raises(LookupError, match=billing_chat.context_id):
        await drain(conversations.run_turn(billing_chat, Turn(kind="resume", text="allow")))
