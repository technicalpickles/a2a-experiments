"""Service-side A2A conversations, one per chat.

Grown from a2a-rig's harness client (a2a_rig/events.py): same create_client +
send_message loop, reshaped as a long-lived registry. The service — not the
browser — owns which task a chat has pending; agui.py records it here after
the translator has seen the whole turn. The store owns pending state, not
this class: a service restart finds it right where the last turn left it.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator, Protocol

import httpx
from a2a.client import create_client
from a2a.client.client import ClientConfig
from a2a.types import Message, Part, Role, SendMessageRequest, StreamResponse

from a2a_orchestrator.store import Pending, Store
from a2a_orchestrator.translate import Turn


class ChatLike(Protocol):
    context_id: str
    upstream_url: str


class Conversations:
    def __init__(self, http: httpx.AsyncClient, store: Store):
        self._http = http
        self._store = store
        self._clients: dict[str, object] = {}

    def set_pending(
        self, context_id: str, task_id: str, call_id: str, payload: str
    ) -> None:
        self._store.set_pending(context_id, task_id, call_id, payload)

    def clear_pending(self, context_id: str) -> None:
        self._store.clear_pending(context_id)
        # A wedged connection must not outlive the exchange that wedged it.
        self._clients.pop(context_id, None)

    def pending_of(self, context_id: str) -> Pending | None:
        return self._store.pending_of(context_id)

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
            pending = self._store.pending_of(chat.context_id)
            if pending is None:
                raise LookupError(f"no pending task for context {chat.context_id!r}")
            claimed = turn.request_id or turn.tool_call_id
            if claimed != pending.call_id:
                raise ValueError(
                    f"resume answers {claimed!r} but the pending approval is "
                    f"{pending.call_id!r}"
                )
            task_id = pending.task_id
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
