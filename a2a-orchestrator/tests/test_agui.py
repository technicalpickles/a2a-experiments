"""The conversation plane, end to end: RunAgentInput in, AG-UI SSE out, rig behind."""

from __future__ import annotations

import json
import sqlite3
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


async def test_permission_pends_as_a_tool_call_and_allow_resumes(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    pending = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    starts = [e for e in pending if e["type"] == "TOOL_CALL_START"]
    assert [e["toolCallName"] for e in starts] == ["request_permission"]
    call_id = starts[0]["toolCallId"]
    args = json.loads(
        "".join(e["delta"] for e in pending if e["type"] == "TOOL_CALL_ARGS")
    )
    assert args["tool"] == "Bash"
    assert types_of(pending)[-1] == "RUN_FINISHED"

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
    pending = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    call_id = next(e["toolCallId"] for e in pending if e["type"] == "TOOL_CALL_START")
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


async def test_fresh_message_while_pending_leaves_the_card_answerable(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    pending = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    call_id = next(e["toolCallId"] for e in pending if e["type"] == "TOOL_CALL_START")

    fresh = await run(
        http, service_url, chat["context_id"], user_says("hello from the cockpit")
    )
    assert types_of(fresh)[-1] == "RUN_FINISHED"

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


async def test_two_chats_route_to_their_own_repos(mission, open_chat, http, service_url):
    billing = await open_chat(mission["id"], "billing-api")
    checkout = await open_chat(mission["id"], "checkout-web")

    billing_events = await run(
        http, service_url, billing["context_id"], user_says("what is this repo?")
    )
    checkout_events = await run(
        http, service_url, checkout["context_id"], user_says("what is this repo?")
    )

    assert "Ready when you are" in text_of(billing_events)
    assert "checkout flow" in text_of(checkout_events)
    assert billing["context_id"] != checkout["context_id"]


async def test_resume_with_nothing_pending_is_a_run_error(
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


async def test_seam_traffic_lands_in_the_event_log(
    mission, open_chat, http, service_url, service_db
):
    chat = await open_chat(mission["id"], "billing-api")
    await run(
        http, service_url, chat["context_id"], user_says("hello from the cockpit")
    )
    rows = sqlite3.connect(service_db).execute(
        "SELECT direction, payload FROM events WHERE context_id = ? ORDER BY seq",
        (chat["context_id"],),
    ).fetchall()
    incoming = [json.loads(p) for d, p in rows if d == "in"]
    assert [m["content"] for m in incoming] == ["hello from the cockpit"]
    out_types = [json.loads(p)["type"] for d, p in rows if d == "out"]
    assert out_types[0] == "RUN_STARTED"
    assert out_types[-1] == "RUN_FINISHED"
    assert "TEXT_MESSAGE_CONTENT" in out_types


async def test_mismatched_resume_refuses_and_keeps_pending(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    pending = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    call_id = next(e["toolCallId"] for e in pending if e["type"] == "TOOL_CALL_START")

    wrong = await run(
        http,
        service_url,
        chat["context_id"],
        [
            {
                "id": uuid.uuid4().hex,
                "role": "tool",
                "toolCallId": "not-" + call_id,
                "content": json.dumps({"decision": "allow"}),
            }
        ],
    )
    assert types_of(wrong)[-1] == "RUN_ERROR"

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
