"""Card discovery — the first thing any client does, and the thing that broke
a2a-cli and a2a-inspector (see a2a-experiments DEVLOG 2026-08-06)."""

from __future__ import annotations


def test_card_is_served(card):
    assert card["name"]
    assert card["version"]


def test_card_advertises_streaming(card):
    assert card["capabilities"]["streaming"] is True


def test_card_declares_skills(card):
    assert card["skills"], "agent card should advertise at least one skill"
    for skill in card["skills"]:
        assert skill["id"]
        assert skill["name"]


def test_card_uses_supported_interfaces(card):
    """a2a-sdk 1.x replaced the single `url` field with an interface list.

    Pinned deliberately: this exact shape change is what stranded the 0.3.x
    clients, so the harness should notice if a2acode ever moves back.
    """
    interfaces = card.get("supportedInterfaces") or card.get("supported_interfaces")
    assert interfaces, f"expected supportedInterfaces on the card, got keys: {sorted(card)}"
    assert any(i.get("url") for i in interfaces)
