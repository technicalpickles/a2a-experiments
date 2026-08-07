"""Task lifecycle beyond the happy stream: fetch-after-the-fact, and cancel."""

from __future__ import annotations

import pytest
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskState

from a2a_rig.events import send, state_name


async def test_get_task_after_completion(client, simple_prompt):
    capture = await send(client, simple_prompt)

    task = await client.get_task(GetTaskRequest(id=capture.task_id))

    assert task.id == capture.task_id
    assert state_name(task.status.state) == "completed"


async def test_get_task_preserves_context(client, simple_prompt):
    capture = await send(client, simple_prompt)

    task = await client.get_task(GetTaskRequest(id=capture.task_id))

    assert task.context_id == capture.context_id


async def test_get_task_retains_artifacts(client, simple_prompt, reply_marker):
    """A client reconnecting after a dropped stream should still see output."""
    capture = await send(client, simple_prompt)

    task = await client.get_task(GetTaskRequest(id=capture.task_id))

    joined = "".join(p.text for a in task.artifacts for p in a.parts if p.text)
    assert reply_marker in joined


# Canceling a task parked on input-required does not take: the call returns
# successfully, but the task stays input_required both in the response and on a
# later tasks/get. Traced through all three layers:
#
#   Protocol: fine. input-required is an interrupted, non-terminal state, and
#   TaskNotCancelableError exists precisely to distinguish terminal ones. The
#   spec expects this to work.
#
#   a2acode: parks by *returning* from execute() (executor.py, "Paused on a
#   permission request; keep the stream for the follow-up"), keeping the
#   BackendSession alive out of band. Deliberate — it is what lets one round
#   trip span two execute() calls — but it means the producer looks finished.
#
#   a2a-sdk: ActiveTask.cancel (active_task.py) only acts `if not
#   self._is_finished.is_set() and self._producer_task`, else logs "not
#   cancelling" and returns the task untouched. It models cancelable as "has a
#   running producer" rather than "is not terminal". Worse, V2's
#   on_cancel_task dropped the guard V1 had —
#   `if result.status.state != TASK_STATE_CANCELED: raise TaskNotCancelableError`
#   — so the mismatch surfaces as a successful response instead of an error.
#   (DefaultRequestHandler is aliased to V2; the V1 file is dead code.)
#
# So the silent success is an a2a-sdk V2 regression; a2acode's design choice is
# what walks into it. strict=True so these flip loudly if either side fixes it.
# Matters for any UI offering "cancel" on an approval prompt: today that lies.
cancel_of_parked_task_is_broken = pytest.mark.xfail(
    strict=True,
    reason="a2a-sdk V2: cancel no-ops on a task whose producer already returned",
)


@cancel_of_parked_task_is_broken
async def test_cancel_a_parked_task(client, permission_prompt):
    """A task waiting on input is the reliably-cancellable state: it sits
    there until answered, so there is no race with completion."""
    parked = await send(client, permission_prompt)
    assert parked.final_state == "input_required"

    task = await client.cancel_task(CancelTaskRequest(id=parked.task_id))

    assert task.status.state == TaskState.TASK_STATE_CANCELED


@cancel_of_parked_task_is_broken
async def test_cancel_is_visible_to_a_later_get(client, permission_prompt):
    parked = await send(client, permission_prompt)
    await client.cancel_task(CancelTaskRequest(id=parked.task_id))

    task = await client.get_task(GetTaskRequest(id=parked.task_id))

    assert state_name(task.status.state) == "canceled"


async def test_cancel_of_a_parked_task_reports_success(client, permission_prompt):
    """Documents today's behavior, so the contrast with the xfails above is
    explicit: the call succeeds, it just has no effect."""
    parked = await send(client, permission_prompt)

    task = await client.cancel_task(CancelTaskRequest(id=parked.task_id))

    assert task.id == parked.task_id
    assert state_name(task.status.state) == "input_required"


async def test_cancelling_a_finished_task_errors(client, simple_prompt):
    """Terminal tasks are not cancellable; the server should say so rather
    than silently accepting."""
    capture = await send(client, simple_prompt)

    with pytest.raises(Exception):
        await client.cancel_task(CancelTaskRequest(id=capture.task_id))
