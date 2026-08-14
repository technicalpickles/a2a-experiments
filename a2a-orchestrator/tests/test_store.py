"""The SQLite floor: missions and chats, nothing the milestone doesn't need."""

from __future__ import annotations

import sqlite3

from a2a_orchestrator.store import Store, Pending

BILLING = "http://127.0.0.1:9200/repos/billing-api/"


def test_create_and_list_missions(tmp_path):
    store = Store(tmp_path / "test.db")
    created = store.create_mission(title="Ship health checks")

    missions = store.list_missions()

    assert [m.id for m in missions] == [created.id]
    assert missions[0].title == "Ship health checks"
    assert missions[0].created_at


def test_mission_title_defaults(tmp_path):
    store = Store(tmp_path / "test.db")
    assert store.create_mission().title == "Untitled mission"


def test_rename_mission(tmp_path):
    store = Store(tmp_path / "test.db")
    mission = store.create_mission()

    renamed = store.rename_mission(mission.id, "Better title")

    assert renamed.title == "Better title"
    assert store.get_mission(mission.id).title == "Better title"


def test_rename_unknown_mission_returns_none(tmp_path):
    store = Store(tmp_path / "test.db")
    assert store.rename_mission("nope", "x") is None


def test_create_chat_mints_unique_context_ids(tmp_path):
    store = Store(tmp_path / "test.db")
    mission = store.create_mission()

    first = store.create_chat(mission.id, "billing-api", BILLING)
    second = store.create_chat(mission.id, "billing-api", BILLING)

    assert first.context_id != second.context_id


def test_chat_lookup_by_context(tmp_path):
    store = Store(tmp_path / "test.db")
    mission = store.create_mission()
    chat = store.create_chat(mission.id, "billing-api", BILLING)

    found = store.chat_for_context(chat.context_id)

    assert found.upstream_url == BILLING
    assert found.agent == "billing-api"
    assert store.chat_for_context("missing") is None


def test_chats_for_mission_lists_in_order(tmp_path):
    store = Store(tmp_path / "test.db")
    mission = store.create_mission()
    other = store.create_mission()
    first = store.create_chat(mission.id, "billing-api", BILLING)
    second = store.create_chat(mission.id, "checkout-web", BILLING)
    store.create_chat(other.id, "billing-api", BILLING)

    chats = store.chats_for_mission(mission.id)

    assert [c.context_id for c in chats] == [first.context_id, second.context_id]


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "test.db"
    mission = Store(path).create_mission(title="Persist me")

    reopened = Store(path)

    assert [m.title for m in reopened.list_missions()] == ["Persist me"]
    assert reopened.chats_for_mission(mission.id) == []


def _chat(store):
    mission = store.create_mission()
    return store.create_chat(mission.id, "billing-api", "http://upstream/")


def test_events_append_in_order(tmp_path):
    store = Store(tmp_path / "orch.db")
    chat = _chat(store)
    store.append_event(chat.context_id, "out", '{"type": "RUN_STARTED"}')
    store.append_event(chat.context_id, "in", '{"role": "user"}')
    rows = store.events_for_context(chat.context_id)
    assert [(seq, direction) for seq, direction, _ in rows] == [(1, "out"), (2, "in")]
    assert rows[0][2] == '{"type": "RUN_STARTED"}'


def test_events_are_isolated_per_context(tmp_path):
    store = Store(tmp_path / "orch.db")
    one, two = _chat(store), _chat(store)
    store.append_event(one.context_id, "out", "{}")
    store.append_event(two.context_id, "out", "{}")
    store.append_event(one.context_id, "in", "{}")
    assert [seq for seq, _, _ in store.events_for_context(one.context_id)] == [1, 2]
    assert [seq for seq, _, _ in store.events_for_context(two.context_id)] == [1]


def test_pending_set_read_clear(tmp_path):
    store = Store(tmp_path / "orch.db")
    chat = _chat(store)
    assert store.pending_of(chat.context_id) is None
    store.set_pending(chat.context_id, "t1", "req-1", '{"tool": "Bash"}')
    assert store.pending_of(chat.context_id) == Pending(
        task_id="t1", call_id="req-1", payload='{"tool": "Bash"}'
    )
    store.clear_pending(chat.context_id)
    assert store.pending_of(chat.context_id) is None


def test_events_and_pending_survive_reopen(tmp_path):
    path = tmp_path / "orch.db"
    store = Store(path)
    chat = _chat(store)
    store.append_event(chat.context_id, "out", '{"type": "RUN_STARTED"}')
    store.set_pending(chat.context_id, "t1", "req-1", "{}")

    reopened = Store(path)
    assert [seq for seq, _, _ in reopened.events_for_context(chat.context_id)] == [1]
    assert reopened.pending_of(chat.context_id).task_id == "t1"


def test_migration_adds_pending_columns_to_an_existing_db(tmp_path):
    path = tmp_path / "orch.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE missions (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                               created_at TEXT NOT NULL);
        CREATE TABLE chats (context_id TEXT PRIMARY KEY,
                            mission_id TEXT NOT NULL REFERENCES missions(id),
                            agent TEXT NOT NULL, upstream_url TEXT NOT NULL,
                            created_at TEXT NOT NULL);
        INSERT INTO missions VALUES ('m1', 'title', 'now');
        INSERT INTO chats VALUES ('c1', 'm1', 'billing-api', 'http://up/', 'now');
        """
    )
    db.commit()
    db.close()

    store = Store(path)
    assert store.pending_of("c1") is None
    store.set_pending("c1", "t1", "req-1", "{}")
    assert store.pending_of("c1").task_id == "t1"
