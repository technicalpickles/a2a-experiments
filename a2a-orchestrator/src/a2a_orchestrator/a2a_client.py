"""Service-side A2A conversations, one per chat.

Grown from a2a-rig's harness client (a2a_rig/events.py): same create_client +
send_message loop, reshaped as a long-lived registry. The service — not the
browser — owns which task a chat has pending; agui.py records it here after
the translator has seen the whole turn. In-memory by design (spec: Domain
model / Identifiers): a service restart loses the pending state, same deferral class
as reload replay, both resolved by the future event log.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator, Protocol

import httpx
from a2a.client import create_client
from a2a.client.client import ClientConfig
from a2a.types import Message, Part, Role, SendMessageRequest, StreamResponse

from a2a_orchestrator.translate import Turn


class ChatLike(Protocol):
    context_id: str
    upstream_url: str


class Conversations:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http
        self._clients: dict[str, object] = {}
        self._pending: dict[str, str] = {}

    def set_pending(self, context_id: str, task_id: str) -> None:
        self._pending[context_id] = task_id

    def clear_pending(self, context_id: str) -> None:
        self._pending.pop(context_id, None)

    def pending_task(self, context_id: str) -> str | None:
        return self._pending.get(context_id)

    async def _client(self, chat: ChatLike):
        if chat.context_id not in self._clients:
            self._clients[chat.context_id] = await create_client(
                chat.upstream_url,
                ClientConfig(streaming=True, httpx_client=self._http),
            )
        return self._clients[chat.context_id]

    async def run_turn(
        self, chat: ChatLike, turn: Turn
    ) -> AsyncIterator[StreamResponse]:
        task_id = ""
        if turn.kind == "resume":
            task_id = self._pending.get(chat.context_id, "")
            if not task_id:
                raise LookupError(f"no pending task for context {chat.context_id!r}")
        client = await self._client(chat)
        message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.ROLE_USER,
            parts=[Part(text=turn.text)],
        )
        message.context_id = chat.context_id
        if task_id:
            message.task_id = task_id
        async for event in client.send_message(SendMessageRequest(message=message)):
            yield event
