"""Playback-specific behavior: scenario parsing, matching, and failing loudly.

Everything else about playback is covered by the backend-agnostic suite — that
it passes those tests unchanged is the actual claim being made here.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
import pytest_asyncio
from a2a.client import create_client
from a2a.client.client import ClientConfig
from a2a.types import CancelTaskRequest, GetTaskRequest
from a2acode.backends.base import PermissionDecision, RunRequest
from a2acode.backends.session import BackendSession

from a2a_playback import scenario as scenario_mod
from a2a_playback.backend import PlaybackBackend, ScriptedError
from a2a_playback.scenario import Match, ScenarioError, parse
from a2a_rig.events import parts_text, send, state_name
from a2a_rig.server import serve

SCENARIOS = Path(__file__).parent / "scenarios"

pytestmark = pytest.mark.backend("playback")


@pytest.fixture(scope="session")
def _scenario_servers():
    """Playback servers keyed by scenario (and env), reused across tests.

    Same bargain as the main pool in conftest: booting costs ~0.5s, and tasks
    are isolated by id, so one server per distinct configuration is enough.
    """
    pool: dict[tuple, str] = {}
    stack = ExitStack()
    try:

        def get(name: str, env: dict[str, str] | None = None) -> str:
            key = (name, tuple(sorted((env or {}).items())))
            if key not in pool:
                pool[key] = stack.enter_context(
                    serve(backend="playback", scenario=SCENARIOS / name, env=env)
                )
            return pool[key]

        yield get
    finally:
        stack.close()


@pytest_asyncio.fixture
async def on_scenario(_scenario_servers, http_client):
    """`client = await on_scenario("vocabulary.yaml")` — a client on one scenario."""

    async def make(name: str, env: dict[str, str] | None = None):
        return await create_client(
            _scenario_servers(name, env),
            ClientConfig(streaming=True, httpx_client=http_client),
        )

    return make


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


def test_error_is_a_known_event_kind():
    """A scenario can script a failing run, not only a succeeding one."""
    scenario = parse(
        {"name": "s", "plays": [{"match": {}, "events": [{"error": "disk full"}]}]}
    )

    assert scenario.select("anything", 1).events == [{"error": "disk full"}]


def test_error_needs_a_message():
    with pytest.raises(ScenarioError, match="needs a message"):
        parse({"name": "s", "plays": [{"match": {}, "events": [{"error": ""}]}]})


def test_events_inside_a_timeout_branch_are_validated():
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
                                    "timeout_ms": 50,
                                    "on_timeout": [{"bogus": "x"}],
                                }
                            }
                        ],
                    }
                ],
            }
        )


def test_a_timeout_branch_needs_a_timeout_to_reach_it():
    """Otherwise the branch is dead script that reads as covered behavior."""
    with pytest.raises(ScenarioError, match="`timeout_ms`"):
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
                                    "on_timeout": [{"text": "never"}],
                                }
                            }
                        ],
                    }
                ],
            }
        )


def test_a_plan_step_needs_content():
    """`step["content"]` is a hard index in the backend, so a step without one
    would raise halfway through a turn a frontend is already watching."""
    with pytest.raises(ScenarioError, match="`content`"):
        parse(
            {
                "name": "s",
                "plays": [
                    {
                        "match": {},
                        "events": [{"plan": {"steps": [{"status": "pending"}]}}],
                    }
                ],
            }
        )


def test_a_plan_cannot_be_two_things_at_once():
    """a2acode renders markdown ahead of steps and never mentions the loss, so a
    scenario written with both would quietly ship a plan the author never read."""
    with pytest.raises(ScenarioError, match="one of"):
        parse(
            {
                "name": "s",
                "plays": [
                    {
                        "match": {},
                        "events": [
                            {
                                "plan": {
                                    "steps": [{"content": "step one"}],
                                    "markdown": "## Plan\n",
                                }
                            }
                        ],
                    }
                ],
            }
        )


def test_an_empty_plan_is_allowed():
    """It is how an agent says it abandoned the checklist — a2acode replaces the
    artifact with nothing rather than leaving a stale plan on screen."""
    scenario = parse(
        {"name": "s", "plays": [{"match": {}, "events": [{"plan": {}}]}]}
    )

    assert scenario.select("anything", 1).events == [{"plan": {}}]


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


# --- Driving the backend directly ----------------------------------------
#
# a2acode's own BackendSession, driven in-process. Unlike the harness proper —
# which shells out so it exercises the wire — these assert the backend's control
# flow at the seam a2acode actually calls, which is where scripted failures and
# permission timeouts are decided.


def _driven(raw: dict, prompt: str = "go") -> BackendSession:
    session = BackendSession()
    backend = PlaybackBackend(parse(raw))
    request = RunRequest(prompt=prompt, context_id="ctx")
    session.start(lambda s: backend.drive(s, request))
    return session


async def _events(session: BackendSession, timeout: float = 2.0) -> list:
    """Drain one stretch of the run. Bounded so a driver that never emits fails
    the test instead of hanging the suite."""

    async def collect() -> list:
        return [event async for event in session.drain()]

    return await asyncio.wait_for(collect(), timeout=timeout)


async def test_error_event_fails_the_run_with_its_message():
    """A scripted failure is a real failure — the driver raises, exactly as a
    backend hitting a broken sandbox would."""
    session = _driven(
        {
            "name": "s",
            "plays": [
                {
                    "match": {},
                    "events": [{"text": "trying"}, {"error": "sandbox out of disk"}],
                }
            ],
        }
    )

    with pytest.raises(ScriptedError, match="sandbox out of disk"):
        await _events(session)


async def test_events_before_an_error_still_reach_the_consumer():
    session = _driven(
        {
            "name": "s",
            "plays": [
                {
                    "match": {},
                    "events": [
                        {"tool_use": {"name": "Bash", "id": "t1"}},
                        {"error": "exit 1"},
                    ],
                }
            ],
        }
    )

    seen = []
    with pytest.raises(ScriptedError):
        async for event in session.drain():
            seen.append(event)

    assert [type(e).__name__ for e in seen] == ["ToolUse"]


def _gate(**body) -> dict:
    return {
        "name": "s",
        "plays": [
            {
                "match": {},
                "events": [{"permission": {"tool": "Bash", **body}}],
            }
        ],
    }


async def test_an_unanswered_permission_takes_the_timeout_branch():
    """The abandoned-approval path. Live inference cannot be asked to sit on a
    prompt for a fixed interval, so this is only testable scripted."""
    session = _driven(
        _gate(
            timeout_ms=50,
            on_allow=[{"text": "ran it"}],
            on_timeout=[{"text": "nobody answered, so I left it alone"}],
        )
    )
    parked = await _events(session)
    assert type(parked[-1]).__name__ == "PermissionRequest"

    after = await _events(session)

    assert [getattr(e, "text", None) for e in after] == [
        "nobody answered, so I left it alone"
    ]


async def test_a_timeout_falls_back_to_the_deny_branch():
    """Not answering is a refusal to grant. A scenario only needs `on_timeout`
    when it wants to say something different about it."""
    session = _driven(
        _gate(timeout_ms=50, on_allow=[{"text": "ran it"}], on_deny=[{"text": "skipped"}])
    )
    await _events(session)

    after = await _events(session)

    assert [getattr(e, "text", None) for e in after] == ["skipped"]


async def test_answering_in_time_beats_the_timeout():
    session = _driven(
        _gate(
            timeout_ms=5_000,
            on_allow=[{"text": "ran it"}],
            on_timeout=[{"text": "nobody answered"}],
        )
    )
    parked = await _events(session)
    session.resolve(PermissionDecision(request_id=parked[-1].request_id, allow=True))

    after = await _events(session)

    assert [getattr(e, "text", None) for e in after] == ["ran it"]


async def test_a_permission_without_a_timeout_waits_indefinitely():
    """The default has to stay 'wait': a gate that silently expired would turn
    a slow reviewer into a denial nobody scripted."""
    session = _driven(_gate(on_allow=[{"text": "ran it"}], on_deny=[{"text": "skipped"}]))
    parked = await _events(session)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_events(session), timeout=0.2)


# --- Pacing ---------------------------------------------------------------
#
# Scenarios run flat out by default, which is what makes the suite fast. A demo
# or a loading-state test wants the opposite, and PLAYBACK_SPEED is the dial.
# Bounds are loose on purpose: these assert that pacing happens, not that the
# event loop is a stopwatch.

SLOW = 150  # ms — long enough to measure, short enough not to drag the suite


async def _elapsed(session: BackendSession) -> float:
    started = time.monotonic()
    await _events(session)
    return time.monotonic() - started


def _one(body: dict, **scenario) -> dict:
    return {"name": "s", "plays": [{"match": {}, "events": [body]}], **scenario}


async def test_delays_are_off_by_default(monkeypatch):
    """An unset PLAYBACK_SPEED means CI never waits on scripted pacing."""
    monkeypatch.delenv("PLAYBACK_SPEED", raising=False)

    elapsed = await _elapsed(_driven(_one({"text": {"text": "hi", "delay_ms": SLOW}})))

    assert elapsed < 0.1


async def test_delay_ms_paces_an_event_when_speed_is_on(monkeypatch):
    monkeypatch.setenv("PLAYBACK_SPEED", "1.0")

    elapsed = await _elapsed(_driven(_one({"text": {"text": "hi", "delay_ms": SLOW}})))

    assert elapsed >= SLOW / 1000 * 0.8


async def test_playback_speed_scales_delays(monkeypatch):
    """The same scenario, run at a tenth of the pace."""
    monkeypatch.setenv("PLAYBACK_SPEED", "0.1")

    elapsed = await _elapsed(_driven(_one({"text": {"text": "hi", "delay_ms": SLOW}})))

    assert elapsed < SLOW / 1000 * 0.8


async def test_a_scenario_default_paces_events_that_do_not_say(monkeypatch):
    monkeypatch.setenv("PLAYBACK_SPEED", "1.0")

    elapsed = await _elapsed(
        _driven(_one({"text": "hi"}, defaults={"delay_ms": SLOW}))
    )

    assert elapsed >= SLOW / 1000 * 0.8


async def test_a_permission_can_be_paced_too(monkeypatch):
    """"Thinking, then asking" is the shape a frontend wants to demo; a gate
    that arrives instantly reads as a bug in the UI, not in the script."""
    monkeypatch.setenv("PLAYBACK_SPEED", "1.0")
    session = _driven(_gate(delay_ms=SLOW, on_allow=[{"text": "ran it"}]))

    started = time.monotonic()
    parked = await _events(session)

    assert type(parked[-1]).__name__ == "PermissionRequest"
    assert time.monotonic() - started >= SLOW / 1000 * 0.8


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


async def test_a_scripted_error_fails_the_task(on_scenario):
    """The whole point of scripting a failure: a frontend's error path gets
    exercised on demand, through a2acode's real failure handling."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "crash the sandbox please")

    assert capture.final_state == "failed"


async def test_work_done_before_a_scripted_error_is_still_reported(on_scenario):
    """A failed run is not a blank one — what happened before the failure is
    what a user needs to understand it."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "crash the sandbox please")

    assert any("No space left on device" in text for text in capture.status_texts)


async def test_an_expired_approval_answers_a_late_caller_with_the_timeout_branch(
    on_scenario,
):
    """The whole abandoned-approval round trip over the wire: the task parks,
    the timeout fires with nobody listening, and the caller who finally says
    "allow" finds it was decided without them."""
    client = await on_scenario("vocabulary.yaml")
    parked = await send(client, "deploy to production")
    assert parked.final_state == "input_required"

    await asyncio.sleep(0.5)
    resumed = await send(
        client, "allow", task_id=parked.task_id, context_id=parked.context_id
    )

    assert "production is untouched" in resumed.artifact_text()
    assert resumed.completion_metadata.get("stop_reason") == "permission_timeout"


# --- Plans -----------------------------------------------------------------
#
# Shape pinned against a real ACP-backed Claude run — see a2a-experiments
# docs/captures/phase5-acp-plan-run.jsonl. Three plan updates, one artifact id,
# `- [ ]` / `- [>]` / `- [x]` as the marks. The claude backend cannot produce
# one of these at all today (docs/UPSTREAM.md), which is the reason a frontend
# needs the scripted path rather than a live one.


def _plans(capture) -> list:
    return [a for a in capture.artifacts if a.name == "plan"]


async def test_a_plan_arrives_as_a_markdown_checklist(on_scenario):
    """The whole reason to watch a plan: which step the agent is on."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "show me the plan")

    first = _plans(capture)[0]
    assert first.parts[0].media_type == "text/markdown"
    assert parts_text(first.parts) == (
        "- [>] Read the failing test\n"
        "- [ ] Fix the parser\n"
        "- [ ] Run the suite\n"
    )


async def test_a_plan_update_replaces_the_one_before_it(on_scenario):
    """Reported by replacement, not by delta. A consumer that appended these
    would show the same three steps three times over."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "show me the plan")

    plans = _plans(capture)
    assert len(plans) == 3
    assert len({p.artifact_id for p in plans}) == 1
    assert parts_text(plans[-1].parts) == (
        "- [x] Read the failing test\n"
        "- [x] Fix the parser\n"
        "- [x] Run the suite\n"
    )


async def test_a_high_priority_step_says_so(on_scenario):
    """`priority` is the one PlanStep field the ACP run never exercised, so it
    is scripted here rather than assumed."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "show me the plan")

    assert "- [ ] (high) Fix the parser" in parts_text(_plans(capture)[1].parts)


async def test_a_prose_plan_is_carried_verbatim(on_scenario):
    """Not every agent keeps a checklist. Flattening prose into invented steps
    would be the rig lying about what the agent said."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "plan this out in prose")

    assert parts_text(_plans(capture)[0].parts) == (
        "## Approach\n\nStart with the parser, then widen to the callers.\n"
    )


async def test_a_plan_kept_in_a_file_arrives_as_a_pointer(on_scenario):
    """The third Plan variant: the agent's plan lives somewhere else."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "keep the plan in a file")

    assert "PLAN.md" in parts_text(_plans(capture)[0].parts)


async def test_an_abandoned_plan_clears_the_checklist(on_scenario):
    """A frontend that keeps rendering a plan the agent walked away from is
    showing work that is not happening."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "abandon the plan")

    plans = _plans(capture)
    assert len(plans) == 2
    assert parts_text(plans[-1].parts) == ""


async def test_stop_reason_reaches_the_client(on_scenario):
    """Tells a truncated answer from a finished one. Already plumbed through
    a2acode's result metadata; this pins it so the scenario vocabulary can
    rely on it."""
    client = await on_scenario("vocabulary.yaml")

    capture = await send(client, "list every endpoint, hit the ceiling if you must")

    assert capture.completion_metadata.get("stop_reason") == "max_tokens"


# Cancelling a task that is genuinely mid-run is worse than cancelling a parked
# one (see test_lifecycle.py): the task is not merely left alone, it is left in
# `working` with no terminal state, forever. Traced:
#
#   a2a-sdk: ActiveTask.cancel (active_task.py) cancels `self._producer_task`
#   *first*, and only then awaits `self._agent_executor.cancel(...)`. The
#   producer is the task running `execute`.
#
#   a2acode: `_pump`'s `except asyncio.CancelledError` branch is the one path
#   that deliberately emits no status — it drops the session and re-raises,
#   because that branch was written for a disconnected client, where there is
#   nobody left to tell. So the executor never writes a terminal state.
#
#   The `updater.cancel()` inside a2acode's own `cancel()` does then enqueue a
#   canceled status, but by then it does not reach the task store, and
#   `ActiveTask.cancel` returns the task it read *before* cancelling — which
#   still says `working`. That is what the caller gets back.
#
# So the ordering is the bug: the only component that owns the task's terminal
# state is killed before it can write one. Fixable in either place — the SDK
# awaiting the executor's cancel before killing the producer, or a2acode
# distinguishing "client vanished" from "cancelled on purpose".
#
# Playback is what made this cheap to see: a 3s scripted delay, no API key, no
# inference, reproducible every run. strict=True so it flips loudly if fixed.
cancel_mid_run_leaves_the_task_working = pytest.mark.xfail(
    strict=True,
    reason="a2a-sdk V2: the producer is cancelled before the executor can "
    "write a terminal state, so the task is stranded in `working`",
)


async def _cancel_mid_run(client) -> tuple:
    """Start a slow turn and cancel it the moment the driver is provably
    running — the tool_use lands before the delay, so it is the signal."""
    fired = False
    response = None

    async def hook(event, capture):
        nonlocal fired, response
        if fired or event.WhichOneof("payload") != "status_update":
            return
        status = event.status_update.status
        if not status.HasField("message"):
            return
        if "sleep 30" not in parts_text(status.message.parts):
            return
        fired = True
        response = await client.cancel_task(CancelTaskRequest(id=capture.task_id))

    capture = await send(client, "take your time with this one", on_event=hook)
    assert fired, "never saw the tool_use that says the driver is running"
    return capture, response


@cancel_mid_run_leaves_the_task_working
async def test_a_cancel_lands_while_the_run_is_still_going(on_scenario):
    """The cancel case a parked task cannot cover: here the producer really is
    running, so the a2a-sdk guard the parked tests trip over does not apply.
    A UI offering "stop" during a long turn depends on this."""
    client = await on_scenario("vocabulary.yaml", {"PLAYBACK_SPEED": "1.0"})

    capture, _ = await _cancel_mid_run(client)

    assert capture.final_state == "canceled"


@cancel_mid_run_leaves_the_task_working
async def test_a_cancelled_run_reaches_a_terminal_state(on_scenario):
    """Weaker than the above and still fails: a caller who cancels should at
    least be able to stop polling."""
    client = await on_scenario("vocabulary.yaml", {"PLAYBACK_SPEED": "1.0"})

    capture, _ = await _cancel_mid_run(client)
    task = await client.get_task(GetTaskRequest(id=capture.task_id))

    assert state_name(task.status.state) in {"canceled", "failed", "completed"}


async def test_a_cancelled_run_is_stranded_in_working(on_scenario):
    """Documents today's behavior, so the contrast with the xfails above is
    explicit. The stream just stops; nothing ever closes the task out."""
    client = await on_scenario("vocabulary.yaml", {"PLAYBACK_SPEED": "1.0"})

    capture, response = await _cancel_mid_run(client)
    task = await client.get_task(GetTaskRequest(id=capture.task_id))

    assert capture.final_state == "working"
    assert state_name(response.status.state) == "working"
    assert state_name(task.status.state) == "working"


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
