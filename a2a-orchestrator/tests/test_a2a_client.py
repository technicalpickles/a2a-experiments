"""Service-side A2A conversations, driven straight against the rig (no service)."""

from __future__ import annotations

import pytest

from a2a_orchestrator.a2a_client import Conversations
from a2a_orchestrator.store import Store
from a2a_orchestrator.translate import Turn


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "orch.db")


@pytest.fixture
async def billing_chat(rig_url, http, store):
    index = (await http.get(rig_url)).json()
    entry = next(e for e in index["repos"] if e["name"] == "billing-api")
    base = entry["card_url"].removesuffix(".well-known/agent-card.json")
    mission = store.create_mission()
    return store.create_chat(mission.id, "billing-api", base)


async def drain(events):
    return [event async for event in events]


async def test_free_text_round_trips(billing_chat, http, store):
    conversations = Conversations(http, store)
    events = await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="hello from the cockpit"))
    )
    kinds = [e.WhichOneof("payload") for e in events]
    assert "task" in kinds
    assert "artifact_update" in kinds


async def test_upstream_adopts_the_minted_context(billing_chat, http, store):
    conversations = Conversations(http, store)
    events = await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="hello"))
    )
    task = next(e.task for e in events if e.WhichOneof("payload") == "task")
    assert task.context_id == billing_chat.context_id


async def test_resume_targets_the_pending_task(billing_chat, http, store):
    conversations = Conversations(http, store)
    pending = await drain(
        conversations.run_turn(
            billing_chat, Turn(kind="message", text="please run the tests")
        )
    )
    task_id = next(e.task.id for e in pending if e.WhichOneof("payload") == "task")
    conversations.set_pending(billing_chat.context_id, task_id, "req-1", "{}")
    assert conversations.pending_of(billing_chat.context_id).task_id == task_id

    resumed = await drain(
        conversations.run_turn(
            billing_chat, Turn(kind="resume", text="allow", tool_call_id="req-1")
        )
    )
    resumed_task_ids = {
        e.status_update.task_id
        for e in resumed
        if e.WhichOneof("payload") == "status_update"
    }
    assert resumed_task_ids == {task_id}
    conversations.clear_pending(billing_chat.context_id)
    assert conversations.pending_of(billing_chat.context_id) is None


async def test_resume_answering_the_wrong_call_refuses(billing_chat, http, store):
    conversations = Conversations(http, store)
    conversations.set_pending(billing_chat.context_id, "t1", "req-1", "{}")
    with pytest.raises(ValueError, match="pending"):
        await drain(
            conversations.run_turn(
                billing_chat, Turn(kind="resume", text="allow", tool_call_id="req-x")
            )
        )
    assert conversations.pending_of(billing_chat.context_id) is not None


async def test_request_id_outranks_the_tool_call_id(billing_chat, http, store):
    """A re-armed card carries a fresh toolCallId; the request_id still matches."""
    conversations = Conversations(http, store)
    pending = await drain(
        conversations.run_turn(
            billing_chat, Turn(kind="message", text="please run the tests")
        )
    )
    task_id = next(e.task.id for e in pending if e.WhichOneof("payload") == "task")
    conversations.set_pending(billing_chat.context_id, task_id, "req-1", "{}")
    resumed = await drain(
        conversations.run_turn(
            billing_chat,
            Turn(kind="resume", text="allow",
                 tool_call_id="freshly-minted", request_id="req-1"),
        )
    )
    assert resumed


async def test_clear_pending_evicts_the_cached_client(billing_chat, http, store):
    conversations = Conversations(http, store)
    await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="hello"))
    )
    assert billing_chat.context_id in conversations._clients
    conversations.clear_pending(billing_chat.context_id)
    assert billing_chat.context_id not in conversations._clients


async def test_resume_with_nothing_pending_refuses(billing_chat, http, store):
    conversations = Conversations(http, store)
    with pytest.raises(LookupError, match=billing_chat.context_id):
        await drain(conversations.run_turn(billing_chat, Turn(kind="resume", text="allow")))
