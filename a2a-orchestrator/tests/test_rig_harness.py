"""Substrate wiring: the rig comes up and serves its index."""

from __future__ import annotations


async def test_rig_index_lists_the_fake_repos(rig_url, http):
    index = (await http.get(rig_url)).json()
    names = [entry["name"] for entry in index["repos"]]
    assert {"billing-api", "checkout-web", "infra-terraform"} <= set(names)


async def test_index_entries_carry_card_urls(rig_url, http):
    index = (await http.get(rig_url)).json()
    for entry in index["repos"]:
        assert entry["card_url"].endswith(".well-known/agent-card.json")
