"""Context continuity: many tasks, one conversation."""

from __future__ import annotations

from a2a_rig.events import send


async def test_second_turn_reuses_the_context(client, simple_prompt):
    first = await send(client, simple_prompt)

    second = await send(client, f"{simple_prompt} again", context_id=first.context_id)

    assert second.context_id == first.context_id


async def test_each_turn_gets_its_own_task_id(client, simple_prompt):
    """Observed against real claude too: a resumed conversation still opens a
    new task, so clients must key on context, not task, for history."""
    first = await send(client, simple_prompt)

    second = await send(client, f"{simple_prompt} again", context_id=first.context_id)

    assert second.task_id
    assert second.task_id != first.task_id


async def test_turns_complete_independently(client, simple_prompt):
    first = await send(client, simple_prompt)
    second = await send(client, f"{simple_prompt} again", context_id=first.context_id)

    assert first.final_state == "completed"
    assert second.final_state == "completed"
