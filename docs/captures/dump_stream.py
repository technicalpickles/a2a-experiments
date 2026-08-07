"""Drive an a2acode server and dump every streamed event as JSON.

Same client path as `a2acode call`, but serializes the raw protobuf events with
MessageToDict so the output is a wire-shape reference for scenario authoring,
not a human-readable summary.

Usage: dump_stream.py "<prompt>" [--task ID] [--context ID] [--url URL]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import uuid4

import httpx
from a2a.client import create_client
from a2a.client.client import ClientConfig
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState
from google.protobuf.json_format import MessageToDict


def state_name(state) -> str:
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--url", default="http://localhost:9100/")
    ap.add_argument("--task")
    ap.add_argument("--context")
    args = ap.parse_args()

    message = Message(
        message_id=uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=args.text)]
    )
    if args.context:
        message.context_id = args.context
    if args.task:
        message.task_id = args.task

    final_state = None
    ids = {"task": args.task or "", "context": args.context or ""}

    timeout = httpx.Timeout(900.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        client = await create_client(
            args.url, ClientConfig(streaming=True, httpx_client=http)
        )
        async for event in client.send_message(SendMessageRequest(message=message)):
            which = event.WhichOneof("payload")
            payload = MessageToDict(event, preserving_proto_field_name=True)
            print(json.dumps({"event": which, **payload}, ensure_ascii=False), flush=True)
            if which == "task":
                ids["task"] = event.task.id
                ids["context"] = event.task.context_id
            elif which == "status_update":
                final_state = state_name(event.status_update.status.state)

    print(
        json.dumps({"event": "_summary", "final_state": final_state, "ids": ids}),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
