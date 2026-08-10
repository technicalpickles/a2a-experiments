"""Management REST: what A2A has no vocabulary for — missions, chats, catalog.

Handlers read the store/catalog/http client off ``request.app.state``; the
lifespan in app.py owns their lifetimes. Error bodies always carry an
``error`` key naming what failed, per the spec's error-handling table.
"""

from __future__ import annotations

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from a2a_orchestrator.store import Chat, Mission, Store


def _chat_json(chat: Chat) -> dict:
    return {
        "context_id": chat.context_id,
        "mission_id": chat.mission_id,
        "agent": chat.agent,
        "a2a_url": chat.a2a_url,
        "created_at": chat.created_at,
    }


def _mission_json(store: Store, mission: Mission) -> dict:
    return {
        "id": mission.id,
        "title": mission.title,
        "created_at": mission.created_at,
        "chats": [_chat_json(c) for c in store.chats_for_mission(mission.id)],
    }


async def _body(request: Request) -> dict:
    try:
        parsed = await request.json()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def get_catalog(request: Request) -> JSONResponse:
    catalog, http = request.app.state.catalog, request.app.state.http
    try:
        entries = await catalog.repos(http)
    except httpx.HTTPError as exc:
        return JSONResponse(
            {"error": f"catalog index at {catalog.index_url} unreachable: {exc}"},
            status_code=502,
        )
    return JSONResponse(
        {"repos": [{"name": e.name, "description": e.description} for e in entries]}
    )


async def list_missions(request: Request) -> JSONResponse:
    store = request.app.state.store
    return JSONResponse(
        {"missions": [_mission_json(store, m) for m in store.list_missions()]}
    )


async def create_mission(request: Request) -> JSONResponse:
    store = request.app.state.store
    body = await _body(request)
    mission = store.create_mission(title=body.get("title") or "Untitled mission")
    return JSONResponse(_mission_json(store, mission), status_code=201)


async def rename_mission(request: Request) -> JSONResponse:
    store = request.app.state.store
    body = await _body(request)
    title = body.get("title")
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    mission = store.rename_mission(request.path_params["mission_id"], title)
    if mission is None:
        return JSONResponse({"error": "no such mission"}, status_code=404)
    return JSONResponse(_mission_json(store, mission))


async def open_chat(request: Request) -> JSONResponse:
    store = request.app.state.store
    catalog, http = request.app.state.catalog, request.app.state.http
    mission = store.get_mission(request.path_params["mission_id"])
    if mission is None:
        return JSONResponse({"error": "no such mission"}, status_code=404)
    body = await _body(request)
    agent = body.get("agent")
    if not agent:
        return JSONResponse({"error": "agent is required"}, status_code=400)
    try:
        entry = await catalog.resolve(http, agent)
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except httpx.HTTPError as exc:
        return JSONResponse(
            {"error": f"catalog index at {catalog.index_url} unreachable: {exc}"},
            status_code=502,
        )
    chat = store.create_chat(mission.id, agent, entry.base_url)
    return JSONResponse(_chat_json(chat), status_code=201)
