"""Playback-specific behavior: scenario parsing, matching, and failing loudly.

Everything else about playback is covered by the backend-agnostic suite — that
it passes those tests unchanged is the actual claim being made here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from a2a.client import create_client
from a2a.client.client import ClientConfig

from a2a_playback import scenario as scenario_mod
from a2a_playback.scenario import Match, ScenarioError, parse
from a2a_rig.events import send
from a2a_rig.server import serve

SCENARIOS = Path(__file__).parent / "scenarios"

pytestmark = pytest.mark.backend("playback")


# --- Matching ------------------------------------------------------------


def test_turn_match_is_exact():
    match = Match(turn=2)

    assert match.matches("anything", 2)
    assert not match.matches("anything", 1)


def test_contains_is_case_insensitive():
    match = Match(contains="Explain")

    assert match.matches("please explain this", 1)


def test_regex_match():
    match = Match(regex=r"^add a /\w+ endpoint")

    assert match.matches("add a /health endpoint", 1)
    assert not match.matches("remove the endpoint", 1)


def test_rules_combine_conjunctively():
    """`turn` and `contains` together mean both, not either."""
    match = Match(turn=1, contains="explain")

    assert match.matches("explain this", 1)
    assert not match.matches("explain this", 2)
    assert not match.matches("something else", 1)


def test_first_match_wins():
    scenario = parse(
        {
            "name": "s",
            "plays": [
                {"match": {"contains": "a"}, "events": [{"text": "first"}]},
                {"match": {}, "events": [{"text": "default"}]},
            ],
        }
    )

    assert scenario.select("a", 1).events == [{"text": "first"}]
    assert scenario.select("zzz", 1).events == [{"text": "default"}]


def test_unmatched_turn_raises():
    scenario = parse(
        {"name": "s", "plays": [{"match": {"turn": 1}, "events": [{"text": "hi"}]}]}
    )

    with pytest.raises(ScenarioError, match="no play matched"):
        scenario.select("anything", 2)


# --- Validation ----------------------------------------------------------


def test_unknown_event_kind_is_rejected():
    with pytest.raises(ScenarioError, match="unknown event"):
        parse({"name": "s", "plays": [{"match": {}, "events": [{"txt": "oops"}]}]})


def test_unknown_match_key_is_rejected():
    with pytest.raises(ScenarioError, match="unknown match keys"):
        parse({"name": "s", "plays": [{"match": {"turnn": 1}, "events": [{"text": "x"}]}]})


def test_permission_needs_a_tool():
    with pytest.raises(ScenarioError, match="needs a `tool`"):
        parse(
            {
                "name": "s",
                "plays": [{"match": {}, "events": [{"permission": {"input": {}}}]}],
            }
        )


def test_events_inside_permission_branches_are_validated():
    with pytest.raises(ScenarioError, match="unknown event"):
        parse(
            {
                "name": "s",
                "plays": [
                    {
                        "match": {},
                        "events": [
                            {
                                "permission": {
                                    "tool": "Bash",
                                    "on_allow": [{"bogus": "x"}],
                                }
                            }
                        ],
                    }
                ],
            }
        )


def test_a_catch_all_that_is_not_last_is_rejected():
    """Otherwise the plays after it silently never run."""
    with pytest.raises(ScenarioError, match="unreachable"):
        parse(
            {
                "name": "s",
                "plays": [
                    {"match": {}, "events": [{"text": "default"}]},
                    {"match": {"contains": "a"}, "events": [{"text": "never"}]},
                ],
            }
        )


def test_scenario_needs_plays():
    with pytest.raises(ScenarioError, match="non-empty `plays`"):
        parse({"name": "s", "plays": []})


def test_shipped_scenario_parses():
    """The scenario the harness defaults to should always be loadable."""
    scenario = scenario_mod.load(
        Path(__file__).resolve().parents[1] / "scenarios" / "billing-api.yaml"
    )

    assert scenario.name == "billing-api"
    assert scenario.card_name == "billing-api"


# --- End to end ----------------------------------------------------------


async def test_scenario_drives_the_card(card):
    """`card:` in the scenario reaches the real agent card."""
    assert card["name"] == "billing-api"
    assert "playback" in card["description"]


async def test_permission_branch_carries_scripted_tool_calls(client, permission_prompt):
    """The allow branch should run its own scripted events, not just text."""
    parked = await send(client, permission_prompt)

    resumed = await send(
        client, "allow", task_id=parked.task_id, context_id=parked.context_id
    )

    assert "42 tests pass" in resumed.artifact_text()


async def test_result_metadata_reaches_the_client(client, permission_prompt):
    """Scripted cost/turns land where a real backend's would."""
    parked = await send(client, permission_prompt)

    resumed = await send(
        client, "allow", task_id=parked.task_id, context_id=parked.context_id
    )

    metadata = resumed.completion_metadata
    assert metadata.get("cost_usd") == pytest.approx(0.0173)
    assert metadata.get("num_turns") == 4


async def test_file_change_arrives_as_a_diff_artifact(client, permission_prompt):
    capture = await send(client, permission_prompt)

    names = [a.name for a in capture.artifacts]
    assert any("app.py" in name for name in names), names


async def test_turn_counting_is_per_context(client, simple_prompt):
    """A fresh conversation replays the scenario from turn 1."""
    first = await send(client, simple_prompt)
    second = await send(client, simple_prompt)

    assert first.context_id != second.context_id
    assert first.artifact_text() == second.artifact_text()


async def test_an_unmatched_turn_fails_the_task(http_client):
    """The core anti-mock guarantee: no plausible answer to an unscripted
    prompt. A mis-scripted test should break, not quietly pass."""
    with serve(backend="playback", scenario=SCENARIOS / "strict.yaml") as url:
        client = await create_client(
            url, ClientConfig(streaming=True, httpx_client=http_client)
        )

        matched = await send(client, "a known question")
        assert matched.final_state == "completed"

        unmatched = await send(client, "something nobody scripted")
        assert unmatched.final_state == "failed"
