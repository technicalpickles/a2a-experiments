"""The conversation plane: POST /agui/run, threadId-routed, SSE out.

One run per turn. RUN_STARTED is emitted before upstream is contacted so
every failure after it — unknown thread, refused turn, upstream fault —
lands inside the run as RUN_ERROR rather than as a broken transport. The
pending/clear decision happens here, after the translator has seen the whole
turn: pending permission -> remember the taskId; a consumed resume that
didn't re-pend -> forget it; a fresh message that didn't pend -> leave any
existing pending alone, since it wasn't this turn's to drop.
"""

from __future__ import annotations

import json
import logging

from ag_ui.core import (
    MessagesSnapshotEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
)
from ag_ui.encoder import EventEncoder
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from a2a_orchestrator.translate import RunTranslator, fold_messages, incoming_turn

logger = logging.getLogger(__name__)


async def run_agent(request: Request) -> StreamingResponse | JSONResponse:
    try:
        run_input = RunAgentInput.model_validate(await request.json())
    except Exception as exc:
        return JSONResponse({"error": f"not a RunAgentInput: {exc}"}, status_code=422)

    store = request.app.state.store
    conversations = request.app.state.conversations
    encoder = EventEncoder()

    async def stream():
        try:
            chat = store.chat_for_context(run_input.thread_id)
        except Exception:
            # No chat resolved means there's no context_id to log against —
            # these two go out unlogged, same as the no-chat-bound RUN_ERROR
            # below.
            logger.exception(
                "chat lookup failed for thread %s", run_input.thread_id
            )
            yield encoder.encode(
                RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
            )
            yield encoder.encode(
                RunErrorEvent(
                    message=f"chat lookup failed for thread {run_input.thread_id!r}"
                )
            )
            return

        def emit(event, *, best_effort: bool = False) -> str:
            # A write failure is a real failure: on the happy path it raises,
            # and the except arm below turns it into RUN_ERROR — a log with
            # holes is worse than a loud stop. Once we're already in that
            # except arm, though, delivering RUN_ERROR to the client
            # outranks logging it — best_effort emits try the write and fall
            # back to a bare encode rather than lose the event that tells
            # the client the run is over.
            if chat is not None:
                try:
                    store.append_event(
                        chat.context_id,
                        "out",
                        event.model_dump_json(by_alias=True, exclude_none=True),
                    )
                except Exception:
                    if not best_effort:
                        raise
                    logger.exception(
                        "failed to log outbound event for context %s",
                        chat.context_id,
                    )
            return encoder.encode(event)

        translator = RunTranslator(run_input.thread_id, run_input.run_id)
        try:
            yield emit(
                RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
            )
            if chat is None:
                yield encoder.encode(
                    RunErrorEvent(
                        message=f"no chat bound for thread {run_input.thread_id!r}"
                    )
                )
                return
            tail = run_input.messages[-1] if run_input.messages else None
            try:
                turn = incoming_turn(run_input)
            except ValueError:
                if tail is not None:
                    store.append_event(
                        chat.context_id,
                        "in",
                        tail.model_dump_json(by_alias=True, exclude_none=True),
                    )
                raise
            if tail is not None:
                if turn.kind == "resume":
                    pending = conversations.pending_of(chat.context_id)
                    claimed = turn.request_id or turn.tool_call_id
                    if pending is not None and claimed == pending.call_id:
                        # A re-armed card answers with a freshly minted
                        # toolCallId; the log must pair the answer with the
                        # call it verified against, or replay folds an
                        # orphan (final review, F1).
                        tail = tail.model_copy(update={"tool_call_id": pending.call_id})
                store.append_event(
                    chat.context_id,
                    "in",
                    tail.model_dump_json(by_alias=True, exclude_none=True),
                )
            async for event in conversations.run_turn(chat, turn):
                for out in translator.feed(event):
                    yield emit(out)
            for out in translator.finish():
                yield emit(out)
        except Exception as exc:  # every failure must reach the stream as RUN_ERROR
            logger.exception(
                "run %s on thread %s failed", run_input.run_id, run_input.thread_id
            )
            for out in translator.abort():
                yield emit(out, best_effort=True)
            yield emit(RunErrorEvent(message=str(exc)), best_effort=True)
            return
        if translator.truncated:
            # The upstream never reached a terminal state; whatever was
            # pending before this turn is not this turn's to decide.
            return
        # The pending state is replaced or consumed, never incidentally
        # dropped — a fresh message while an approval waits leaves the card
        # answerable.
        if translator.pending and translator.task_id:
            conversations.set_pending(
                chat.context_id,
                translator.task_id,
                translator.call_id,
                json.dumps(translator.pending),
            )
        elif turn.kind == "resume":
            conversations.clear_pending(chat.context_id)

    return StreamingResponse(stream(), media_type=encoder.get_content_type())


async def connect_agent(request: Request) -> StreamingResponse | JSONResponse:
    """Replay: answer CopilotKit's mount-time connect with the folded log.

    Derived data only — this endpoint never writes to the event log.
    """
    try:
        run_input = RunAgentInput.model_validate(await request.json())
    except Exception as exc:
        return JSONResponse({"error": f"not a RunAgentInput: {exc}"}, status_code=422)

    store = request.app.state.store
    encoder = EventEncoder()

    async def stream():
        yield encoder.encode(
            RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )
        try:
            chat = store.chat_for_context(run_input.thread_id)
        except Exception:
            logger.exception(
                "chat lookup failed for thread %s", run_input.thread_id
            )
            yield encoder.encode(
                RunErrorEvent(
                    message=f"chat lookup failed for thread {run_input.thread_id!r}"
                )
            )
            return
        if chat is None:
            yield encoder.encode(
                RunErrorEvent(
                    message=f"no chat bound for thread {run_input.thread_id!r}"
                )
            )
            return
        messages = fold_messages(store.events_for_context(chat.context_id))
        yield encoder.encode(MessagesSnapshotEvent(messages=messages))
        yield encoder.encode(
            RunFinishedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )

    return StreamingResponse(stream(), media_type=encoder.get_content_type())
