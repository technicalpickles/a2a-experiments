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


async def test_a_recorded_gate_replays_and_the_other_branch_is_loud(tmp_path, http_client):
    """Recording the allow path gives on_allow and nothing else.

    The gate spans two `execute` calls but one `drive()` — the session parks
    inside `request_permission` — so it is still exactly one recorded play.
    """
    out = tmp_path / "gated.yaml"
    prompt = "add a /health endpoint and run the tests"

    with serve(repo=REPO, record_out=out) as url:
        client = await _client(url, http_client)
        parked = await send(client, prompt)
        assert parked.final_state == "input_required"
        resumed = await send(
            client, "allow", task_id=parked.task_id, context_id=parked.context_id
        )
        assert resumed.final_state == "completed"

    plays = load_scenario(out).plays
    assert len(plays) == 1, "a gated turn is one drive, so one play"
    permission = next(e["permission"] for e in plays[0].events if "permission" in e)
    assert "on_allow" in permission
    assert "on_deny" not in permission, "a run that was allowed never saw a denial"


async def test_replaying_a_recorded_gate_and_denying_is_loud(tmp_path, http_client):
    """The other half: the unrecorded branch fails the task rather than
    completing it empty."""
    out = tmp_path / "gated.yaml"
    prompt = "add a /health endpoint and run the tests"

    with serve(repo=REPO, record_out=out) as url:
        client = await _client(url, http_client)
        parked = await send(client, prompt)
        await send(client, "allow", task_id=parked.task_id, context_id=parked.context_id)

    with serve(backend="playback", repo=_promote(out, tmp_path / "replayed")) as url:
        client = await _client(url, http_client)
        parked = await send(client, prompt)
        denied = await send(
            client, "deny", task_id=parked.task_id, context_id=parked.context_id
        )

    assert denied.final_state == "failed"
