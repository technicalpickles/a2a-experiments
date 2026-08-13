"""A service restart loses nothing: history replays, the approval resumes."""

from __future__ import annotations

import json
import uuid

from test_agui import connect, run, types_of, user_says


async def open_chat_at(http, url, agent="billing-api"):
    mission = (await http.post(f"{url}api/missions", json={})).json()
    response = await http.post(
        f"{url}api/missions/{mission['id']}/chats", json={"agent": agent}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_history_and_pending_survive_a_restart(restartable_service, http):
    chat = await open_chat_at(http, restartable_service.url)
    pending = await run(
        http, restartable_service.url, chat["context_id"],
        user_says("please run the tests"),
    )
    call_id = next(
        e["toolCallId"] for e in pending if e["type"] == "TOOL_CALL_START"
    )

    restartable_service.restart()

    replay = await connect(http, restartable_service.url, chat["context_id"])
    snapshot = next(e for e in replay if e["type"] == "MESSAGES_SNAPSHOT")
    calls = [
        c for m in snapshot["messages"] for c in (m.get("toolCalls") or [])
    ]
    assert [c["id"] for c in calls] == [call_id]

    resumed = await run(
        http,
        restartable_service.url,
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
