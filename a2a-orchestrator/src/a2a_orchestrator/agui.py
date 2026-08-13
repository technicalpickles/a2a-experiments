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

from ag_ui.core import RunAgentInput, RunErrorEvent, RunStartedEvent
from ag_ui.encoder import EventEncoder
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from a2a_orchestrator.translate import RunTranslator, incoming_turn

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
        yield encoder.encode(
            RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )
        translator = RunTranslator(run_input.thread_id, run_input.run_id)
        try:
            chat = store.chat_for_context(run_input.thread_id)
            if chat is None:
                yield encoder.encode(
                    RunErrorEvent(
                        message=f"no chat bound for thread {run_input.thread_id!r}"
                    )
                )
                return
            turn = incoming_turn(run_input)
            async for event in conversations.run_turn(chat, turn):
                for out in translator.feed(event):
                    yield encoder.encode(out)
            for out in translator.finish():
                yield encoder.encode(out)
        except Exception as exc:  # every failure must reach the stream as RUN_ERROR
            logger.exception(
                "run %s on thread %s failed", run_input.run_id, run_input.thread_id
            )
            for out in translator.abort():
                yield encoder.encode(out)
            yield encoder.encode(RunErrorEvent(message=str(exc)))
            return
        # The pending state is replaced or consumed, never incidentally dropped — a
        # fresh message while an approval is pending leaves the card
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
