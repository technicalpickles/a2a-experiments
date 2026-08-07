# M2 Multi-Repo Rig Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-scenario `playback` server into a directory of fake repos, served from one process with a JSON registry, that a frontend and agent tests can develop against offline.

**Architecture:** A repo is a directory (`repo.yaml` for identity, `scenarios/*.yaml` for plays). One parent Starlette app serves a JSON index at `/` and mounts one `build_app()` per repo at `/repos/<name>/`. The single-repo-per-process path is retained as `--repo`, because it is what proves the registry abstraction is topology-independent.

**Tech Stack:** Python 3.13, `a2a-sdk` 1.1.2, a2acode v0.6.2 (pinned git dep), Starlette, uvicorn, pytest + pytest-asyncio (`asyncio_mode = "auto"`), PyYAML.

**Spec:** `docs/superpowers/specs/2026-08-07-m2-multi-repo-rig-design.md`

## Global Constraints

- All work happens in `a2a-rig/`. Run every command from that directory.
- Tests run with `uv run pytest`. The suite must pass against **both** backends: `uv run pytest --backend playback` and `uv run pytest --backend echo`.
- Baseline before this plan: **79 passed, 4 xfailed**, under 5s each. The 4 xfails are the upstream cancel bugs and must stay xfailed, not fixed.
- **The backend-agnostic suite (`test_card.py`, `test_lifecycle.py`, `test_multiturn.py`, `test_permission.py`, `test_stream.py`) must need zero edits.** If it needs edits, the split leaked into the wrong layer — stop and reassess.
- A repo's id is its **directory name**. `repo.yaml` has no `name:` field. Never derive identity from anywhere else.
- Scenario files contain `plays:` and nothing else. Identity and `defaults:` live only in `repo.yaml`.
- Task headers are kebab-case slugs, not numbers, so inserting a task never renumbers a reference.

---

### mount-lifespan

Retires the assumption the whole default topology rests on, before anything is built on it. Starlette does **not** run a mounted sub-app's lifespan; a2acode's `build_app()` uses a lifespan to initialize its task stores. Without explicit propagation, mounted repos would come up with uninitialized stores.

This task tests our propagation helper against dummy children, so it proves our code rather than a2acode's internals. The real end-to-end proof comes in `rig-app`.

**Files:**
- Create: `src/a2a_playback/mounting.py`
- Test: `tests/test_mounting.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `mount_lifespans(children: list[Starlette]) -> Callable` — builds a lifespan callable for a parent Starlette app that enters every child app's own lifespan context for the parent's lifetime.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mounting.py`:

```python
"""Mounted sub-apps do not get their lifespans run for free.

Starlette runs the lifespan of the app it is serving, not of anything mounted
inside it. a2acode's `build_app()` initializes its task stores in a lifespan,
so a mounted repo whose lifespan never ran would serve from uninitialized
stores. This pins the propagation helper that fixes it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.applications import Starlette

from a2a_playback.mounting import mount_lifespans


def _recording_app(log: list[str], name: str) -> Starlette:
    @asynccontextmanager
    async def lifespan(_app):
        log.append(f"start:{name}")
        try:
            yield
        finally:
            # try/finally, not a bare `yield` then append: unwinding throws the
            # exception in *at* the yield, so anything after it is skipped and
            # the shutdown this test is checking for would never be recorded.
            log.append(f"stop:{name}")

    return Starlette(lifespan=lifespan)


async def test_a_parent_runs_every_child_lifespan():
    log: list[str] = []
    children = [_recording_app(log, "a"), _recording_app(log, "b")]
    parent = Starlette(lifespan=mount_lifespans(children))

    async with parent.router.lifespan_context(parent):
        assert log == ["start:a", "start:b"]

    assert log == ["start:a", "start:b", "stop:b", "stop:a"]


async def test_a_child_that_fails_to_start_does_not_strand_its_siblings():
    """Otherwise a bad repo leaves the already-started ones un-shut-down."""
    log: list[str] = []

    @asynccontextmanager
    async def boom(_app):
        raise RuntimeError("child refused to start")
        yield  # pragma: no cover

    children = [_recording_app(log, "a"), Starlette(lifespan=boom)]
    parent = Starlette(lifespan=mount_lifespans(children))

    try:
        async with parent.router.lifespan_context(parent):
            pass
    except RuntimeError:
        pass

    assert log == ["start:a", "stop:a"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mounting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'a2a_playback.mounting'`

- [ ] **Step 3: Write the implementation**

Create `src/a2a_playback/mounting.py`:

```python
"""Lifespan propagation for mounted sub-apps.

Starlette runs the lifespan of the app being served and of nothing mounted
inside it. a2acode's `build_app()` initializes its task and push-notification
stores in a lifespan, so a mounted repo whose lifespan never ran would answer
requests against uninitialized stores. Serving N repos from one process
therefore means running N lifespans by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any


def mount_lifespans(children: list[Any]) -> Callable:
    """A parent lifespan that runs every child app's lifespan.

    An `AsyncExitStack` rather than a loop of `__aenter__` calls: if one child
    raises on startup, the stack unwinds the ones already started instead of
    leaving them running with nobody to shut them down.
    """

    @asynccontextmanager
    async def lifespan(_app):
        async with AsyncExitStack() as stack:
            for child in children:
                await stack.enter_async_context(
                    child.router.lifespan_context(child)
                )
            yield

    return lifespan
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mounting.py -v`
Expected: 2 passed

Both assertions in this task were checked against Starlette before the plan was written:
ordered startup with reverse shutdown, and a failing child unwinding the siblings already
started. If either behaves differently, the mounted topology is not viable — stop and report.

- [ ] **Step 5: Commit**

```bash
git add src/a2a_playback/mounting.py tests/test_mounting.py
git commit -m "Propagate lifespans to mounted sub-apps

Starlette runs the lifespan of the app it serves and nothing mounted inside
it, and a2acode initializes its stores in a lifespan. Serving N repos from
one process means running N lifespans by hand, unwinding cleanly if one
child refuses to start."
```

---

### repo-format

Splits the format: `scenario.py` keeps parsing and validating a plays document, and a new `repo.py` owns identity, defaults, and combining a repo's scenarios. No serving changes yet.

The catch-all-must-be-last rule **moves out of `scenario.py` entirely** and into repo loading, because it is a property of the concatenated play list. Left per-file, a catch-all in `01-x.yaml` would silently shadow `02-y.yaml`.

**Files:**
- Modify: `src/a2a_playback/scenario.py`
- Create: `src/a2a_playback/repo.py`
- Test: `tests/test_repo.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Scenario(plays: list[Play], path: Path | None)` — a plays document. No name, card, or defaults.
  - `parse_scenario(raw: dict, *, path: Path | None = None) -> Scenario`
  - `load_scenario(path: str | Path) -> Scenario`
  - `Repo(repo_id: str, card_name: str | None, card_description: str | None, defaults: dict, scenarios: list[Scenario], path: Path | None)` with `.plays -> list[Play]`, `.default_delay_ms -> float`, and `.select(prompt: str, turn: int) -> Play`
  - `RepoError(Exception)`
  - `load_repo(path: str | Path) -> Repo`
  - `load_repos(root: str | Path) -> list[Repo]`
  - `ScenarioError` keeps its name and meaning: the plays are wrong, or none matched.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_repo.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'a2a_playback.repo'`

- [ ] **Step 3: Strip identity out of `scenario.py`**

In `src/a2a_playback/scenario.py`, replace the module docstring, the `Scenario` dataclass, `load`, and `parse` with the versions below. **Keep `Match`, `Play`, `ScenarioError`, `message_of`, `_validate_event`, `_validate_plan`, `EVENT_NAMES`, and `MATCH_KEYS` exactly as they are.**

Replace the docstring:

```python
"""Scenario files: parse and validate one document of plays.

A scenario is a *script*, and only a script — a list of **plays**, each with
match rules and a list of events written in a2acode's own ``BackendEvent``
vocabulary. Who the agent running it is, and how it is paced, live in
``repo.py``: a repo has scenarios, it is not one. Keeping the vocabulary
identical to a2acode's is what will let recorded scenarios (M3) and
hand-written ones be the same format.

Validation is deliberately strict and up-front. A scenario with a typo'd event
name should fail when the server starts, not halfway through a turn that a
frontend is watching. The one rule that is *not* here is
catch-all-must-be-last: that is a property of a repo's whole concatenated play
list, so it lives in ``repo.py``.
"""
```

Replace the `Scenario` dataclass and the `load`/`parse` functions:

```python
@dataclass
class Scenario:
    """One document of plays, and where it came from."""

    plays: list[Play]
    path: Path | None = None


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path}: expected a mapping at the top level")
    return parse_scenario(raw, path=path)


def parse_scenario(raw: dict[str, Any], *, path: Path | None = None) -> Scenario:
    where = str(path) if path else "<scenario>"

    raw_plays = raw.get("plays")
    if not isinstance(raw_plays, list) or not raw_plays:
        raise ScenarioError(f"{where}: scenario needs a non-empty `plays` list")

    unknown = set(raw) - {"plays"}
    if unknown:
        raise ScenarioError(
            f"{where}: scenario has unexpected keys {sorted(unknown)}; a scenario "
            f"holds `plays` and nothing else — identity and defaults belong in "
            f"repo.yaml"
        )

    return Scenario(
        plays=[_parse_play(p, i, where) for i, p in enumerate(raw_plays, start=1)],
        path=path,
    )
```

Delete from `scenario.py`: the `select` method, the `default_delay_ms` property, the `name`/`card_name`/`card_description`/`defaults` fields, and the catch-all loop that raised `"unreachable"` (it moves to `repo.py`).

- [ ] **Step 4: Write `repo.py`**

Create `src/a2a_playback/repo.py`:

```python
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
                f"last in repo {repo.repo_id!r}; every play after it is unreachable"
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_repo.py -v`
Expected: 11 passed

The rest of the suite is expected to be broken at this point (`test_playback.py` still imports the old API). `backend-takes-repo` fixes it.

- [ ] **Step 6: Commit**

```bash
git add src/a2a_playback/scenario.py src/a2a_playback/repo.py tests/test_repo.py
git commit -m "Split repo identity from scenario scripts

A repo has scenarios; it is not one. repo.yaml carries identity and defaults,
scenarios/*.yaml carry plays and nothing else, and the directory name is the
id so there is no second source of truth to drift.

Catch-all-must-be-last moves to repo loading, since it is a property of the
concatenated play list: enforced per file, a catch-all at the end of one
scenario would silently shadow the next one."
```

---

### backend-takes-repo

Points `PlaybackBackend` and the single-repo server at `Repo`, and migrates the test instruments. Ends with the whole suite green again.

**Files:**
- Modify: `src/a2a_playback/backend.py`
- Modify: `src/a2a_playback/serve.py`
- Modify: `src/a2a_playback/__init__.py`
- Modify: `src/a2a_rig/server.py`
- Create: `tests/repos/vocabulary/repo.yaml`, `tests/repos/vocabulary/scenarios/probes.yaml`
- Create: `tests/repos/strict/repo.yaml`, `tests/repos/strict/scenarios/known.yaml`
- Delete: `tests/scenarios/vocabulary.yaml`, `tests/scenarios/strict.yaml`
- Modify: `tests/test_playback.py`

**Interfaces:**
- Consumes: `Repo`, `load_repo`, `RepoError` from `repo-format`.
- Produces:
  - `PlaybackBackend(repo: Repo)` with attribute `.repo`
  - `build_repo_app(repo: Repo, *, url: str) -> Starlette` in `serve.py`
  - `rig-serve --repo <dir> --host H --port P`
  - `a2a_rig.server.serve(backend=..., repo=<dir>, ...)` replacing `scenario=`

- [ ] **Step 1: Point the backend at a repo**

In `src/a2a_playback/backend.py`:

- change the import `from .scenario import Scenario, ScenarioError, message_of` to `from .repo import Repo` plus `from .scenario import ScenarioError, message_of`
- change the class docstring to `"""Emits scripted events from a repo's scenarios."""`
- replace `__init__` and the two `self.scenario` uses:

```python
    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        # contextId -> how many turns that conversation has seen. The `turn: N`
        # match rule counts within a context, so a fresh conversation replays
        # the repo's scripts from the top.
        self._turns: dict[str, int] = {}
```

In `drive`, `play = self.scenario.select(...)` becomes `play = self.repo.select(...)`.
In `_delay`, `delay_ms = self.scenario.default_delay_ms` becomes `delay_ms = self.repo.default_delay_ms`.

- [ ] **Step 2: Point the server at a repo**

Replace `build` and `main` in `src/a2a_playback/serve.py`:

```python
def build_repo_app(repo, *, url: str):
    """One repo's a2acode app, with the playback backend injected."""
    backend = PlaybackBackend(repo)
    return build_app(
        backend,
        url=url,
        card_name=repo.card_name,
        card_description=repo.card_description,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-serve", description="Serve scripted A2A agents from a repo directory."
    )
    parser.add_argument("--repo", help="Path to one repo directory.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument(
        "--log-level", default="info", help="uvicorn log level (default: info)."
    )
    args = parser.parse_args(argv)

    if not args.repo:
        parser.error("--repo is required")

    url = f"http://{args.host}:{args.port}/"
    try:
        app = build_repo_app(load_repo(args.repo), url=url)
    except (RepoError, ScenarioError) as exc:
        # Config problems are user errors, not crashes: say what is wrong and
        # exit, rather than burying it in a traceback.
        print(f"rig-serve: {exc}", file=sys.stderr)
        return 2

    print(f"rig-serve: repo={args.repo} card={url}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0
```

Update the imports at the top of `serve.py`:

```python
from .backend import PlaybackBackend
from .repo import RepoError, load_repo
from .scenario import ScenarioError
```

Delete the old `DEFAULT_SCENARIO`-style `--scenario` argument and the `build()` function it fed.

- [ ] **Step 3: Update the package exports**

Replace `src/a2a_playback/__init__.py`:

```python
"""A scenario-driven a2acode backend: the real A2A producer, minus the model."""

from .backend import PlaybackBackend
from .repo import Repo, RepoError, load_repo, load_repos
from .scenario import Scenario, ScenarioError, load_scenario

__all__ = [
    "PlaybackBackend",
    "Repo",
    "RepoError",
    "Scenario",
    "ScenarioError",
    "load_repo",
    "load_repos",
    "load_scenario",
]
```

- [ ] **Step 4: Update the harness launcher**

In `src/a2a_rig/server.py`, replace `DEFAULT_SCENARIO` and `_playback_command`:

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = REPO_ROOT / "repos" / "billing-api"


def _playback_command(port: int, repo: str | Path | None) -> list[str]:
    """`playback` is ours, so it is served by rig-serve, not the a2acode CLI.

    Uses the running interpreter rather than a console script so the harness
    works from a bare checkout without an install step.
    """
    return [
        sys.executable,
        "-m",
        "a2a_playback.serve",
        "--repo",
        str(repo or DEFAULT_REPO),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
```

In `serve()`, rename the `scenario` parameter to `repo` and update the call:

```python
@contextmanager
def serve(
    backend: str = "echo",
    cwd: str | Path | None = None,
    extra_args: list[str] | None = None,
    port: int | None = None,
    repo: str | Path | None = None,
    env: dict[str, str] | None = None,
):
```

and inside, `cmd = _playback_command(port, repo)`.

- [ ] **Step 5: Migrate the test instruments**

Create `tests/repos/vocabulary/repo.yaml`:

```yaml
# Probes for the M1 event vocabulary. A test instrument rather than a demo:
# one play per behavior a frontend needs to render but cannot provoke on
# demand from live inference.
card:
  name: vocabulary
  description: "M1 vocabulary probes (playback)"
defaults:
  delay_ms: 0
```

Move the **`plays:` block only** from `tests/scenarios/vocabulary.yaml` into `tests/repos/vocabulary/scenarios/probes.yaml`, dropping its `name:`, `card:`, and `defaults:` keys and its file-level comment header (that comment now lives in `repo.yaml`). Keep every play and every per-play comment byte-for-byte.

Create `tests/repos/strict/repo.yaml`:

```yaml
# Deliberately has no catch-all play, so anything unscripted fails the turn.
# Used to prove a mis-scripted test never gets a plausible wrong answer.
card:
  name: strict
  description: "No catch-all (playback)"
```

Create `tests/repos/strict/scenarios/known.yaml`:

```yaml
plays:
  - match: { contains: "known question" }
    events:
      - text: "A scripted answer."
      - result: { cost_usd: 0.001, num_turns: 1, stop_reason: end_turn }
```

Then delete the old files:

```bash
git rm tests/scenarios/vocabulary.yaml tests/scenarios/strict.yaml
```

- [ ] **Step 6: Update `test_playback.py`**

Four mechanical changes:

1. Imports — replace the `a2a_playback` imports with:

```python
from a2a_playback import repo as repo_mod
from a2a_playback.backend import PlaybackBackend, ScriptedError
from a2a_playback.repo import Repo, RepoError, load_repo
from a2a_playback.scenario import Match, ScenarioError, parse_scenario
```

Keep whichever of these the file actually uses; drop the rest. `scenario_mod` usages become `repo_mod` where they concern defaults or selection.

2. Paths and fixtures — replace the `SCENARIOS` constant and rename the fixtures:

```python
REPOS = Path(__file__).parent / "repos"
```

`_scenario_servers` becomes `_repo_servers`, and its `serve(...)` call passes `repo=REPOS / name`. `on_scenario` becomes `on_repo`, and every call site changes from `await on_scenario("vocabulary.yaml")` to `await on_repo("vocabulary")`. The `strict.yaml` call site at the end of the file becomes `serve(backend="playback", repo=REPOS / "strict")`.

3. Direct-construction tests — every `parse({"name": "s", "plays": [...]})` becomes `parse_scenario({"plays": [...]})`. The helpers `_driven` and `_one` build a `Repo` instead of a `Scenario`:

```python
def _repo_of(plays: list[dict], **defaults) -> Repo:
    """A one-scenario repo, for tests that drive the backend directly."""
    return Repo(
        repo_id="t",
        defaults=defaults,
        scenarios=[parse_scenario({"plays": plays})],
    )
```

Route `_driven` and `_one` through `_repo_of` so `PlaybackBackend` receives a `Repo`.

4. Tests that move or change:
   - `test_scenario_needs_plays` keeps testing `parse_scenario` (a scenario still needs plays).
   - `test_a_catch_all_that_is_not_last_is_rejected` moves to `tests/test_repo.py` as the already-written `test_a_catch_all_in_an_earlier_file_is_rejected`; **delete it from `test_playback.py`** rather than leaving a duplicate.
   - `test_shipped_scenario_parses` becomes `test_shipped_repos_load`, calling `load_repos(Path(__file__).parents[1] / "repos")` and asserting at least 3 repos load. It will fail until `repos-ship`; mark it `@pytest.mark.xfail(reason="shipped repos land in repos-ship", strict=True)` in this task and remove the marker there.

- [ ] **Step 7: Run the full suite against both backends**

Run:
```bash
uv run pytest --backend playback
uv run pytest --backend echo
```
Expected: all pass, with the 4 pre-existing xfails plus the 1 temporary xfail from step 6, and the backend-agnostic suite unmodified.

- [ ] **Step 8: Commit**

```bash
git add -A src/a2a_playback src/a2a_rig tests/
git commit -m "Point the backend and server at repos

PlaybackBackend takes a Repo, rig-serve takes --repo, and the test
instruments become repos under tests/repos/ — vocabulary and strict really
are fake repos that happen to be used as instruments.

The backend-agnostic suite is untouched, which is the check that the split
landed in the right layer."
```

---

### rig-app

The multi-repo app: a JSON index at `/`, one mounted `build_app()` per repo, and `--repos`. This is where the mounted topology is proven end to end against real a2acode.

**Files:**
- Modify: `src/a2a_playback/serve.py`
- Modify: `src/a2a_rig/server.py`
- Test: `tests/test_rig.py` (create)

**Interfaces:**
- Consumes: `mount_lifespans` from `mount-lifespan`; `Repo`, `load_repos` from `repo-format`; `build_repo_app` from `backend-takes-repo`.
- Produces:
  - `index_document(repos: list[Repo], base_url: str) -> dict`
  - `build_rig_app(repos: list[Repo], *, base_url: str) -> Starlette`
  - `rig-serve --repos <dir> --host H --port P`
  - `a2a_rig.server.serve(backend="playback", repos=<dir>, ...)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rig.py`:

```python
"""The multi-repo rig: one process, N repos, one index.

The index is the contract. A consumer that reads a list of card URLs cannot
tell N mounted paths from N standalone ports, which is what lets the rig
change topology without breaking anything built on it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from a2a.client import create_client
from a2a.client.client import ClientConfig

from a2a_playback.repo import load_repos
from a2a_playback.serve import index_document
from a2a_rig.events import send
from a2a_rig.server import serve

REPOS = Path(__file__).parent / "repos"

pytestmark = pytest.mark.backend("playback")


@pytest.fixture(scope="session")
def rig_url():
    with serve(backend="playback", repos=REPOS) as url:
        yield url


@pytest_asyncio.fixture
async def index(rig_url, http_client):
    response = await http_client.get(rig_url)
    return response.json()


def test_the_index_lists_every_repo_in_the_directory():
    repos = load_repos(REPOS)
    document = index_document(repos, "http://127.0.0.1:9200/")

    assert [entry["name"] for entry in document["repos"]] == [
        r.repo_id for r in repos
    ]


def test_card_urls_are_absolute():
    """So the same document describes N ports as easily as N paths."""
    document = index_document(load_repos(REPOS), "http://127.0.0.1:9200/")

    for entry in document["repos"]:
        assert entry["card_url"].startswith("http://127.0.0.1:9200/repos/")
        assert entry["card_url"].endswith("/.well-known/agent-card.json")


async def test_the_served_index_names_the_test_repos(index):
    assert {"vocabulary", "strict"} <= {e["name"] for e in index["repos"]}


async def test_every_advertised_card_is_reachable(index, http_client):
    """The mounted topology's load-bearing assumption, over the wire."""
    for entry in index["repos"]:
        card = (await http_client.get(entry["card_url"])).json()
        assert card["name"] == entry["name"]


async def test_the_rig_itself_is_not_an_agent(rig_url, http_client):
    """No card at the root: the rig is a directory of agents, not one."""
    response = await http_client.get(
        f"{rig_url.rstrip('/')}/.well-known/agent-card.json"
    )

    assert response.status_code == 404


async def test_a_mounted_repo_answers_a_real_turn(rig_url, http_client):
    """Proves a card carrying an absolute mounted url round-trips: the client
    reads it and posts JSON-RPC back to the mounted path, not the host root."""
    base = f"{rig_url.rstrip('/')}/repos/vocabulary/"
    client = await create_client(
        base, ClientConfig(streaming=True, httpx_client=http_client)
    )

    capture = await send(client, "hit the ceiling")

    assert capture.completion_metadata.get("stop_reason") == "max_tokens"


async def test_two_repos_serve_different_content(rig_url, http_client):
    """The actual claim of the milestone. Everything else is plumbing."""
    vocabulary = await create_client(
        f"{rig_url.rstrip('/')}/repos/vocabulary/",
        ClientConfig(streaming=True, httpx_client=http_client),
    )
    strict = await create_client(
        f"{rig_url.rstrip('/')}/repos/strict/",
        ClientConfig(streaming=True, httpx_client=http_client),
    )

    answered = await send(strict, "the known question")
    unmatched = await send(vocabulary, "the known question")

    assert "A scripted answer." in answered.artifact_text()
    assert unmatched.final_state == "failed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rig.py -v`
Expected: FAIL with `ImportError: cannot import name 'index_document' from 'a2a_playback.serve'`

- [ ] **Step 3: Build the index and the parent app**

Add to `src/a2a_playback/serve.py`, above `main`:

```python
def index_document(repos: list[Repo], base_url: str) -> dict:
    """The registry: what repos exist and where their cards are.

    `card_url` is absolute on purpose. It is what lets the same document
    describe N repos mounted on one port or N repos on their own ports, so a
    consumer built against the index is not welded to one topology.

    `name` is the directory name — the repo id, and the same string in the URL.
    `description` is quoted from the card a client will actually fetch rather
    than kept as a second copy.
    """
    base = base_url.rstrip("/")
    return {
        "repos": [
            {
                "name": repo.repo_id,
                "description": repo.card_description or "",
                "card_url": (
                    f"{base}/repos/{repo.repo_id}/.well-known/agent-card.json"
                ),
            }
            for repo in repos
        ]
    }


def build_rig_app(repos: list[Repo], *, base_url: str):
    """One process, N repos: an index at `/` and a mounted a2acode app each.

    Deliberately no agent card at the root — the rig is a directory of agents,
    not an agent. `/.well-known/agent-card.json` at the root 404s, which is
    the honest answer.
    """
    base = base_url.rstrip("/")
    document = index_document(repos, base)

    async def index(_request):
        return JSONResponse(document)

    children = [
        build_repo_app(repo, url=f"{base}/repos/{repo.repo_id}/") for repo in repos
    ]
    routes = [Route("/", index)]
    routes += [
        Mount(f"/repos/{repo.repo_id}", app=child)
        for repo, child in zip(repos, children)
    ]
    # Mounted apps do not get their lifespans run by the parent for free, and
    # a2acode initializes its stores in one. See mounting.py.
    return Starlette(routes=routes, lifespan=mount_lifespans(children))
```

Add the imports `serve.py` now needs:

```python
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .mounting import mount_lifespans
from .repo import Repo, RepoError, load_repo, load_repos
```

- [ ] **Step 4: Add `--repos` to the CLI**

In `main`, add the argument and branch:

```python
    parser.add_argument("--repo", help="Path to one repo directory.")
    parser.add_argument("--repos", help="Path to a directory of repo directories.")
```

and replace the body after `args = parser.parse_args(argv)`:

```python
    if bool(args.repo) == bool(args.repos):
        parser.error("pass exactly one of --repo or --repos")

    url = f"http://{args.host}:{args.port}/"
    try:
        if args.repo:
            app = build_repo_app(load_repo(args.repo), url=url)
            what = f"repo={args.repo}"
        else:
            app = build_rig_app(load_repos(args.repos), base_url=url)
            what = f"repos={args.repos}"
    except (RepoError, ScenarioError) as exc:
        # Config problems are user errors, not crashes: say what is wrong and
        # exit, rather than burying it in a traceback.
        print(f"rig-serve: {exc}", file=sys.stderr)
        return 2

    print(f"rig-serve: {what} card={url}", flush=True)
```

- [ ] **Step 5: Teach the harness launcher about `--repos`**

In `src/a2a_rig/server.py`, replace `_playback_command` and add the `repos` parameter to `serve()`:

```python
def _playback_command(
    port: int, repo: str | Path | None, repos: str | Path | None
) -> list[str]:
    """`playback` is ours, so it is served by rig-serve, not the a2acode CLI.

    Uses the running interpreter rather than a console script so the harness
    works from a bare checkout without an install step.
    """
    selector = ["--repos", str(repos)] if repos else ["--repo", str(repo or DEFAULT_REPO)]
    return [
        sys.executable,
        "-m",
        "a2a_playback.serve",
        *selector,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
```

`serve()` gains `repos: str | Path | None = None` alongside `repo`, and calls `_playback_command(port, repo, repos)`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_rig.py -v`
Expected: 7 passed

If `test_every_advertised_card_is_reachable` or `test_a_mounted_repo_answers_a_real_turn` fails, the mounted topology's assumption is broken. **Stop and report** rather than working around it — the spec's fallback is per-repo processes as the default, with the index contract unchanged.

- [ ] **Step 7: Run the full suite against both backends**

Run:
```bash
uv run pytest --backend playback
uv run pytest --backend echo
```
Expected: all pass, 4 pre-existing xfails plus the 1 temporary xfail.

- [ ] **Step 8: Commit**

```bash
git add src/a2a_playback/serve.py src/a2a_rig/server.py tests/test_rig.py
git commit -m "Serve N repos from one process behind a JSON index

An index at / listing absolute card URLs, and one mounted a2acode app per
repo. The index is the contract: a consumer reading card URLs cannot tell
mounted paths from separate ports, which is what keeps the topology
swappable.

No card at the root, because the rig is a directory of agents rather than
one."
```

---

### repos-ship

The three fake repos the rig ships with, moved and written so a frontend has something plausible to develop against.

**Files:**
- Create: `repos/billing-api/repo.yaml`, `repos/billing-api/scenarios/refactor.yaml`
- Create: `repos/checkout-web/repo.yaml`, `repos/checkout-web/scenarios/upgrade.yaml`
- Create: `repos/infra-terraform/repo.yaml`, `repos/infra-terraform/scenarios/plan-and-apply.yaml`
- Delete: `scenarios/billing-api.yaml`
- Modify: `tests/test_playback.py` (remove the temporary xfail)

**Interfaces:**
- Consumes: the repo format from `repo-format`.
- Produces: `repos/` with 3 loadable repos, satisfying Phase 6's "3+ fake repos".

- [ ] **Step 1: Move billing-api into the new shape**

Create `repos/billing-api/repo.yaml`:

```yaml
# A fake billing-api repo that "does" a small feature with a permission gate.
#
# Hand-written from the shape of the real Claude run captured in
# a2a-experiments docs/captures/phase2-claude-run.jsonl. M3 replaces
# hand-written repos like this one with recordings.
card:
  name: billing-api
  description: "Fake billing-api repo (playback)"
defaults:
  delay_ms: 0 # instant unless PLAYBACK_SPEED asks otherwise
```

Move the **`plays:` block only** from `scenarios/billing-api.yaml` into
`repos/billing-api/scenarios/refactor.yaml`, dropping `name:`, `card:`, `defaults:`, and the
file header comment (now in `repo.yaml`). Keep every play and per-play comment byte-for-byte.
Then `git rm scenarios/billing-api.yaml` and remove the now-empty `scenarios/` directory.

- [ ] **Step 2: Write the second repo**

Create `repos/checkout-web/repo.yaml`:

```yaml
# A frontend repo. Exists so a consumer has two repos that answer the same
# prompt differently — the thing a multi-repo rig is for.
card:
  name: checkout-web
  description: "Fake checkout-web repo (playback)"
defaults:
  delay_ms: 0
```

Create `repos/checkout-web/scenarios/upgrade.yaml`:

```yaml
plays:
  # A dependency bump the agent wants to verify by building.
  - match: { contains: "upgrade" }
    events:
      - plan:
          steps:
            - { content: "Bump the dependency", status: in_progress }
            - { content: "Run the build", status: pending }
      - tool_use: { name: Read, input: { file_path: "package.json" }, id: t1 }
      - tool_result: { id: t1, name: Read }
      - text: "Bumping the router to 6.22.0.\n"
      - file_change:
          path: "package.json"
          diff: |
            --- a/package.json
            +++ b/package.json
            @@ -12,7 +12,7 @@
            -    "react-router-dom": "6.21.0",
            +    "react-router-dom": "6.22.0",
      - plan:
          steps:
            - { content: "Bump the dependency", status: completed }
            - { content: "Run the build", status: in_progress }
      - permission:
          tool: Bash
          input: { command: "npm run build" }
          description: "$ npm run build"
          on_allow:
            - tool_use: { name: Bash, input: { command: "npm run build" }, id: t2 }
            - tool_result: { id: t2, name: Bash, output: "built in 4.1s" }
            - text: "Upgraded and the build is clean."
            - result: { cost_usd: 0.0211, num_turns: 4, stop_reason: end_turn }
          on_deny:
            - text: "Bumped the version but did not build, so it is unverified."
            - result: { cost_usd: 0.0119, num_turns: 3, stop_reason: end_turn }

  # A read-only question, so a consumer has a turn with no gate in it.
  - match: {}
    events:
      - thought: "The caller wants orientation, not changes."
      - tool_use: { name: Read, input: { file_path: "src/App.tsx" }, id: t1 }
      - tool_result: { id: t1, name: Read }
      - text: "This is a Vite + React checkout flow; routing lives in src/routes."
      - result: { cost_usd: 0.003, num_turns: 1, stop_reason: end_turn }
```

- [ ] **Step 3: Write the third repo**

Create `repos/infra-terraform/repo.yaml`:

```yaml
# An infrastructure repo, where the interesting path is a change nobody should
# apply without looking. Its default play fails, so a consumer has a repo that
# models "this went wrong" without needing a special prompt.
card:
  name: infra-terraform
  description: "Fake infra-terraform repo (playback)"
defaults:
  delay_ms: 0
```

Create `repos/infra-terraform/scenarios/plan-and-apply.yaml`:

```yaml
plays:
  # The gate that matters: a plan a human should read before applying.
  - match: { contains: "apply" }
    events:
      - tool_use: { name: Bash, input: { command: "terraform plan" }, id: t1 }
      - tool_result:
          id: t1
          name: Bash
          output: "Plan: 2 to add, 1 to change, 1 to destroy."
      - text: "One resource gets destroyed. Reading the plan before applying.\n"
      - permission:
          tool: Bash
          input: { command: "terraform apply -auto-approve" }
          description: "$ terraform apply -auto-approve"
          timeout_ms: 300
          on_allow:
            - tool_use:
                name: Bash
                input: { command: "terraform apply -auto-approve" }
                id: t2
            - tool_result: { id: t2, name: Bash, output: "Apply complete!" }
            - text: "Applied."
            - result: { num_turns: 3, stop_reason: end_turn }
          on_deny:
            - text: "Left the infrastructure alone."
            - result: { num_turns: 3, stop_reason: end_turn }
          on_timeout:
            - notice: "No answer in time; treating the apply as declined."
            - text: "Nobody signed off, so nothing was applied."
            - result: { num_turns: 3, stop_reason: permission_timeout }

  # A run that dies partway, as the default: a consumer gets a failing repo
  # without having to know a magic phrase.
  - match: {}
    events:
      - tool_use: { name: Bash, input: { command: "terraform init" }, id: t1 }
      - tool_result:
          id: t1
          name: Bash
          failed: true
          output: "Error: Failed to query available provider packages"
      - error: "terraform init could not reach the provider registry"
```

- [ ] **Step 4: Remove the temporary xfail**

In `tests/test_playback.py`, delete the `@pytest.mark.xfail(reason="shipped repos land in repos-ship", strict=True)` marker from `test_shipped_repos_load`.

- [ ] **Step 5: Verify the repos load and serve**

Run:
```bash
uv run python -c "
from a2a_playback.repo import load_repos
for r in load_repos('repos'):
    print(r.repo_id, '->', len(r.plays), 'plays')
"
```
Expected: three lines — `billing-api`, `checkout-web`, `infra-terraform` — each with at least 1 play.

- [ ] **Step 6: Run the full suite against both backends**

Run:
```bash
uv run pytest --backend playback
uv run pytest --backend echo
```
Expected: all pass, 4 pre-existing xfails, no temporary xfail left.

- [ ] **Step 7: Commit**

```bash
git add -A repos/ scenarios/ tests/test_playback.py
git commit -m "Ship three fake repos

billing-api moves into the new shape; checkout-web and infra-terraform are
new. Three rather than two, because two is not enough to notice a registry
that accidentally serves the same repo twice.

infra-terraform's default play fails, so a consumer gets a repo that models
'this went wrong' without needing to know a magic phrase."
```

---

### repos-fixture

The pytest fixture that hands agent tests a directory of repos, and the timing check that is Phase 6's exit criterion.

**Files:**
- Modify: `tests/conftest.py`
- Test: `tests/test_rig.py` (extend)

**Interfaces:**
- Consumes: `serve(repos=...)` from `rig-app`; the shipped repos from `repos-ship`.
- Produces: a session-scoped `repos` fixture with `.names -> list[str]`, `.index -> dict`, and `async .client(name: str)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_rig.py`:

```python
# --- The fixture agent tests actually use --------------------------------


async def test_the_fixture_exposes_every_shipped_repo(repos):
    assert set(repos.names) >= {"billing-api", "checkout-web", "infra-terraform"}


async def test_the_fixture_hands_out_a_client_per_repo(repos):
    """What an agent test does: pick a repo by name, talk to it."""
    client = await repos.client("billing-api")

    capture = await send(client, "explain the tax module")

    assert "VAT" in capture.artifact_text()


async def test_repos_answer_independently(repos):
    """Two repos, two different answers, one process."""
    checkout = await repos.client("checkout-web")
    infra = await repos.client("infra-terraform")

    upgraded = await send(checkout, "upgrade the router")
    broken = await send(infra, "get started")

    assert upgraded.final_state == "input_required"
    assert broken.final_state == "failed"


async def test_three_repos_are_driveable_inside_the_phase_budget(repos):
    """Phase 6's exit criterion: 3+ fake repos, under 5s total. Booting once
    for all N is what makes that hold as repos accumulate."""
    import time

    start = time.monotonic()
    for name in ("billing-api", "checkout-web", "infra-terraform"):
        await send(await repos.client(name), "explain this repo")
    assert time.monotonic() - start < 5.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rig.py -v -k "fixture or budget or independently"`
Expected: FAIL with `fixture 'repos' not found`

- [ ] **Step 3: Write the fixture**

Add to `tests/conftest.py`:

```python
REPOS_DIR = Path(__file__).resolve().parents[1] / "repos"


class Rig:
    """A running rig, and the repos it serves.

    Hands out one A2A client per repo, resolved through the index rather than
    by building URLs — so a test is written against the registry contract, the
    same way a real consumer would be, and keeps working if the topology
    changes underneath it.
    """

    def __init__(self, url: str, index: dict, http_client):
        self.url = url
        self.index = index
        self._http = http_client

    @property
    def names(self) -> list[str]:
        return [entry["name"] for entry in self.index["repos"]]

    async def client(self, name: str):
        for entry in self.index["repos"]:
            if entry["name"] == name:
                base = entry["card_url"].removesuffix(".well-known/agent-card.json")
                return await create_client(
                    base, ClientConfig(streaming=True, httpx_client=self._http)
                )
        raise LookupError(f"no repo named {name!r}; have {self.names}")


@pytest.fixture(scope="session")
def _rig_url():
    """One rig process for the whole session.

    Booting costs ~0.5s and tasks are isolated by id, so one process serving
    every repo is what keeps a growing repo directory cheap to test against.
    """
    with serve(backend="playback", repos=REPOS_DIR) as url:
        yield url


@pytest_asyncio.fixture
async def repos(_rig_url, http_client) -> Rig:
    index = (await http_client.get(_rig_url)).json()
    return Rig(_rig_url, index, http_client)
```

Add the imports `conftest.py` needs: `from pathlib import Path`, and `create_client` / `ClientConfig` if not already imported.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_rig.py -v`
Expected: 11 passed

- [ ] **Step 5: Run the full suite against both backends, with timing**

Run:
```bash
uv run pytest --backend playback
uv run pytest --backend echo
```
Expected: all pass, 4 xfails, each run under 5s.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_rig.py
git commit -m "Add the repos fixture: a directory of repos for agent tests

Resolves clients through the index rather than by building URLs, so tests are
written against the registry contract the same way a real consumer is, and
survive a topology change.

One rig process per session: booting once for all N is what keeps the
under-5s budget holding as repos accumulate."
```

---

### docs-graduate

Moves the architectural decisions out of the spec and into the plan of record, and records what happened.

**Files:**
- Modify: `docs/DESIGN-v3.md`
- Modify: `docs/PLAN.md`
- Modify: `docs/DEVLOG.md`
- Modify: `a2a-rig/README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Correct DESIGN-v3's repo/scenario conflation**

In `docs/DESIGN-v3.md` §2, replace the sentence "A **fake repo is just a scenario file**" and the paragraph following it with:

```markdown
A **fake repo is a directory**: `repo.yaml` declares who the agent is, and
`scenarios/*.yaml` hold the scripts it can play. A repo *has* scenarios; it is
not one. The directory name is the repo id, so identity has one source. A
multi-repo frontend is therefore cheap to develop: a directory of repo
directories, served either from one process with each repo mounted at
`/repos/<name>/` behind a JSON index, or one process per repo (DESIGN-v2 §9).
No git checkouts, no workspaces, no claude installs anywhere.

Consumers read the **index**, not the filesystem and not root-scoped
well-known discovery: `GET /` returns `{"repos": [{"name", "description",
"card_url"}]}` with absolute card URLs. That is the seam that keeps the two
topologies interchangeable — a consumer built on the index cannot tell them
apart.
```

Update the §2 diagram so its command reads `--repos repos/` and its tree shows
`repos/billing-api/{repo.yaml, scenarios/}` rather than `scenarios/*.yaml`.

In §4, retitle "Scenario format" to "Repo and scenario format", and add above the existing
YAML example:

```markdown
Identity and pacing live in `repo.yaml`; a scenario file holds `plays:` and
nothing else. A repo's scenario files are read in filename order and their
plays concatenated, then matched first-match-wins — which is what makes M3
additive, since a recording is just a new file in `scenarios/`.
```

Then split the existing example into the two files it is now.

- [ ] **Step 2: Check off Phase 6 in PLAN.md**

Replace the Phase 6 body in `docs/PLAN.md`:

```markdown
- [x] Scenario directory → N fake repos: one process with each repo mounted at
      `/repos/<name>/` behind a JSON index at `/`, each with its own card
      (DESIGN-v2 §9 pattern 2). One process per repo is retained as `--repo` —
      it is what proves the index is topology-independent rather than a shape
      only the rig can serve.
- [x] Pytest fixtures exposing "a directory of repos" to your agents' tests.
      The `repos` fixture resolves clients through the index, so tests are
      written against the same contract a real consumer uses.
- [ ] **Start building the frontend and agents against this** — the rig is now their
      standing dev environment.

Done along the way: **`repo` and `scenario` were split.** One YAML had been
carrying both an agent's identity and its script, a conflation inherited from
DESIGN-v3 itself (§2 said a fake repo *is* a scenario file; §4 said a scenario
is a list of plays). M3 would have broken it — recording produces several
scripts per repo, and identity inside the script means every recording
restates it. DESIGN-v3 corrected.

**Exit:** ✅ for the rig. 3+ fake repos, driven through one process, well
inside the 5s budget; the frontend dev loop is offline. The remaining bullet
is the consumer, which is its own project.
```

- [ ] **Step 3: Append a DEVLOG entry**

Append a `## 2026-08-07 — Phase 6 (M2): a directory of repos` section to `docs/DEVLOG.md`
covering, in prose consistent with the existing entries:

- the repo/scenario split, why the question came up, and that the conflation was DESIGN-v3's rather than M2's
- that M3 was the forcing function: N recordings per repo, so identity cannot live in the script
- the lifespan finding — Starlette does not run mounted sub-apps' lifespans, and a2acode initializes its stores in one, so N mounted repos means N lifespans run by hand
- the index as the topology seam, and why the single-repo path was kept rather than deleted
- final counts from the last verification run, and confirmation the backend-agnostic suite needed no edits

- [ ] **Step 4: Update the rig README**

In `a2a-rig/README.md`, replace every `--scenario`/`scenarios/` reference with the repo layout,
and add a short "Running the rig" section showing both:

```bash
# every repo, one process, index at /
uv run rig-serve --repos repos/ --port 9200

# one repo at a host root, the way a real deployment would run it
uv run rig-serve --repo repos/billing-api --port 9201
```

- [ ] **Step 5: Update CLAUDE.md's description of the rig**

In the "Target architecture (DESIGN-v3)" section of `CLAUDE.md`, update the sentence describing
scenarios so it says a repo is a directory (`repo.yaml` + `scenarios/*.yaml`), that the
directory name is the id, and that consumers read the index at `/`.

- [ ] **Step 6: Verify the docs match the code**

Run:
```bash
grep -rn "\-\-scenario\|scenarios/billing-api" docs/ CLAUDE.md a2a-rig/README.md \
  --exclude-dir=superpowers
```
Expected: no hits except in DEVLOG entries describing past work. `docs/superpowers/` is
excluded because the M2 spec and this plan are historical records of how the change was
decided and must not be rewritten to match the outcome.

- [ ] **Step 7: Final verification**

Run:
```bash
uv run pytest --backend playback
uv run pytest --backend echo
```
Expected: all pass, 4 xfailed, under 5s each. Record the exact counts in the DEVLOG entry.

- [ ] **Step 8: Commit**

```bash
git add docs/ CLAUDE.md a2a-rig/README.md
git commit -m "Graduate M2's decisions into DESIGN-v3

The repo/scenario split and the index contract move from the M2 spec into the
plan of record, and DESIGN-v3's own conflation gets corrected: §2 had said a
fake repo *is* a scenario file while §4 defined a scenario as a list of plays."
```

---

## Self-Review

**Spec coverage.** Layout and format split → `repo-format`, `backend-takes-repo`, `repos-ship`. Registry contract → `rig-app`. Both topologies → `backend-takes-repo` (`--repo`) and `rig-app` (`--repos`). Risk retirement → `mount-lifespan` and `rig-app` step 6. Fixtures → `repos-fixture`. Error handling → `repo-format` tests plus the CLI's `RepoError`/`ScenarioError` branch. Testing list → covered across `test_repo.py`, `test_rig.py`. Migration → `backend-takes-repo` and `repos-ship`. DESIGN-v3 correction → `docs-graduate`.

**One spec deviation, deliberate.** The spec ordered risk-retirement first and framed it as Starlette routing. Investigation found the sharper risk is lifespan propagation, which is testable in isolation, so `mount-lifespan` retires that up front and `rig-app` step 6 retires the routing and card round-trip against real a2acode. Under the fallback, the format work in `repo-format` and `backend-takes-repo` is still correct and still wanted, so nothing is gambled by ordering it before the wire proof.

**Interface consistency.** `Repo.repo_id` (not `.name`) everywhere. `parse_scenario`/`load_scenario` replace `parse`/`load` at every call site. `PlaybackBackend.repo` replaces `.scenario` in both `drive` and `_delay`. `build_repo_app` is defined in `backend-takes-repo` and consumed in `rig-app`. `mount_lifespans` is defined in `mount-lifespan` and consumed in `rig-app`. `serve(repo=, repos=)` is introduced in `backend-takes-repo` and extended in `rig-app`, with both call sites updated.
