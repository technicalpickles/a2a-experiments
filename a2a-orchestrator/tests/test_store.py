"""The SQLite floor: missions and chats, nothing the milestone doesn't need."""

from __future__ import annotations

from a2a_orchestrator.store import Store

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
