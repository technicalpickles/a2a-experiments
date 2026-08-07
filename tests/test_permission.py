"""The input-required round trip, both branches.

These are the tests that are impossible to run reliably against live inference —
whether a real model asks permission is a judgment call, not a guarantee — which
is most of the argument for the playback backend existing at all.
"""

from __future__ import annotations

import pytest

from a2a_rig.events import send


async def test_prompt_parks_in_input_required(client, permission_prompt):
    capture = await send(client, permission_prompt)

    assert capture.final_state == "input_required"


async def test_permission_payload_is_on_the_status_message(
    client, permission_prompt, permission_tool
):
    """The renderable detail lives in metadata, not the text part.

    Confirmed against a real claude run too, where the text part was only
    "Permission requested for Edit: Edit" while metadata held the tool args.
    """
    capture = await send(client, permission_prompt)

    permission = capture.permission
    assert permission is not None, "expected a2acode_permission metadata"
    assert permission["tool"] == permission_tool
    assert permission["request_id"]
    assert permission["input"]


async def test_allow_resumes_to_completion(client, permission_prompt):
    parked = await send(client, permission_prompt)

    resumed = await send(
        client, "allow", task_id=parked.task_id, context_id=parked.context_id
    )

    assert resumed.final_state == "completed"


async def test_deny_completes_without_running(client, permission_prompt, denied_marker):
    parked = await send(client, permission_prompt)

    resumed = await send(
        client, "deny", task_id=parked.task_id, context_id=parked.context_id
    )

    assert resumed.final_state == "completed"
    assert denied_marker in resumed.artifact_text()


@pytest.mark.parametrize(
    "answer", ["allow", "yes", "y", "approve", "ok", "accept", "grant"]
)
async def test_allow_synonyms(client, permission_prompt, denied_marker, answer):
    """a2acode matches a fixed vocabulary (executor.py); pin it so a
    narrowing upstream change surfaces here rather than in a frontend."""
    parked = await send(client, permission_prompt)

    resumed = await send(
        client, answer, task_id=parked.task_id, context_id=parked.context_id
    )

    assert denied_marker not in resumed.artifact_text()


async def test_unrecognized_answer_is_a_denial(client, permission_prompt, denied_marker):
    """There is no third option: anything that is not an allow word denies,
    and the caller's own wording is discarded rather than passed to the agent."""
    parked = await send(client, permission_prompt)

    resumed = await send(
        client, "hmm maybe", task_id=parked.task_id, context_id=parked.context_id
    )

    assert denied_marker in resumed.artifact_text()
