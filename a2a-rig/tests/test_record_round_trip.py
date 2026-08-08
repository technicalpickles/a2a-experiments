"""The property M3 rests on: a recording replays as what it recorded.

`to_scenario_event` and `PlaybackBackend._to_backend_event` are inverses living
in different files. Unit tests pin them pairwise; this pins them end to end,
through a real server, a real client, and a real file on disk — for free,
because the thing being recorded is the scripted backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from a2a.client import create_client
from a2a.client.client import ClientConfig

from a2a_playback.repo import load_repo
from a2a_playback.scenario import load_scenario
from a2a_rig.events import send
from a2a_rig.server import serve

REPO = Path(__file__).resolve().parents[1] / "repos" / "billing-api"


def _promote(recording: Path, into: Path) -> Path:
    """Stand the recording up as a repo of its own, the way a `mv` would."""
    (into / "scenarios").mkdir(parents=True)
    (into / "repo.yaml").write_text('card:\n  description: "replay"\n')
    (into / "scenarios" / "10-recorded.yaml").write_text(recording.read_text())
    load_repo(into)  # must load clean, or promotion was never going to work
    return into


async def _client(url, http_client):
    return await create_client(
        url, ClientConfig(streaming=True, httpx_client=http_client)
    )


async def test_a_recorded_playback_run_replays_as_itself(tmp_path, http_client):
    """Record billing-api answering a prompt, then serve the recording and ask
    the same question. The two streams must agree."""
    out = tmp_path / "recorded.yaml"
    prompt = "explain the tax module"

    with serve(repo=REPO, record_out=out) as url:
        original = await send(await _client(url, http_client), prompt)

    scenario = load_scenario(out)
    assert scenario.recorded["prompts"] == [prompt]
    assert len(scenario.plays) == 1

    with serve(backend="playback", repo=_promote(out, tmp_path / "replayed")) as url:
        replayed = await send(await _client(url, http_client), prompt)

    assert replayed.artifact_text() == original.artifact_text()
    assert replayed.final_state == original.final_state
    assert replayed.status_texts == original.status_texts


async def test_a_recorded_gate_replays_the_allow_branch_and_the_other_branch_is_loud(
    tmp_path, http_client
):
    """The gate spans two `execute` calls but one `drive()` — the session
    parks inside `request_permission` — so it is still exactly one recorded
    play, holding only the branch actually taken (on_allow).

    Replaying it must reproduce both halves of that recorded run — the parked
    phase (plan, the read, the pre-gate text) and the resumed phase (the
    nested on_allow events: another tool_use, the file_change already landed
    on disk, the closing text and result) — not just the gate's shape. Then,
    on a fresh task against the same replay server, denying must fail loudly
    rather than complete on a branch that was never scripted.
    """
    out = tmp_path / "gated.yaml"
    prompt = "add a /health endpoint and run the tests"

    with serve(repo=REPO, record_out=out) as url:
        client = await _client(url, http_client)
        recorded_parked = await send(client, prompt)
        assert recorded_parked.final_state == "input_required"
        recorded_resumed = await send(
            client,
            "allow",
            task_id=recorded_parked.task_id,
            context_id=recorded_parked.context_id,
        )
        assert recorded_resumed.final_state == "completed"

    plays = load_scenario(out).plays
    assert len(plays) == 1, "a gated turn is one drive, so one play"
    permission = next(
        (e["permission"] for e in plays[0].events if "permission" in e), None
    )
    assert permission is not None, "expected a recorded permission event"
    on_allow = permission.get("on_allow")
    assert on_allow, "on_allow must be non-empty, or replay could never reach it"
    assert "on_deny" not in permission, "a run that was allowed never saw a denial"
    kinds = {kind for event in on_allow for kind in event}
    assert {"tool_use", "text", "result"} <= kinds

    with serve(backend="playback", repo=_promote(out, tmp_path / "replayed")) as url:
        client = await _client(url, http_client)
        replayed_parked = await send(client, prompt)
        assert replayed_parked.final_state == "input_required"
        assert replayed_parked.artifact_text() == recorded_parked.artifact_text()
        assert replayed_parked.status_texts == recorded_parked.status_texts

        replayed_resumed = await send(
            client,
            "allow",
            task_id=replayed_parked.task_id,
            context_id=replayed_parked.context_id,
        )
        assert replayed_resumed.final_state == "completed"
        assert replayed_resumed.artifact_text() == recorded_resumed.artifact_text()
        assert replayed_resumed.status_texts == recorded_resumed.status_texts

        # A fresh task against the same replay server: the unscripted branch.
        parked_again = await send(client, prompt)
        assert parked_again.final_state == "input_required"
        denied = await send(
            client,
            "deny",
            task_id=parked_again.task_id,
            context_id=parked_again.context_id,
        )

    assert denied.final_state == "failed"
