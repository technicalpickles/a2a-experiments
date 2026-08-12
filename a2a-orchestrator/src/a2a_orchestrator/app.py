"""Assemble the service: management REST now, the A2A proxy route in the
a2a-proxy task, and — when a built frontend exists — the static cockpit,
mounted last so /api and /a2a always win."""

from __future__ import annotations

import contextlib
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from a2a_orchestrator import agui, api, proxy
from a2a_orchestrator.a2a_client import Conversations
from a2a_orchestrator.catalog import Catalog
from a2a_orchestrator.store import Store


def build_app(
    db_path: str | Path,
    catalog_path: str | Path,
    frontend_dist: Path | None = None,
) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.store = Store(db_path)
        app.state.catalog = Catalog.load(catalog_path)
        timeout = httpx.Timeout(120.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as http:
            app.state.http = http
            app.state.conversations = Conversations(http)
            yield

    routes = [
        Route("/api/catalog", api.get_catalog),
        Route("/api/missions", api.list_missions, methods=["GET"]),
        Route("/api/missions", api.create_mission, methods=["POST"]),
        Route("/api/missions/{mission_id}", api.rename_mission, methods=["PATCH"]),
        Route("/api/missions/{mission_id}/chats", api.open_chat, methods=["POST"]),
        Route(
            "/a2a/chats/{context_id}/{path:path}",
            proxy.a2a_endpoint,
            methods=["GET", "POST"],
        ),
        Route("/agui/run", agui.run_agent, methods=["POST"]),
    ]
    if frontend_dist and frontend_dist.is_dir():
        routes.append(Mount("/", StaticFiles(directory=frontend_dist, html=True)))
    return Starlette(routes=routes, lifespan=lifespan)
