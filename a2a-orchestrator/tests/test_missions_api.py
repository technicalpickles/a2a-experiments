"""Management REST over the wire, against the service subprocess.

The service is session-scoped, so tests assert on what they created and
never on database totals.
"""

from __future__ import annotations


async def test_create_mission_and_find_it_listed(service_url, http):
    created = (
        await http.post(f"{service_url}api/missions", json={"title": "Ticket ABC-123"})
    ).json()

    listed = (await http.get(f"{service_url}api/missions")).json()["missions"]

    mine = next(m for m in listed if m["id"] == created["id"])
    assert mine["title"] == "Ticket ABC-123"
    assert mine["chats"] == []


async def test_create_mission_defaults_the_title(service_url, http):
    response = await http.post(f"{service_url}api/missions", json={})

    assert response.status_code == 201
    assert response.json()["title"] == "Untitled mission"


async def test_rename_mission(service_url, http, mission):
    response = await http.patch(
        f"{service_url}api/missions/{mission['id']}", json={"title": "Renamed"}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


async def test_rename_unknown_mission_404s(service_url, http):
    response = await http.patch(
        f"{service_url}api/missions/nope", json={"title": "x"}
    )
    assert response.status_code == 404


async def test_catalog_endpoint_lists_repos(service_url, http):
    repos = (await http.get(f"{service_url}api/catalog")).json()["repos"]
    assert "billing-api" in [repo["name"] for repo in repos]


async def test_open_chat_binds_and_returns_the_context(
    service_url, http, mission, open_chat
):
    chat = await open_chat(mission["id"], "billing-api")

    assert chat["agent"] == "billing-api"
    assert chat["a2a_url"] == f"/a2a/chats/{chat['context_id']}/"

    listed = (await http.get(f"{service_url}api/missions")).json()["missions"]
    mine = next(m for m in listed if m["id"] == mission["id"])
    assert chat["context_id"] in [c["context_id"] for c in mine["chats"]]


async def test_open_chat_with_unknown_repo_names_it(service_url, http, mission):
    response = await http.post(
        f"{service_url}api/missions/{mission['id']}/chats",
        json={"agent": "no-such-repo"},
    )

    assert response.status_code == 404
    assert "no-such-repo" in response.json()["error"]


async def test_open_chat_on_unknown_mission_404s(service_url, http):
    response = await http.post(
        f"{service_url}api/missions/nope/chats", json={"agent": "billing-api"}
    )
    assert response.status_code == 404


async def test_open_chat_without_agent_400s(service_url, http, mission):
    response = await http.post(
        f"{service_url}api/missions/{mission['id']}/chats", json={}
    )
    assert response.status_code == 400
