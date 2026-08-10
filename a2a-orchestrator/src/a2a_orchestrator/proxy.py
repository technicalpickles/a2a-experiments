"""The conversation plane: a contextId-routed pass-through A2A proxy.

Every chat's base URL is ``/a2a/chats/{context_id}/`` — the contextId rides
the path, so routing any call (message/stream today, a cold
``tasks/resubscribe`` after a browser reload tomorrow) is a store lookup,
never a guess from observed traffic. The relay forwards bytes unmodified in
both directions, with the spec's one deliberate exception: agent cards
advertise the upstream's own origin (in both ``localhost`` and ``127.0.0.1``
spellings), so card responses are buffered and rewritten to the proxy's own
base — otherwise the browser's client escapes the proxy on its next call.
The proxy base is derived from the request's own host, so it stays correct
behind the Vite dev proxy and when hit directly alike.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

CARD_PATH = ".well-known/agent-card.json"

_HOP_BY_HOP = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _upstream_spellings(upstream_url: str) -> list[str]:
    """The upstream base in every spelling its cards are known to use."""
    parts = urlsplit(upstream_url)
    port = f":{parts.port}" if parts.port else ""
    hosts = {parts.hostname, "127.0.0.1", "localhost"} - {None}
    return [
        urlunsplit((parts.scheme, f"{host}{port}", parts.path, "", ""))
        for host in sorted(hosts)
    ]


def rewrite_card(text: str, upstream_url: str, proxy_base: str) -> str:
    for spelling in _upstream_spellings(upstream_url):
        text = text.replace(spelling, proxy_base)
        text = text.replace(spelling.rstrip("/"), proxy_base.rstrip("/"))
    return text


async def a2a_endpoint(request: Request) -> Response:
    store, http = request.app.state.store, request.app.state.http
    context_id = request.path_params["context_id"]
    path = request.path_params["path"]

    chat = store.chat_for_context(context_id)
    if chat is None:
        return JSONResponse(
            {"error": f"no chat bound for context {context_id!r}"}, status_code=404
        )

    target = f"{chat.upstream_url}{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    proxy_base = (
        f"{request.url.scheme}://{request.url.netloc}/a2a/chats/{context_id}/"
    )

    if request.method == "GET" and path == CARD_PATH:
        upstream = await http.get(target)
        return Response(
            rewrite_card(upstream.text, chat.upstream_url, proxy_base),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    upstream_request = http.build_request(
        request.method, target, content=request.stream(), headers=headers
    )
    upstream = await http.send(upstream_request, stream=True)
    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream.aclose),
    )
