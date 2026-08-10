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


def test_a_permission_with_no_branches_is_rejected_at_load(tmp_path):
    """A gate that can do nothing on any answer is a mistake, not a valid state."""
    home = _repo(
        tmp_path,
        "gateless",
        scenarios={
            "only.yaml": """
plays:
  - match: {}
    events:
      - permission: { tool: Bash, input: { command: "ls" } }
"""
        },
    )
    with pytest.raises(ScenarioError, match="at least one branch"):
        load_repo(home)


def test_load_repos_reads_every_directory(tmp_path):
    _repo(tmp_path, "beta")
    _repo(tmp_path, "alpha")

    assert [r.repo_id for r in load_repos(tmp_path)] == ["alpha", "beta"]


def test_load_repos_skips_dot_prefixed_directories(tmp_path):
    """Hidden directories (e.g., .claude, .vscode) are ignored when scanning for repos."""
    _repo(tmp_path, "beta")
    _repo(tmp_path, "alpha")
    # Create a hidden directory that is not a repo
    (tmp_path / ".claude").mkdir()

    assert [r.repo_id for r in load_repos(tmp_path)] == ["alpha", "beta"]


def test_an_empty_repos_directory_is_an_error(tmp_path):
    with pytest.raises(RepoError, match="no repos"):
        load_repos(tmp_path)


# --- `recorded:` provenance (M3 groundwork) -----------------------------------


_RECORDED_PLAYS = """
plays:
  - match: { contains: "hello" }
    events:
      - text: "hi"
recorded:
  recorded_at: "2026-08-07T00:00:00Z"
  source_prompt: "hello"
  backend: claude
"""


def test_a_recorded_block_loads_and_its_plays_still_work(tmp_path):
    home = _repo(tmp_path, "r", scenarios={"only.yaml": _RECORDED_PLAYS})

    repo = load_repo(home)

    assert repo.select("hello", 1).events == [{"text": "hi"}]


def test_the_recorded_block_is_reachable_on_the_scenario(tmp_path):
    home = _repo(tmp_path, "r", scenarios={"only.yaml": _RECORDED_PLAYS})

    scenario = load_repo(home).scenarios[0]

    assert scenario.recorded == {
        "recorded_at": "2026-08-07T00:00:00Z",
        "source_prompt": "hello",
        "backend": "claude",
    }


def test_a_scenario_without_recorded_defaults_to_empty(tmp_path):
    home = _repo(tmp_path, "r")

    scenario = load_repo(home).scenarios[0]

    assert scenario.recorded == {}


def test_an_unknown_top_level_key_that_is_not_recorded_is_still_rejected(tmp_path):
    """`recorded` is the one permitted companion to `plays` — a typo like
    `play:` or `plays :` (which yaml parses as a sibling key) should still
    fail loudly rather than silently doing nothing."""
    home = _repo(
        tmp_path,
        "r",
        scenarios={"only.yaml": 'plays:\n  - match: {}\n    events:\n      - text: "hi"\nbogus: 1\n'},
    )

    with pytest.raises(ScenarioError, match="bogus"):
        load_repo(home)


def test_a_non_mapping_recorded_is_rejected(tmp_path):
    home = _repo(
        tmp_path,
        "r",
        scenarios={
            "only.yaml": 'plays:\n  - match: {}\n    events:\n      - text: "hi"\nrecorded: "not a mapping"\n'
        },
    )

    with pytest.raises(ScenarioError, match="recorded"):
        load_repo(home)


# --- scenario file prefixes (M3 promotion-as-mv) --------------------------------


@pytest.mark.parametrize("repo_name", ["billing-api", "checkout-web", "infra-terraform"])
def test_a_shipped_repo_accepts_a_new_scenario_file_without_reordering(repo_name):
    """Promotion must be a `mv`. A catch-all living in the same file as real
    plays means any file sorting after it shadows everything it contains."""
    from pathlib import Path

    home = Path(__file__).parents[1] / "repos" / repo_name
    scenarios = sorted(p.name for p in (home / "scenarios").glob("*.yaml"))
    assert scenarios[-1] == "99-default.yaml", (
        f"the catch-all must sort last; got {scenarios}"
    )
    for name in scenarios[:-1]:
        assert not name.startswith("99-"), f"{name} would compete with the catch-all"
    load_repo(home)


@pytest.mark.parametrize("turn", [1, 2])
def test_a_new_scenario_file_drops_in_without_shadowing(tmp_path, turn):
    """The point of the prefixes: a recorded file lands ahead of the
    hand-written plays and the catch-all, and everything stays reachable —
    including on turn 1, the shape a real `--record` capture most commonly
    takes. That only holds because billing-api's broad `turn: 1` greeting
    play was moved out of 30-refactor.yaml into 90-greeting.yaml, which
    sorts after a promoted 20-*.yaml file; before that move this test's
    turn=1 case failed for an unrelated reason (the greeting, not the
    catch-all, shadowed it)."""
    import shutil
    from pathlib import Path

    home = tmp_path / "billing-api"
    shutil.copytree(Path(__file__).parents[1] / "repos" / "billing-api", home)
    (home / "scenarios" / "20-recorded.yaml").write_text(
        'plays:\n'
        '  - match: { regex: "^a recorded prompt$" }\n'
        '    events:\n'
        '      - text: "from a recording"\n'
        '      - result: { num_turns: 1 }\n'
    )

    repo = load_repo(home)  # must not raise: the catch-all is still last
    play = repo.select("a recorded prompt", turn=turn)
    assert play.events[0] == {"text": "from a recording"}, "shadowed by an earlier-sorting play"


# --- `recorded.prompts` self-check (a promoted recording shadowed at load) -----


def test_a_shadowed_recorded_prompt_fails_to_load(tmp_path):
    """The recording's own source prompt no longer selects a play from its
    own scenario — an earlier-sorting hand-written play has eaten it, the
    same way billing-api's `contains: "run the tests"` once could have eaten
    a promoted recording of that exact prompt."""
    home = _repo(
        tmp_path,
        "r",
        scenarios={
            "10-handwritten.yaml": (
                'plays:\n'
                '  - match: { contains: "run the tests" }\n'
                '    events:\n'
                '      - text: "handwritten"\n'
            ),
            "20-recorded.yaml": (
                'plays:\n'
                '  - match: { regex: "^please run the tests now$" }\n'
                '    events:\n'
                '      - text: "recorded"\n'
                'recorded:\n'
                '  prompts:\n'
                '    - "please run the tests now"\n'
            ),
        },
    )

    with pytest.raises(RepoError, match="no longer selects"):
        load_repo(home)


def test_an_unshadowed_recorded_prompt_loads_clean(tmp_path):
    home = _repo(
        tmp_path,
        "r",
        scenarios={
            "20-recorded.yaml": (
                'plays:\n'
                '  - match: { regex: "^please run the tests now$" }\n'
                '    events:\n'
                '      - text: "recorded"\n'
                'recorded:\n'
                '  prompts:\n'
                '    - "please run the tests now"\n'
            ),
        },
    )

    repo = load_repo(home)  # must not raise

    assert repo.select("please run the tests now", turn=1).events == [{"text": "recorded"}]
