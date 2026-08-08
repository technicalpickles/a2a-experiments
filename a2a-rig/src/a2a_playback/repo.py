"""Repos: who a fake agent is, and which scripts it can run.

A repo is a directory. ``repo.yaml`` declares identity and pacing;
``scenarios/*.yaml`` hold the plays. The **directory name is the repo id** —
there is no ``name:`` field, so a repo cannot end up with two names that
disagree.

A repo's scenario files are read in filename order and their plays
concatenated into one list, matched first-match-wins exactly as a single
document's plays are. That is what makes M3 additive: a recorded scenario is a
new file in ``scenarios/``, and nothing about the format changes to accept it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .scenario import Play, Scenario, ScenarioError, load_scenario

REPO_FILE = "repo.yaml"
SCENARIOS_DIR = "scenarios"


class RepoError(Exception):
    """A repo directory is malformed, missing, or empty.

    Distinct from ``ScenarioError``: that one means a script is wrong, this one
    means the thing meant to be running scripts is not a repo.
    """


@dataclass
class Repo:
    """One fake repo: an agent identity plus the scripts it can play."""

    repo_id: str
    card_name: str | None = None
    card_description: str | None = None
    defaults: dict[str, Any] = field(default_factory=dict)
    scenarios: list[Scenario] = field(default_factory=list)
    path: Path | None = None

    @property
    def plays(self) -> list[Play]:
        return [play for scenario in self.scenarios for play in scenario.plays]

    @property
    def default_delay_ms(self) -> float:
        return float(self.defaults.get("delay_ms", 0) or 0)

    def select(self, prompt: str, turn: int) -> Play:
        """First match wins. No match is an error, never a plausible answer."""
        for play in self.plays:
            if play.match.matches(prompt, turn):
                return play
        raise ScenarioError(
            f"repo {self.repo_id!r}: no play matched turn {turn} of {prompt!r}. "
            f"Add a matching play, or a `- match: {{}}` default if you want a "
            f"catch-all. Refusing to guess."
        )


def load_repo(path: str | Path) -> Repo:
    path = Path(path)

    repo_file = path / REPO_FILE
    if not repo_file.is_file():
        raise RepoError(f"{path}: not a repo — no {REPO_FILE}")

    try:
        raw = yaml.safe_load(repo_file.read_text()) or {}
    except yaml.YAMLError as exc:
        raise RepoError(f"{repo_file}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise RepoError(f"{repo_file}: expected a mapping at the top level")

    scenario_dir = path / SCENARIOS_DIR
    files = sorted(scenario_dir.glob("*.yaml")) if scenario_dir.is_dir() else []
    if not files:
        raise RepoError(
            f"{path}: no scenarios — expected {SCENARIOS_DIR}/*.yaml. A repo that "
            f"can answer nothing is a mistake, not a valid state"
        )

    card = raw.get("card") or {}
    repo = Repo(
        repo_id=path.name,
        card_name=card.get("name"),
        card_description=card.get("description"),
        defaults=raw.get("defaults") or {},
        scenarios=[load_scenario(f) for f in files],
        path=path,
    )
    _reject_shadowed_plays(repo)
    _reject_shadowed_recordings(repo)
    return repo


def load_repos(root: str | Path) -> list[Repo]:
    root = Path(root)
    if not root.is_dir():
        raise RepoError(f"{root}: not a directory")
    homes = sorted(d for d in root.iterdir() if d.is_dir())
    if not homes:
        raise RepoError(f"{root}: no repos — a rig serving nothing is a mistake")
    return [load_repo(home) for home in homes]


def _reject_shadowed_plays(repo: Repo) -> None:
    """Catch-all-must-be-last, across the repo's whole concatenated play list.

    Enforced here rather than per file: a `match: {}` at the end of
    `01-first.yaml` is last in its own document but shadows every play in
    `02-second.yaml`, and a scenario that silently never runs reads as covered
    behavior.
    """
    pairs = [(s, p) for s in repo.scenarios for p in s.plays]
    for scenario, play in pairs[:-1]:
        if play.match.is_default:
            raise RepoError(
                f"{scenario.path}: {play.describe()} is a catch-all but is not "
                f"last in repo {repo.repo_id!r} ({repo.path}); every play after "
                f"it is unreachable"
            )


def _reject_shadowed_recordings(repo: Repo) -> None:
    """The self-check in the other direction from ``_reject_shadowed_plays``.

    A hand-written play sorting *ahead* of a promoted recording can shadow it
    exactly the way billing-api's `contains: "run the tests"` once shadowed a
    `20-*.yaml` recording of that same prompt: silently, since the recording
    still loads and still replays, just never for the prompt it exists to
    answer. `recorded.prompts` exists so a re-record run has a source list to
    work from; checking it here is a second, free use of the same list — a
    promoted recording that is shadowed now fails loudly at boot instead of
    quietly answering wrong on replay.

    `prompts` is a source list, not an index into `plays` (scrubbing can drop
    or reorder either independently), so this matches by selecting the play a
    prompt actually resolves to and checking its identity, not its position.

    Shadowing runs both ways and both are now guarded: this catches a
    recording being shadowed by an earlier-sorting play; the *existing* test
    suite catches the opposite — a recording shadowing a hand-written play —
    because `tests/conftest.py`'s `permission_prompt`/`denied_marker`
    fixtures depend on billing-api's hand-written gate scenario and would go
    red if a recording ever ate that prompt first.
    """
    for scenario in repo.scenarios:
        prompts = (scenario.recorded or {}).get("prompts") or []
        for prompt in prompts:
            play = repo.select(prompt, turn=1)
            if not any(candidate is play for candidate in scenario.plays):
                winner = next(
                    s for s in repo.scenarios
                    if any(candidate is play for candidate in s.plays)
                )
                raise RepoError(
                    f"{scenario.path}: recorded prompt {prompt!r} no longer "
                    f"selects a play from this scenario — {play.describe()} in "
                    f"{winner.path} wins instead. Something earlier in "
                    f"{repo.repo_id!r}'s concatenated play list now shadows "
                    f"this recording"
                )
