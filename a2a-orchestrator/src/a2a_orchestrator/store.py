"""SQLite persistence: missions and the chats bound inside them.

One connection, one file. The service is a single process on one event loop,
so a shared connection with explicit transactions is enough — no pool, no ORM.
The schema is the floor the spec names: what chat routing and (later) resume
actually need. Session detail grows only when a use case demands it.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    context_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id),
    agent TEXT NOT NULL,
    upstream_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_MISSION_COLS = "id, title, created_at"
_CHAT_COLS = "context_id, mission_id, agent, upstream_url, created_at"


@dataclass
class Mission:
    id: str
    title: str
    created_at: str


@dataclass
class Chat:
    context_id: str
    mission_id: str
    agent: str
    upstream_url: str
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def create_mission(self, title: str = "Untitled mission") -> Mission:
        mission = Mission(id=uuid.uuid4().hex, title=title, created_at=_now())
        with self._db:
            self._db.execute(
                "INSERT INTO missions VALUES (?, ?, ?)",
                (mission.id, mission.title, mission.created_at),
            )
        return mission

    def list_missions(self) -> list[Mission]:
        rows = self._db.execute(
            f"SELECT {_MISSION_COLS} FROM missions ORDER BY created_at, id"
        ).fetchall()
        return [Mission(*row) for row in rows]

    def get_mission(self, mission_id: str) -> Mission | None:
        row = self._db.execute(
            f"SELECT {_MISSION_COLS} FROM missions WHERE id = ?", (mission_id,)
        ).fetchone()
        return Mission(*row) if row else None

    def rename_mission(self, mission_id: str, title: str) -> Mission | None:
        with self._db:
            changed = self._db.execute(
                "UPDATE missions SET title = ? WHERE id = ?", (title, mission_id)
            ).rowcount
        return self.get_mission(mission_id) if changed else None

    def create_chat(self, mission_id: str, agent: str, upstream_url: str) -> Chat:
        chat = Chat(
            context_id=uuid.uuid4().hex,
            mission_id=mission_id,
            agent=agent,
            upstream_url=upstream_url,
            created_at=_now(),
        )
        with self._db:
            self._db.execute(
                "INSERT INTO chats VALUES (?, ?, ?, ?, ?)",
                (chat.context_id, chat.mission_id, chat.agent,
                 chat.upstream_url, chat.created_at),
            )
        return chat

    def chats_for_mission(self, mission_id: str) -> list[Chat]:
        rows = self._db.execute(
            f"SELECT {_CHAT_COLS} FROM chats WHERE mission_id = ? "
            "ORDER BY created_at, context_id",
            (mission_id,),
        ).fetchall()
        return [Chat(*row) for row in rows]

    def chat_for_context(self, context_id: str) -> Chat | None:
        row = self._db.execute(
            f"SELECT {_CHAT_COLS} FROM chats WHERE context_id = ?", (context_id,)
        ).fetchone()
        return Chat(*row) if row else None
