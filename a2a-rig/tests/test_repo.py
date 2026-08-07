"""Repo loading: identity, defaults, and combining a repo's scenarios.

A repo has scenarios; it is not one. `repo.yaml` says who the agent is,
`scenarios/*.yaml` say what it does, and the directory name is the id — so
there is no second place for a repo's name to disagree with itself.
"""

from __future__ import annotations

import pytest

from a2a_playback.repo import RepoError, load_repo, load_repos
from a2a_playback.scenario import ScenarioError


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _repo(root, name: str, *, card: str = "", scenarios: dict[str, str] | None = None):
    """A repo on disk. `scenarios` maps filename -> YAML body."""
    home = root / name
    _write(home / "repo.yaml", card or 'card:\n  name: x\n  description: "d"\n')
    for filename, body in (scenarios or {"only.yaml": _PLAYS}).items():
        _write(home / "scenarios" / filename, body)
    return home


_PLAYS = """
plays:
  - match: { contains: "hello" }
    events:
      - text: "hi"
"""


def test_the_directory_name_is_the_repo_id(tmp_path):
    """Not a `name:` field — one source of truth, so nothing can drift."""
    home = _repo(tmp_path, "billing-api")

    assert load_repo(home).repo_id == "billing-api"


def test_card_identity_comes_from_repo_yaml(tmp_path):
    home = _repo(
        tmp_path,
        "billing-api",
        card='card:\n  name: billing-api\n  description: "Fake billing repo"\n',
    )

    repo = load_repo(home)

    assert repo.card_name == "billing-api"
    assert repo.card_description == "Fake billing repo"


def test_defaults_come_from_repo_yaml(tmp_path):
    home = _repo(
        tmp_path,
        "r",
        card='card:\n  name: r\ndefaults:\n  delay_ms: 250\n',
    )

    assert load_repo(home).default_delay_ms == 250


def test_plays_concatenate_across_scenarios_in_filename_order(tmp_path):
    home = _repo(
        tmp_path,
        "r",
        scenarios={
            "02-second.yaml": 'plays:\n  - match: { contains: "b" }\n    events:\n      - text: "B"\n',
            "01-first.yaml": 'plays:\n  - match: { contains: "a" }\n    events:\n      - text: "A"\n',
        },
    )

    repo = load_repo(home)

    assert [p.match.contains for p in repo.plays] == ["a", "b"]


def test_first_match_wins_across_scenario_files(tmp_path):
    home = _repo(
        tmp_path,
        "r",
        scenarios={
            "01-first.yaml": 'plays:\n  - match: {}\n    events:\n      - text: "first"\n',
        },
    )

    play = load_repo(home).select("anything", 1)

    assert play.events == [{"text": "first"}]


def test_a_catch_all_in_an_earlier_file_is_rejected(tmp_path):
    """The existing catch-all-must-be-last rule, now across file boundaries —
    otherwise a later scenario file would silently never run."""
    home = _repo(
        tmp_path,
        "r",
        scenarios={
            "01-first.yaml": 'plays:\n  - match: {}\n    events:\n      - text: "shadows"\n',
            "02-second.yaml": 'plays:\n  - match: { contains: "b" }\n    events:\n      - text: "B"\n',
        },
    )

    with pytest.raises(RepoError, match="unreachable"):
        load_repo(home)


def test_a_directory_without_repo_yaml_is_an_error(tmp_path):
    home = tmp_path / "r"
    _write(home / "scenarios" / "only.yaml", _PLAYS)

    with pytest.raises(RepoError, match="repo.yaml"):
        load_repo(home)


def test_a_repo_with_no_scenarios_is_an_error(tmp_path):
    """A repo that can answer nothing is a mistake, not a valid state — and
    saying so at startup beats failing every turn later."""
    home = tmp_path / "r"
    _write(home / "repo.yaml", 'card:\n  name: r\n')

    with pytest.raises(RepoError, match="no scenarios"):
        load_repo(home)


def test_a_malformed_scenario_names_its_file(tmp_path):
    home = _repo(
        tmp_path,
        "r",
        scenarios={"broken.yaml": 'plays:\n  - match: {}\n    events:\n      - bogus: "x"\n'},
    )

    with pytest.raises(ScenarioError, match="broken.yaml"):
        load_repo(home)


def test_load_repos_reads_every_directory(tmp_path):
    _repo(tmp_path, "beta")
    _repo(tmp_path, "alpha")

    assert [r.repo_id for r in load_repos(tmp_path)] == ["alpha", "beta"]


def test_an_empty_repos_directory_is_an_error(tmp_path):
    with pytest.raises(RepoError, match="no repos"):
        load_repos(tmp_path)
