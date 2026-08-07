"""Stream shape: what a client can rely on arriving, and in what order."""

from __future__ import annotations

from a2a_rig.events import send


async def test_task_arrives_before_any_update(client, simple_prompt):
    """A client needs task/context ids before it can render anything else."""
    capture = await send(client, simple_prompt)

    assert capture.kinds[0] == "task"
    assert "task" not in capture.kinds[1:], "task envelope should be sent once"


async def test_ids_are_populated(client, simple_prompt):
    capture = await send(client, simple_prompt)

    assert capture.task_id
    assert capture.context_id


async def test_stream_ends_in_a_terminal_state(client, simple_prompt):
    capture = await send(client, simple_prompt)

    assert capture.final_state == "completed"


async def test_working_precedes_completion(client, simple_prompt):
    capture = await send(client, simple_prompt)

    assert "working" in capture.states
    assert capture.states.index("working") < capture.states.index("completed")


async def test_response_artifact_carries_the_reply(client, simple_prompt, reply_marker):
    capture = await send(client, simple_prompt)

    assert reply_marker in capture.artifact_text()


async def test_artifacts_may_arrive_chunked(client, simple_prompt, reply_marker):
    """Artifact text is assembled from N updates; tests must join, not index."""
    capture = await send(client, simple_prompt)

    assert capture.artifacts, "expected at least one artifact update"
    assert reply_marker in capture.artifact_text()


async def test_tool_activity_surfaces_as_status_text(client, simple_prompt, tool_marker):
    """Tool use should reach the client as working-status text, not only in
    the final artifact — that is what drives a live activity view."""
    capture = await send(client, simple_prompt)

    joined = "\n".join(capture.status_texts)
    assert tool_marker in joined
