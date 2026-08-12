"""The conversation plane, end to end: RunAgentInput in, AG-UI SSE out, rig behind."""

from __future__ import annotations

import json
import uuid


def events_of(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


async def run(http, service_url, thread_id, messages):
    payload = {
        "threadId": thread_id,
        "runId": uuid.uuid4().hex,
        "state": None,
        "messages": messages,
        "tools": [
            {
                "name": "request_permission",
                "description": "Ask the user to allow or deny a tool use",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "context": [],
        "forwardedProps": None,
    }
    response = await http.post(f"{service_url}agui/run", json=payload)
    assert response.status_code == 200, response.text
    return events_of(response.text)


def user_says(text):
    return [{"id": uuid.uuid4().hex, "role": "user", "content": text}]


def types_of(events):
    return [e["type"] for e in events]


def text_of(events):
    return "".join(
        e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"
    )


async def test_free_text_round_trips(mission, open_chat, http, service_url):
    chat = await open_chat(mission["id"], "billing-api")
    events = await run(
        http, service_url, chat["context_id"], user_says("hello from the cockpit")
    )
    assert types_of(events)[0] == "RUN_STARTED"
    assert types_of(events)[-1] == "RUN_FINISHED"
    assert "Ready when you are" in text_of(events)


async def test_permission_parks_as_a_tool_call_and_allow_resumes(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    parked = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    starts = [e for e in parked if e["type"] == "TOOL_CALL_START"]
    assert [e["toolCallName"] for e in starts] == ["request_permission"]
    call_id = starts[0]["toolCallId"]
    args = json.loads(
        "".join(e["delta"] for e in parked if e["type"] == "TOOL_CALL_ARGS")
    )
    assert args["tool"] == "Bash"
    assert types_of(parked)[-1] == "RUN_FINISHED"

    resumed = await run(
        http,
        service_url,
        chat["context_id"],
        [
            {
                "id": uuid.uuid4().hex,
                "role": "tool",
                "toolCallId": call_id,
                "content": json.dumps({"decision": "allow"}),
            }
        ],
    )
    assert types_of(resumed)[-1] == "RUN_FINISHED"
    assert not [e for e in resumed if e["type"] == "RUN_ERROR"]


async def test_deny_reads_as_the_skipped_ending(mission, open_chat, http, service_url):
    chat = await open_chat(mission["id"], "billing-api")
    parked = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    call_id = next(e["toolCallId"] for e in parked if e["type"] == "TOOL_CALL_START")
    resumed = await run(
        http,
        service_url,
        chat["context_id"],
        [
            {
                "id": uuid.uuid4().hex,
                "role": "tool",
                "toolCallId": call_id,
                "content": json.dumps({"decision": "deny"}),
            }
        ],
    )
    assert "Skipped the test run" in text_of(resumed)


async def test_upstream_failure_is_a_run_error(mission, open_chat, http, service_url):
    chat = await open_chat(mission["id"], "infra-terraform")
    events = await run(
        http, service_url, chat["context_id"], user_says("status check please")
    )
    assert types_of(events)[0] == "RUN_STARTED"
    assert types_of(events)[-1] == "RUN_ERROR"
    assert events[-1]["message"]


async def test_unbound_thread_is_a_run_error(http, service_url):
    events = await run(http, service_url, "deadbeef", user_says("hello"))
    assert types_of(events) == ["RUN_STARTED", "RUN_ERROR"]
    assert "deadbeef" in events[-1]["message"]


async def test_resume_with_nothing_parked_is_a_run_error(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    events = await run(
        http,
        service_url,
        chat["context_id"],
        [
            {
                "id": uuid.uuid4().hex,
                "role": "tool",
                "toolCallId": "req-x",
                "content": json.dumps({"decision": "allow"}),
            }
        ],
    )
    assert types_of(events)[-1] == "RUN_ERROR"
