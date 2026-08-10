# direct-sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The walking skeleton of the cockpit — start a mission in a browser, chat with a
fake repo agent in free text over genuine A2A through a contextId-routed proxy, and answer
an approval, zero inference.

**Architecture:** A new top-level `a2a-orchestrator/` project (incubating like `a2a-rig/`,
per the spec). A Starlette service owns two planes: management REST (`/api/*`: missions,
catalog, open-chat) backed by SQLite, and a pass-through A2A proxy
(`/a2a/chats/{context_id}/*`) that relays bytes unmodified to the upstream repo agent —
except agent cards, whose URLs it rewrites to itself. The browser runs a real a2a-js
client against the proxy; a Vite + React UI renders missions, a chat pane, and approval
cards. Tests drive real subprocesses: a `playback` rig serving `a2a-rig/repos/`, and the
service in front of it.

**Tech Stack:** Python ≥3.13, uv, hatchling, Starlette, uvicorn, httpx, PyYAML, sqlite3
(stdlib); pytest + pytest-asyncio + `a2a-sdk` 1.1.2 (tests); Vite + React + TypeScript +
`@a2a-js/sdk` 1.0.1 (frontend). `a2a-rig` as an editable path dependency (dev group) for
its server harness and event helpers.

**Spec:** `docs/superpowers/specs/2026-08-09-a2a-orchestrator-design.md` — the
`direct-sessions` milestone bullet plus the "Appendix: verified facts for implementers."
Scope is this milestone only: no orchestrator agent, no recording, no replay, no worktrees,
no approval-inbox REST, no Playwright. Later milestones plan themselves when their inputs
exist.

## Global Constraints

- **Vocabulary:** approval (not gate), recording (not trace), worktree (not checkout),
  mission, catalog/Repository. Applies to code, comments, and UI copy.
- **Python:** `requires-python = ">=3.13"`, uv-managed, hatchling build, `asyncio_mode =
  "auto"`, `addopts = "-q"` — mirroring `a2a-rig/pyproject.toml`. Every module starts with
  a docstring and `from __future__ import annotations`.
- **Pins:** `a2a-sdk==1.1.2` (dev; matches a2acode v0.6.2's SDK), `@a2a-js/sdk@^1.0.1`
  (the browser-proven version).
- **Ports:** rig 9200, orchestrator service 9300, Vite dev server 5173 — defaults for
  humans; tests always use free ports via `a2a_rig.server.free_port`.
- **The proxy relays unmodified** with exactly one exception: agent-card responses are
  buffered and their upstream URLs rewritten (both `localhost` and `127.0.0.1` spellings)
  to the proxy's own base. Everything else streams raw.
- **The chat's contextId is service-minted** at chat-open and rides the proxied base path
  (`/a2a/chats/{context_id}/`). Verified 2026-08-10 against the a2a-sdk 1.1.2 installed in
  a2acode's venv (`a2a/server/agent_execution/context.py`,
  `_check_or_generate_context_id`): a client-supplied `message.context_id` is adopted, not
  replaced — so the upstream converges on the service's id. This settles the spec's
  "who mints contextIds" open question; a proxy test pins it on the wire.
- **Cold resubscribe routing is dissolved, not solved:** because the contextId is in the
  path, routing *any* call — including `tasks/resubscribe` by taskId after a reload — is a
  store lookup, never an inference from observed traffic. This settles the handoff's other
  open question.
- **Git:** code changes branch (repo convention). Branch: `direct-sessions`, worktree via
  `wt switch --create direct-sessions --yes --no-cd --format=json` per user preference (falls
  back to plain `git switch -c` if `wt` is unavailable). Commit messages are plain
  imperative sentences (house style), each ending with the session's `Co-Authored-By` and
  `Claude-Session` trailers. All `uv`/`npm`/`pytest` commands below run from
  `a2a-orchestrator/` inside the worktree unless stated otherwise.
- **Test isolation:** the service and rig fixtures are session-scoped (booting costs
  ~0.5s each); test bodies must not assume an empty database — assert on what they
  created, never on totals.

## File structure

```
a2a-orchestrator/
  pyproject.toml                  # scaffold-and-rig-harness
  .gitignore                      # scaffold-and-rig-harness
  catalog.yaml                    # catalog-provider
  README.md                       # static-serve-readme-demo
  src/a2a_orchestrator/
    __init__.py                   # scaffold-and-rig-harness
    store.py                      # mission-store: SQLite missions + chats
    catalog.py                    # catalog-provider: index provider
    api.py                        # missions-api: management REST handlers
    app.py                        # missions-api: Starlette assembly (+proxy route in a2a-proxy)
    serve.py                      # missions-api: orch-serve CLI
    proxy.py                      # a2a-proxy: relay + card rewrite
  tests/
    conftest.py                   # scaffold (rig, http) + missions-api (service, helpers)
    test_rig_harness.py           # scaffold-and-rig-harness
    test_store.py                 # mission-store
    test_catalog.py               # catalog-provider
    test_missions_api.py          # missions-api
    test_proxy.py                 # a2a-proxy
  frontend/                       # frontend-shell (Vite scaffold) + chat-pane-and-approvals
    vite.config.ts
    src/api.ts                    # REST wrappers
    src/a2a.ts                    # a2a-js client + event distillation
    src/App.tsx                   # mission list + mission view
    src/ChatPane.tsx              # conversation + approval flow
    src/ApprovalCard.tsx
    src/index.css
  var/                            # runtime state, gitignored
```

Verified rig facts the tests below lean on (re-checked 2026-08-10 against
`a2a-rig/repos/`):

- Index: `GET /` → `{"repos": [{"name", "description", "card_url"}]}`; repos mount at
  `/repos/<name>/`.
- `billing-api`: any turn-1 message → `90-greeting` ("Looked over the billing-api repo.
  Ready when you are."); later free text → `99-default` ("Still here. Ready when you
  are."). **Both contain "Ready when you are"** — the safe any-turn marker. A message
  containing `"run the tests"` (any turn) parks on a Bash approval (`30-refactor`).
- `checkout-web`: free text without "upgrade" → "This is a Vite + React checkout flow;
  routing lives in src/routes."
- `infra-terraform`: free text without "apply" → default play emits a failed tool result
  and an `error` event — the task **fails**. Free failure-relay coverage.
- Approval wire (`a2a-rig/tests/test_permission.py`): parked state is `input_required`;
  payload rides status-message metadata key `a2acode_permission` as
  `{tool, request_id, input}`; the answer is a plain message on the same
  `task_id` + `context_id`; the stream *ends* when the task parks (no hanging SSE).

---

### scaffold-and-rig-harness

The uv project, the rig fixture, and a smoke test proving the substrate wiring — the same
spawn-and-share pattern `a2a-rig/tests/conftest.py` proved.

**Files:**
- Create: `a2a-orchestrator/pyproject.toml`
- Create: `a2a-orchestrator/.gitignore`
- Create: `a2a-orchestrator/src/a2a_orchestrator/__init__.py`
- Create: `a2a-orchestrator/tests/conftest.py`
- Test: `a2a-orchestrator/tests/test_rig_harness.py`

**Interfaces:**
- Consumes: `a2a_rig.server.serve(backend, repos=...)` context manager yielding a base
  URL; `a2a-rig/repos/` (billing-api, checkout-web, infra-terraform).
- Produces: fixtures `rig_url` (session, str ending `/`) and `http`
  (function-scoped `httpx.AsyncClient`) that every later test module uses.

- [ ] **create-worktree-and-branch**

From the repo root: `wt switch --create direct-sessions --yes --no-cd --format=json`,
parse `.path` from the JSON, and run everything below from `<path>/a2a-orchestrator/`.
(`wt` commands routinely need the command sandbox disabled — expected, not a failure.
Fallback without `wt`: `git switch -c direct-sessions` in the main checkout.)

- [ ] **write-pyproject**

`a2a-orchestrator/pyproject.toml`:

```toml
[project]
name = "a2a-orchestrator"
version = "0.1.0"
description = "Cockpit for coordinating agent work across repos over A2A"
requires-python = ">=3.13"
dependencies = [
    "httpx>=0.28",
    "pyyaml>=6.0",
    "starlette>=0.47",
    "uvicorn>=0.30",
]

[project.scripts]
orch-serve = "a2a_orchestrator.serve:main"

[dependency-groups]
dev = [
    "a2a-rig",
    "a2a-sdk==1.1.2",
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
]

[tool.uv.sources]
a2a-rig = { path = "../a2a-rig", editable = true }

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-q"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/a2a_orchestrator"]
```

The `a2a-rig` path dependency brings `a2acode` (git pin v0.6.2) and `a2a-sdk` 1.1.2
transitively; `a2a-sdk` is listed anyway because tests import `a2a.client` directly.

- [ ] **write-gitignore-and-package**

`a2a-orchestrator/.gitignore`:

```gitignore
var/
__pycache__/
.venv/
.pytest_cache/
```

`a2a-orchestrator/src/a2a_orchestrator/__init__.py`:

```python
"""The cockpit's service half: management REST plus a pass-through A2A proxy."""
```

- [ ] **sync-the-venv**

Run: `uv sync --dev`
Expected: resolves clean, installs `a2a-rig` editable and `a2acode` from its git pin.
Commit `uv.lock` when it appears.

- [ ] **write-conftest-rig-half**

`a2a-orchestrator/tests/conftest.py`:

```python
"""Fixtures: a playback rig serving the fake repos, driven over the wire.

The pattern is a2a-rig's own harness (spawn a real subprocess, poll ready,
share it session-wide) — booting costs ~0.5s and tasks are isolated by id,
so sharing is safe. The service fixture joins this file in the missions-api
task; this half is just the substrate.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from a2a_rig.server import serve as rig_serve

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RIG_REPOS = PROJECT_ROOT.parent / "a2a-rig" / "repos"


@pytest.fixture(scope="session")
def rig_url() -> str:
    """One playback rig serving a2a-rig's repo directory, shared session-wide."""
    with rig_serve(backend="playback", repos=RIG_REPOS) as url:
        yield url


@pytest_asyncio.fixture
async def http():
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        yield client
```

- [ ] **write-smoke-test**

`a2a-orchestrator/tests/test_rig_harness.py`:

```python
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
```

- [ ] **run-smoke**

Run: `uv run pytest tests/test_rig_harness.py -v`
Expected: 2 passed (first run pays the ~0.5s rig boot).

- [ ] **commit-scaffold**

```bash
git add a2a-orchestrator
git commit -m "Scaffold a2a-orchestrator with the rig as its test substrate"
```

---

### mission-store

SQLite persistence: missions, and the chats bound inside them. Pure module, no server —
TDD directly against the class.

**Files:**
- Create: `a2a-orchestrator/src/a2a_orchestrator/store.py`
- Test: `a2a-orchestrator/tests/test_store.py`

**Interfaces:**
- Produces (used by `api.py` and `proxy.py`):
  - `Store(path: str | Path)` — opens/creates the database, applies schema.
  - `Store.create_mission(title: str = "Untitled mission") -> Mission`
  - `Store.list_missions() -> list[Mission]` (creation order)
  - `Store.get_mission(mission_id: str) -> Mission | None`
  - `Store.rename_mission(mission_id: str, title: str) -> Mission | None`
  - `Store.create_chat(mission_id: str, agent: str, upstream_url: str) -> Chat` — mints
    the contextId (`uuid4().hex`).
  - `Store.chats_for_mission(mission_id: str) -> list[Chat]`
  - `Store.chat_for_context(context_id: str) -> Chat | None`
  - `Mission` dataclass: `id, title, created_at` (all `str`).
  - `Chat` dataclass: `context_id, mission_id, agent, upstream_url, created_at` (all
    `str`), plus property `a2a_url -> str` = `f"/a2a/chats/{self.context_id}/"`.

- [ ] **write-failing-store-tests**

`a2a-orchestrator/tests/test_store.py`:

```python
"""The SQLite floor: missions and chats, nothing the milestone doesn't need."""

from __future__ import annotations

from a2a_orchestrator.store import Store

BILLING = "http://127.0.0.1:9200/repos/billing-api/"


def test_create_and_list_missions(tmp_path):
    store = Store(tmp_path / "test.db")
    created = store.create_mission(title="Ship health checks")

    missions = store.list_missions()

    assert [m.id for m in missions] == [created.id]
    assert missions[0].title == "Ship health checks"
    assert missions[0].created_at


def test_mission_title_defaults(tmp_path):
    store = Store(tmp_path / "test.db")
    assert store.create_mission().title == "Untitled mission"


def test_rename_mission(tmp_path):
    store = Store(tmp_path / "test.db")
    mission = store.create_mission()

    renamed = store.rename_mission(mission.id, "Better title")

    assert renamed.title == "Better title"
    assert store.get_mission(mission.id).title == "Better title"


def test_rename_unknown_mission_returns_none(tmp_path):
    store = Store(tmp_path / "test.db")
    assert store.rename_mission("nope", "x") is None


def test_create_chat_mints_unique_context_ids(tmp_path):
    store = Store(tmp_path / "test.db")
    mission = store.create_mission()

    first = store.create_chat(mission.id, "billing-api", BILLING)
    second = store.create_chat(mission.id, "billing-api", BILLING)

    assert first.context_id != second.context_id
    assert first.a2a_url == f"/a2a/chats/{first.context_id}/"


def test_chat_lookup_by_context(tmp_path):
    store = Store(tmp_path / "test.db")
    mission = store.create_mission()
    chat = store.create_chat(mission.id, "billing-api", BILLING)

    found = store.chat_for_context(chat.context_id)

    assert found.upstream_url == BILLING
    assert found.agent == "billing-api"
    assert store.chat_for_context("missing") is None


def test_chats_for_mission_lists_in_order(tmp_path):
    store = Store(tmp_path / "test.db")
    mission = store.create_mission()
    other = store.create_mission()
    first = store.create_chat(mission.id, "billing-api", BILLING)
    second = store.create_chat(mission.id, "checkout-web", BILLING)
    store.create_chat(other.id, "billing-api", BILLING)

    chats = store.chats_for_mission(mission.id)

    assert [c.context_id for c in chats] == [first.context_id, second.context_id]


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "test.db"
    mission = Store(path).create_mission(title="Persist me")

    reopened = Store(path)

    assert [m.title for m in reopened.list_missions()] == ["Persist me"]
    assert reopened.chats_for_mission(mission.id) == []
```

- [ ] **run-store-tests-fail**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'a2a_orchestrator.store'`.

- [ ] **write-store**

`a2a-orchestrator/src/a2a_orchestrator/store.py`:

```python
"""SQLite persistence: missions and the chats bound inside them.

One connection, one file. The service is a single process on one event loop,
so a shared connection with explicit transactions is enough — no pool, no ORM.
The schema is the floor the spec names: what chat routing and (later) resume
actually need. Session detail grows only when a use case demands it.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    context_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id),
    agent TEXT NOT NULL,
    upstream_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_MISSION_COLS = "id, title, created_at"
_CHAT_COLS = "context_id, mission_id, agent, upstream_url, created_at"


@dataclass
class Mission:
    id: str
    title: str
    created_at: str


@dataclass
class Chat:
    context_id: str
    mission_id: str
    agent: str
    upstream_url: str
    created_at: str

    @property
    def a2a_url(self) -> str:
        return f"/a2a/chats/{self.context_id}/"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def create_mission(self, title: str = "Untitled mission") -> Mission:
        mission = Mission(id=uuid.uuid4().hex, title=title, created_at=_now())
        with self._db:
            self._db.execute(
                "INSERT INTO missions VALUES (?, ?, ?)",
                (mission.id, mission.title, mission.created_at),
            )
        return mission

    def list_missions(self) -> list[Mission]:
        rows = self._db.execute(
            f"SELECT {_MISSION_COLS} FROM missions ORDER BY created_at, id"
        ).fetchall()
        return [Mission(*row) for row in rows]

    def get_mission(self, mission_id: str) -> Mission | None:
        row = self._db.execute(
            f"SELECT {_MISSION_COLS} FROM missions WHERE id = ?", (mission_id,)
        ).fetchone()
        return Mission(*row) if row else None

    def rename_mission(self, mission_id: str, title: str) -> Mission | None:
        with self._db:
            changed = self._db.execute(
                "UPDATE missions SET title = ? WHERE id = ?", (title, mission_id)
            ).rowcount
        return self.get_mission(mission_id) if changed else None

    def create_chat(self, mission_id: str, agent: str, upstream_url: str) -> Chat:
        chat = Chat(
            context_id=uuid.uuid4().hex,
            mission_id=mission_id,
            agent=agent,
            upstream_url=upstream_url,
            created_at=_now(),
        )
        with self._db:
            self._db.execute(
                "INSERT INTO chats VALUES (?, ?, ?, ?, ?)",
                (chat.context_id, chat.mission_id, chat.agent,
                 chat.upstream_url, chat.created_at),
            )
        return chat

    def chats_for_mission(self, mission_id: str) -> list[Chat]:
        rows = self._db.execute(
            f"SELECT {_CHAT_COLS} FROM chats WHERE mission_id = ? "
            "ORDER BY created_at, context_id",
            (mission_id,),
        ).fetchall()
        return [Chat(*row) for row in rows]

    def chat_for_context(self, context_id: str) -> Chat | None:
        row = self._db.execute(
            f"SELECT {_CHAT_COLS} FROM chats WHERE context_id = ?", (context_id,)
        ).fetchone()
        return Chat(*row) if row else None
```

(Rows construct dataclasses positionally, so the explicit column lists in `_MISSION_COLS`
/ `_CHAT_COLS` are load-bearing — they pin column order to field order.)

- [ ] **run-store-tests-pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 8 passed.

- [ ] **commit-store**

```bash
git add a2a-orchestrator/src/a2a_orchestrator/store.py a2a-orchestrator/tests/test_store.py
git commit -m "Add the mission store: SQLite missions and chats"
```

---

### catalog-provider

The index provider: `catalog.yaml` names an index URL; the catalog resolves repo names to
agent base URLs — the spec's (mission, repo) → endpoint chain, collapsed exactly as far
as the rig collapses it.

**Files:**
- Create: `a2a-orchestrator/src/a2a_orchestrator/catalog.py`
- Create: `a2a-orchestrator/catalog.yaml`
- Test: `a2a-orchestrator/tests/test_catalog.py`

**Interfaces:**
- Consumes: the rig's index contract — `GET /` →
  `{"repos": [{"name", "description", "card_url"}]}`.
- Produces (used by `api.py`):
  - `Catalog(index_url: str)`; `Catalog.load(path) -> Catalog` (parses `catalog.yaml`,
    rejects providers other than `"index"`).
  - `Catalog.repos(http: httpx.AsyncClient) -> list[RepoEntry]` (raises `httpx.HTTPError`
    when the index is unreachable).
  - `Catalog.resolve(http, name: str) -> RepoEntry` (raises `LookupError` naming the
    repo).
  - `RepoEntry` dataclass: `name, description, card_url` (all `str`), property
    `base_url -> str` (card URL minus `.well-known/agent-card.json`, keeps trailing `/`).

- [ ] **write-failing-catalog-tests**

`a2a-orchestrator/tests/test_catalog.py`:

```python
"""The index provider, against a live rig."""

from __future__ import annotations

import pytest

from a2a_orchestrator.catalog import Catalog


async def test_repos_lists_the_rig_index(rig_url, http):
    catalog = Catalog(index_url=rig_url)

    entries = await catalog.repos(http)

    names = {entry.name for entry in entries}
    assert {"billing-api", "checkout-web", "infra-terraform"} <= names


async def test_resolve_returns_a_servable_base_url(rig_url, http):
    catalog = Catalog(index_url=rig_url)

    entry = await catalog.resolve(http, "billing-api")

    assert entry.base_url.endswith("/repos/billing-api/")
    card = await http.get(f"{entry.base_url}.well-known/agent-card.json")
    assert card.status_code == 200


async def test_resolve_unknown_repo_raises_lookup_error(rig_url, http):
    catalog = Catalog(index_url=rig_url)

    with pytest.raises(LookupError, match="no-such-repo"):
        await catalog.resolve(http, "no-such-repo")


def test_load_parses_catalog_yaml(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("provider: index\nurl: http://127.0.0.1:9200/\n")

    catalog = Catalog.load(path)

    assert catalog.index_url == "http://127.0.0.1:9200/"


def test_load_rejects_unknown_providers(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("provider: spawn\nurl: http://example/\n")

    with pytest.raises(ValueError, match="spawn"):
        Catalog.load(path)
```

- [ ] **run-catalog-tests-fail**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'a2a_orchestrator.catalog'`.

- [ ] **write-catalog**

`a2a-orchestrator/src/a2a_orchestrator/catalog.py`:

```python
"""The catalog: which repositories the cockpit can reach.

The index provider points at an already-running index (the rig's ``GET /``
today, any a2acode index tomorrow) and resolves a repo name to the base URL
its agent serves at. The spawn provider — launching a2acode per worktree —
belongs to the real-agents milestone, not here; ``load`` rejects anything but
``index`` so a future config typo fails loudly instead of half-working.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

CARD_SUFFIX = ".well-known/agent-card.json"


@dataclass
class RepoEntry:
    name: str
    description: str
    card_url: str

    @property
    def base_url(self) -> str:
        return self.card_url.removesuffix(CARD_SUFFIX)


class Catalog:
    def __init__(self, index_url: str):
        self.index_url = index_url

    @classmethod
    def load(cls, path: str | Path) -> Catalog:
        config = yaml.safe_load(Path(path).read_text())
        provider = config.get("provider")
        if provider != "index":
            raise ValueError(f"unknown catalog provider {provider!r}")
        return cls(index_url=config["url"])

    async def repos(self, http: httpx.AsyncClient) -> list[RepoEntry]:
        response = await http.get(self.index_url)
        response.raise_for_status()
        return [
            RepoEntry(entry["name"], entry["description"], entry["card_url"])
            for entry in response.json()["repos"]
        ]

    async def resolve(self, http: httpx.AsyncClient, name: str) -> RepoEntry:
        entries = await self.repos(http)
        for entry in entries:
            if entry.name == name:
                return entry
        raise LookupError(f"no repo named {name!r} in the catalog")
```

- [ ] **write-catalog-yaml**

`a2a-orchestrator/catalog.yaml` (the checked-in default, pointing at a conventionally-run
rig):

```yaml
# Which repositories the cockpit can reach. The index provider discovers
# repos from a running index (GET /) — the rig at its conventional port
# today, real a2acode instances later. Swapping the URL is the whole
# configuration change the spec promises.
provider: index
url: http://127.0.0.1:9200/
```

- [ ] **run-catalog-tests-pass**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: 5 passed.

- [ ] **commit-catalog**

```bash
git add a2a-orchestrator/src/a2a_orchestrator/catalog.py a2a-orchestrator/catalog.yaml a2a-orchestrator/tests/test_catalog.py
git commit -m "Add the catalog index provider and its checked-in default"
```

---

### missions-api

The management REST plane and the service process itself: Starlette app, `orch-serve`
CLI, the service subprocess fixture, and API tests over the wire.

**Files:**
- Create: `a2a-orchestrator/src/a2a_orchestrator/api.py`
- Create: `a2a-orchestrator/src/a2a_orchestrator/app.py`
- Create: `a2a-orchestrator/src/a2a_orchestrator/serve.py`
- Modify: `a2a-orchestrator/tests/conftest.py` (append the service half)
- Test: `a2a-orchestrator/tests/test_missions_api.py`

**Interfaces:**
- Consumes: `Store` and `Catalog` exactly as produced above.
- Produces:
  - HTTP: `GET /api/catalog` → `{"repos": [{"name", "description"}]}` (502 naming the
    index URL when unreachable); `GET /api/missions` →
    `{"missions": [{id, title, created_at, chats: [chat…]}]}`; `POST /api/missions`
    `{title?}` → 201 mission; `PATCH /api/missions/{mission_id}` `{title}` → 200/404;
    `POST /api/missions/{mission_id}/chats` `{agent}` → 201 chat (404 unknown
    mission/repo — naming the repo — 502 index unreachable). A chat serializes as
    `{context_id, mission_id, agent, a2a_url, created_at}`.
  - `build_app(db_path, catalog_path, frontend_dist: Path | None = None) -> Starlette`
    with `app.state.store/.catalog/.http` populated via lifespan.
  - CLI `orch-serve` / `python -m a2a_orchestrator.serve`: `--host 127.0.0.1`,
    `--port 9300`, `--db var/orchestrator.db`, `--catalog catalog.yaml`,
    `--frontend-dist frontend/dist` (served statically if the directory exists).
  - Fixtures: `service_url` (session, str ending `/`), `mission` (a fresh mission dict),
    `open_chat` (async `(mission_id, agent) -> chat dict`).

- [ ] **write-failing-api-tests**

`a2a-orchestrator/tests/test_missions_api.py`:

```python
"""Management REST over the wire, against the service subprocess.

The service is session-scoped, so tests assert on what they created and
never on database totals.
"""

from __future__ import annotations


async def test_create_mission_and_find_it_listed(service_url, http):
    created = (
        await http.post(f"{service_url}api/missions", json={"title": "Ticket ABC-123"})
    ).json()

    listed = (await http.get(f"{service_url}api/missions")).json()["missions"]

    mine = next(m for m in listed if m["id"] == created["id"])
    assert mine["title"] == "Ticket ABC-123"
    assert mine["chats"] == []


async def test_create_mission_defaults_the_title(service_url, http):
    response = await http.post(f"{service_url}api/missions", json={})

    assert response.status_code == 201
    assert response.json()["title"] == "Untitled mission"


async def test_rename_mission(service_url, http, mission):
    response = await http.patch(
        f"{service_url}api/missions/{mission['id']}", json={"title": "Renamed"}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


async def test_rename_unknown_mission_404s(service_url, http):
    response = await http.patch(
        f"{service_url}api/missions/nope", json={"title": "x"}
    )
    assert response.status_code == 404


async def test_catalog_endpoint_lists_repos(service_url, http):
    repos = (await http.get(f"{service_url}api/catalog")).json()["repos"]
    assert "billing-api" in [repo["name"] for repo in repos]


async def test_open_chat_binds_and_returns_the_context(
    service_url, http, mission, open_chat
):
    chat = await open_chat(mission["id"], "billing-api")

    assert chat["agent"] == "billing-api"
    assert chat["a2a_url"] == f"/a2a/chats/{chat['context_id']}/"

    listed = (await http.get(f"{service_url}api/missions")).json()["missions"]
    mine = next(m for m in listed if m["id"] == mission["id"])
    assert chat["context_id"] in [c["context_id"] for c in mine["chats"]]


async def test_open_chat_with_unknown_repo_names_it(service_url, http, mission):
    response = await http.post(
        f"{service_url}api/missions/{mission['id']}/chats",
        json={"agent": "no-such-repo"},
    )

    assert response.status_code == 404
    assert "no-such-repo" in response.json()["error"]


async def test_open_chat_on_unknown_mission_404s(service_url, http):
    response = await http.post(
        f"{service_url}api/missions/nope/chats", json={"agent": "billing-api"}
    )
    assert response.status_code == 404


async def test_open_chat_without_agent_400s(service_url, http, mission):
    response = await http.post(
        f"{service_url}api/missions/{mission['id']}/chats", json={}
    )
    assert response.status_code == 400
```

- [ ] **write-api-handlers**

`a2a-orchestrator/src/a2a_orchestrator/api.py`:

```python
"""Management REST: what A2A has no vocabulary for — missions, chats, catalog.

Handlers read the store/catalog/http client off ``request.app.state``; the
lifespan in app.py owns their lifetimes. Error bodies always carry an
``error`` key naming what failed, per the spec's error-handling table.
"""

from __future__ import annotations

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from a2a_orchestrator.store import Chat, Mission, Store


def _chat_json(chat: Chat) -> dict:
    return {
        "context_id": chat.context_id,
        "mission_id": chat.mission_id,
        "agent": chat.agent,
        "a2a_url": chat.a2a_url,
        "created_at": chat.created_at,
    }


def _mission_json(store: Store, mission: Mission) -> dict:
    return {
        "id": mission.id,
        "title": mission.title,
        "created_at": mission.created_at,
        "chats": [_chat_json(c) for c in store.chats_for_mission(mission.id)],
    }


async def _body(request: Request) -> dict:
    try:
        parsed = await request.json()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def get_catalog(request: Request) -> JSONResponse:
    catalog, http = request.app.state.catalog, request.app.state.http
    try:
        entries = await catalog.repos(http)
    except httpx.HTTPError as exc:
        return JSONResponse(
            {"error": f"catalog index at {catalog.index_url} unreachable: {exc}"},
            status_code=502,
        )
    return JSONResponse(
        {"repos": [{"name": e.name, "description": e.description} for e in entries]}
    )


async def list_missions(request: Request) -> JSONResponse:
    store = request.app.state.store
    return JSONResponse(
        {"missions": [_mission_json(store, m) for m in store.list_missions()]}
    )


async def create_mission(request: Request) -> JSONResponse:
    store = request.app.state.store
    body = await _body(request)
    mission = store.create_mission(title=body.get("title") or "Untitled mission")
    return JSONResponse(_mission_json(store, mission), status_code=201)


async def rename_mission(request: Request) -> JSONResponse:
    store = request.app.state.store
    body = await _body(request)
    title = body.get("title")
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    mission = store.rename_mission(request.path_params["mission_id"], title)
    if mission is None:
        return JSONResponse({"error": "no such mission"}, status_code=404)
    return JSONResponse(_mission_json(store, mission))


async def open_chat(request: Request) -> JSONResponse:
    store = request.app.state.store
    catalog, http = request.app.state.catalog, request.app.state.http
    mission = store.get_mission(request.path_params["mission_id"])
    if mission is None:
        return JSONResponse({"error": "no such mission"}, status_code=404)
    body = await _body(request)
    agent = body.get("agent")
    if not agent:
        return JSONResponse({"error": "agent is required"}, status_code=400)
    try:
        entry = await catalog.resolve(http, agent)
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except httpx.HTTPError as exc:
        return JSONResponse(
            {"error": f"catalog index at {catalog.index_url} unreachable: {exc}"},
            status_code=502,
        )
    chat = store.create_chat(mission.id, agent, entry.base_url)
    return JSONResponse(_chat_json(chat), status_code=201)
```

- [ ] **write-app-assembly**

`a2a-orchestrator/src/a2a_orchestrator/app.py`:

```python
"""Assemble the service: management REST now, the A2A proxy route in the
a2a-proxy task, and — when a built frontend exists — the static cockpit,
mounted last so /api and /a2a always win."""

from __future__ import annotations

import contextlib
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from a2a_orchestrator import api
from a2a_orchestrator.catalog import Catalog
from a2a_orchestrator.store import Store


def build_app(
    db_path: str | Path,
    catalog_path: str | Path,
    frontend_dist: Path | None = None,
) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.store = Store(db_path)
        app.state.catalog = Catalog.load(catalog_path)
        timeout = httpx.Timeout(120.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as http:
            app.state.http = http
            yield

    routes = [
        Route("/api/catalog", api.get_catalog),
        Route("/api/missions", api.list_missions, methods=["GET"]),
        Route("/api/missions", api.create_mission, methods=["POST"]),
        Route("/api/missions/{mission_id}", api.rename_mission, methods=["PATCH"]),
        Route("/api/missions/{mission_id}/chats", api.open_chat, methods=["POST"]),
    ]
    if frontend_dist and frontend_dist.is_dir():
        routes.append(Mount("/", StaticFiles(directory=frontend_dist, html=True)))
    return Starlette(routes=routes, lifespan=lifespan)
```

- [ ] **write-serve-cli**

`a2a-orchestrator/src/a2a_orchestrator/serve.py`:

```python
"""orch-serve: run the service. Defaults follow the repo's port conventions
(rig 9200, orchestrator 9300) and the project layout (catalog.yaml at the
root, runtime state under var/)."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from a2a_orchestrator.app import build_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(prog="orch-serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9300)
    parser.add_argument("--db", default=str(PROJECT_ROOT / "var" / "orchestrator.db"))
    parser.add_argument("--catalog", default=str(PROJECT_ROOT / "catalog.yaml"))
    parser.add_argument(
        "--frontend-dist",
        default=str(PROJECT_ROOT / "frontend" / "dist"),
        help="Serve this directory statically if it exists (demo mode).",
    )
    args = parser.parse_args()
    app = build_app(args.db, args.catalog, frontend_dist=Path(args.frontend_dist))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **extend-conftest-with-service**

Append to `a2a-orchestrator/tests/conftest.py` (new imports merge with the existing
ones):

```python
import subprocess
import sys
import time

from a2a_rig.server import free_port

SERVICE_STARTUP_TIMEOUT_S = 30.0


@pytest.fixture(scope="session")
def service_url(rig_url, tmp_path_factory) -> str:
    """orch-serve as a real subprocess, cataloged against the session rig."""
    workdir = tmp_path_factory.mktemp("orchestrator")
    catalog = workdir / "catalog.yaml"
    catalog.write_text(f"provider: index\nurl: {rig_url}\n")
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "a2a_orchestrator.serve",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--db", str(workdir / "orchestrator.db"),
            "--catalog", str(catalog),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + SERVICE_STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"orch-serve exited early:\n{proc.stdout.read()}")
        try:
            if httpx.get(f"{url}api/missions", timeout=2.0).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        proc.terminate()
        raise TimeoutError("orch-serve did not come up in time")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest_asyncio.fixture
async def mission(service_url, http) -> dict:
    response = await http.post(f"{service_url}api/missions", json={})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def open_chat(service_url, http):
    async def _open(mission_id: str, agent: str) -> dict:
        response = await http.post(
            f"{service_url}api/missions/{mission_id}/chats", json={"agent": agent}
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _open
```

- [ ] **run-api-tests**

Run: `uv run pytest tests/test_missions_api.py -v`
Expected: 9 passed.

- [ ] **run-whole-suite**

Run: `uv run pytest`
Expected: 24 passed (smoke 2 + store 8 + catalog 5 + these 9), no failures.

- [ ] **commit-missions-api**

```bash
git add a2a-orchestrator/src/a2a_orchestrator a2a-orchestrator/tests
git commit -m "Add the management REST plane and the orch-serve process"
```

---

### a2a-proxy

The conversation plane: the contextId-routed pass-through relay with the card-rewrite
exception. This is the milestone's heart, and its tests are the milestone's named exit
("proxy routing, card rewrite, missions API, against a live rig-serve").

**Files:**
- Create: `a2a-orchestrator/src/a2a_orchestrator/proxy.py`
- Modify: `a2a-orchestrator/src/a2a_orchestrator/app.py` (add the proxy route)
- Test: `a2a-orchestrator/tests/test_proxy.py`

**Interfaces:**
- Consumes: `Store.chat_for_context(context_id) -> Chat | None` (`Chat.upstream_url` ends
  with `/`); `app.state.http`.
- Produces: `proxy.a2a_endpoint(request)` handling
  `/a2a/chats/{context_id}/{path:path}` for GET and POST — 404 JSON for unbound
  contexts, buffered+rewritten agent card, raw streaming relay for everything else.
  Helper `rewrite_card(text: str, upstream_url: str, proxy_base: str) -> str` (pure,
  directly testable).

- [ ] **write-failing-proxy-tests**

`a2a-orchestrator/tests/test_proxy.py`:

```python
"""The conversation plane: genuine A2A through the proxy, against real fakes.

The client is the python a2a-sdk — the same protocol surface the browser's
a2a-js client drives — and it follows the same two-step the browser does:
fetch the card from the proxied base, then speak JSON-RPC+SSE to whatever URL
the card advertises. If the card rewrite is wrong, every test below escapes
the proxy and fails; that is the point.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from a2a.client import create_client
from a2a.client.client import ClientConfig
from a2a_rig.events import send


@pytest.fixture
def connect(service_url, http):
    async def _connect(chat: dict):
        base = f"{service_url.rstrip('/')}{chat['a2a_url']}"
        return await create_client(
            base, ClientConfig(streaming=True, httpx_client=http)
        )

    return _connect


async def test_card_is_rewritten_to_the_proxy(
    service_url, http, mission, open_chat, rig_url
):
    chat = await open_chat(mission["id"], "billing-api")

    card_url = f"{service_url.rstrip('/')}{chat['a2a_url']}.well-known/agent-card.json"
    response = await http.get(card_url)

    assert response.status_code == 200
    rig_port = urlsplit(rig_url).port
    assert f"127.0.0.1:{rig_port}" not in response.text
    assert f"localhost:{rig_port}" not in response.text
    assert chat["a2a_url"] in response.text


async def test_free_text_round_trips_over_a2a(mission, open_chat, connect):
    chat = await open_chat(mission["id"], "billing-api")
    client = await connect(chat)

    capture = await send(client, "hello from the cockpit")

    assert capture.final_state == "completed"
    assert "Ready when you are" in capture.artifact_text()


async def test_upstream_adopts_the_service_minted_context(
    mission, open_chat, connect
):
    """The wire check the spec's open question asked for: the service mints
    the contextId at chat-open, the client sends it on turn one, and the
    upstream adopts it rather than replacing it."""
    chat = await open_chat(mission["id"], "billing-api")
    client = await connect(chat)

    capture = await send(client, "hello", context_id=chat["context_id"])

    assert capture.final_state == "completed"
    assert capture.context_id == chat["context_id"]


async def test_turns_share_the_context_across_sends(mission, open_chat, connect):
    chat = await open_chat(mission["id"], "billing-api")
    client = await connect(chat)

    first = await send(client, "hello", context_id=chat["context_id"])
    second = await send(client, "hello again", context_id=chat["context_id"])

    assert first.context_id == chat["context_id"]
    assert second.context_id == chat["context_id"]
    assert first.task_id != second.task_id


async def test_approval_round_trips_through_the_proxy(mission, open_chat, connect):
    chat = await open_chat(mission["id"], "billing-api")
    client = await connect(chat)

    parked = await send(
        client, "please run the tests", context_id=chat["context_id"]
    )

    assert parked.final_state == "input_required"
    assert parked.permission is not None
    assert parked.permission["tool"] == "Bash"

    resumed = await send(
        client, "allow", task_id=parked.task_id, context_id=parked.context_id
    )

    assert resumed.final_state == "completed"


async def test_two_chats_route_to_their_own_repos(mission, open_chat, connect):
    billing = await open_chat(mission["id"], "billing-api")
    checkout = await open_chat(mission["id"], "checkout-web")

    billing_reply = await send(
        await connect(billing), "what is this repo?",
        context_id=billing["context_id"],
    )
    checkout_reply = await send(
        await connect(checkout), "what is this repo?",
        context_id=checkout["context_id"],
    )

    assert "Ready when you are" in billing_reply.artifact_text()
    assert "checkout flow" in checkout_reply.artifact_text()


async def test_upstream_failure_relays_as_a_failed_task(
    mission, open_chat, connect
):
    chat = await open_chat(mission["id"], "infra-terraform")

    capture = await send(
        await connect(chat), "status check please", context_id=chat["context_id"]
    )

    assert capture.final_state == "failed"


async def test_unbound_context_404s(service_url, http):
    response = await http.post(
        f"{service_url}a2a/chats/deadbeef/",
        json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {}},
    )
    assert response.status_code == 404
    assert "deadbeef" in response.json()["error"]
```

(Free-text markers are pinned rig facts: billing-api answers "…Ready when you are." on
both its greeting and default plays; checkout-web's default says "checkout flow";
infra-terraform's default play fails the task. "status check please" deliberately avoids
"apply", which would hit `30-plan-and-apply` instead.)

- [ ] **run-proxy-tests-fail**

Run: `uv run pytest tests/test_proxy.py -v`
Expected: FAIL — every test 404s (no `/a2a` route exists yet). The card test fails on
status code, the client-driven ones on card fetch.

- [ ] **write-proxy**

`a2a-orchestrator/src/a2a_orchestrator/proxy.py`:

```python
"""The conversation plane: a contextId-routed pass-through A2A proxy.

Every chat's base URL is ``/a2a/chats/{context_id}/`` — the contextId rides
the path, so routing any call (message/stream today, a cold
``tasks/resubscribe`` after a browser reload tomorrow) is a store lookup,
never a guess from observed traffic. The relay forwards bytes unmodified in
both directions, with the spec's one deliberate exception: agent cards
advertise the upstream's own origin (in both ``localhost`` and ``127.0.0.1``
spellings), so card responses are buffered and rewritten to the proxy's own
base — otherwise the browser's client escapes the proxy on its next call.
The proxy base is derived from the request's own host, so it stays correct
behind the Vite dev proxy and when hit directly alike.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

CARD_PATH = ".well-known/agent-card.json"

_HOP_BY_HOP = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _upstream_spellings(upstream_url: str) -> list[str]:
    """The upstream base in every spelling its cards are known to use."""
    parts = urlsplit(upstream_url)
    port = f":{parts.port}" if parts.port else ""
    hosts = {parts.hostname, "127.0.0.1", "localhost"} - {None}
    return [
        urlunsplit((parts.scheme, f"{host}{port}", parts.path, "", ""))
        for host in sorted(hosts)
    ]


def rewrite_card(text: str, upstream_url: str, proxy_base: str) -> str:
    for spelling in _upstream_spellings(upstream_url):
        text = text.replace(spelling, proxy_base)
        text = text.replace(spelling.rstrip("/"), proxy_base.rstrip("/"))
    return text


async def a2a_endpoint(request: Request) -> Response:
    store, http = request.app.state.store, request.app.state.http
    context_id = request.path_params["context_id"]
    path = request.path_params["path"]

    chat = store.chat_for_context(context_id)
    if chat is None:
        return JSONResponse(
            {"error": f"no chat bound for context {context_id!r}"}, status_code=404
        )

    target = f"{chat.upstream_url}{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    proxy_base = (
        f"{request.url.scheme}://{request.url.netloc}/a2a/chats/{context_id}/"
    )

    if request.method == "GET" and path == CARD_PATH:
        upstream = await http.get(target)
        return Response(
            rewrite_card(upstream.text, chat.upstream_url, proxy_base),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    upstream_request = http.build_request(
        request.method, target, content=request.stream(), headers=headers
    )
    upstream = await http.send(upstream_request, stream=True)
    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream.aclose),
    )
```

- [ ] **mount-proxy-route**

In `a2a-orchestrator/src/a2a_orchestrator/app.py`, add the import and route. The import
block gains one line:

```python
from a2a_orchestrator import api, proxy
```

and the `routes` list gains, after the `/api` routes and before the static mount:

```python
        Route(
            "/a2a/chats/{context_id}/{path:path}",
            proxy.a2a_endpoint,
            methods=["GET", "POST"],
        ),
```

- [ ] **run-proxy-tests-pass**

Run: `uv run pytest tests/test_proxy.py -v`
Expected: 8 passed. If `test_upstream_adopts_the_service_minted_context` alone fails,
the upstream replaced the client's contextId — that contradicts the verified SDK source,
so re-check against a2acode's installed `a2a/server/agent_execution/context.py` before
changing any design (the fallback would be binding the upstream-minted id to the chat at
first response, but the verification says this won't be needed).

- [ ] **run-whole-suite-again**

Run: `uv run pytest`
Expected: 32 passed.

- [ ] **commit-proxy**

```bash
git add a2a-orchestrator/src/a2a_orchestrator/proxy.py a2a-orchestrator/src/a2a_orchestrator/app.py a2a-orchestrator/tests/test_proxy.py
git commit -m "Add the contextId-routed A2A proxy with the card-rewrite exception"
```

---

### frontend-shell

The Vite + React + TS cockpit shell: mission list, mission view, chat opening — the
management plane wired end to end. Conversation lands in the next task; here the bound
chat renders as its contextId, which proves the REST loop from a browser.

**Files:**
- Create: `a2a-orchestrator/frontend/` (Vite scaffold: `package.json`, `tsconfig*.json`,
  `index.html`, `src/main.tsx`, …)
- Create/replace: `a2a-orchestrator/frontend/vite.config.ts`
- Create: `a2a-orchestrator/frontend/src/api.ts`
- Replace: `a2a-orchestrator/frontend/src/App.tsx`
- Replace: `a2a-orchestrator/frontend/src/index.css`

**Interfaces:**
- Consumes: the management REST endpoints exactly as produced by `missions-api`.
- Produces (used by `chat-pane-and-approvals`): TS types `Mission`
  (`{id, title, created_at, chats}`), `ChatRef`
  (`{context_id, mission_id, agent, a2a_url, created_at}`), `RepoEntry`
  (`{name, description}`); functions `listMissions()`, `createMission(title?)`,
  `listRepos()`, `openChat(missionId, agent)`; `App` renders the selected chat via a
  placeholder that the next task replaces with `<ChatPane chat={chat} …/>`.

- [ ] **scaffold-vite**

From `a2a-orchestrator/`:

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install @a2a-js/sdk@^1.0.1
```

Delete the scaffold's demo styling/content that the steps below don't replace:
`src/App.css` and `src/assets/` (and their imports — `App.tsx` and `index.css` are
replaced wholesale below). Set the `<title>` in `index.html` to `cockpit`.

- [ ] **write-vite-config**

`a2a-orchestrator/frontend/vite.config.ts` (replaces the scaffolded one):

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Both planes proxy to the service in development, so the browser sees one
// origin — which is also why the service's card rewrite (keyed on the
// request's own Host) hands the a2a-js client URLs that stay inside the
// proxy chain.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:9300',
      '/a2a': 'http://127.0.0.1:9300',
    },
  },
})
```

- [ ] **write-api-wrappers**

`a2a-orchestrator/frontend/src/api.ts`:

```ts
// Management REST: typed wrappers over the service's /api endpoints.

export interface ChatRef {
  context_id: string
  mission_id: string
  agent: string
  a2a_url: string
  created_at: string
}

export interface Mission {
  id: string
  title: string
  created_at: string
  chats: ChatRef[]
}

export interface RepoEntry {
  name: string
  description: string
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${await response.text()}`)
  }
  return response.json() as Promise<T>
}

function post(url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function listMissions(): Promise<Mission[]> {
  const data = await json<{ missions: Mission[] }>(await fetch('/api/missions'))
  return data.missions
}

export async function createMission(title?: string): Promise<Mission> {
  return json<Mission>(await post('/api/missions', title ? { title } : {}))
}

export async function listRepos(): Promise<RepoEntry[]> {
  const data = await json<{ repos: RepoEntry[] }>(await fetch('/api/catalog'))
  return data.repos
}

export async function openChat(missionId: string, agent: string): Promise<ChatRef> {
  return json<ChatRef>(await post(`/api/missions/${missionId}/chats`, { agent }))
}
```

- [ ] **write-app-shell**

`a2a-orchestrator/frontend/src/App.tsx` (replaces the scaffolded one; the
`chat bound:` line is the placeholder `chat-pane-and-approvals` replaces):

```tsx
import { useEffect, useState } from 'react'
import {
  createMission,
  listMissions,
  listRepos,
  openChat,
  type ChatRef,
  type Mission,
  type RepoEntry,
} from './api'

export default function App() {
  const [missions, setMissions] = useState<Mission[]>([])
  const [repos, setRepos] = useState<RepoEntry[]>([])
  const [missionId, setMissionId] = useState<string | null>(null)
  const [chat, setChat] = useState<ChatRef | null>(null)
  const [repoChoice, setRepoChoice] = useState('')
  const [error, setError] = useState('')

  const refresh = () =>
    listMissions().then(setMissions).catch((e) => setError(String(e)))

  useEffect(() => {
    refresh()
    listRepos()
      .then((entries) => {
        setRepos(entries)
        if (entries.length > 0) setRepoChoice(entries[0].name)
      })
      .catch((e) => setError(String(e)))
  }, [])

  const startMission = async () => {
    try {
      const created = await createMission()
      await refresh()
      setMissionId(created.id)
      setChat(null)
    } catch (e) {
      setError(String(e))
    }
  }

  const startChat = async () => {
    if (!missionId || !repoChoice) return
    try {
      const opened = await openChat(missionId, repoChoice)
      await refresh()
      setChat(opened)
    } catch (e) {
      setError(String(e))
    }
  }

  const mission = missions.find((m) => m.id === missionId) ?? null

  if (!mission) {
    return (
      <main>
        <h1>cockpit</h1>
        {error && <p className="error">{error}</p>}
        <button onClick={startMission}>New mission</button>
        <ul>
          {missions.map((m) => (
            <li key={m.id}>
              <a href="#" onClick={(e) => { e.preventDefault(); setMissionId(m.id) }}>
                {m.title}
              </a>{' '}
              — {m.chats.length} chat{m.chats.length === 1 ? '' : 's'}
            </li>
          ))}
        </ul>
      </main>
    )
  }

  return (
    <main>
      <h1>
        <a href="#" onClick={(e) => { e.preventDefault(); setMissionId(null); setChat(null) }}>
          cockpit
        </a>{' '}
        / {mission.title}
      </h1>
      {error && <p className="error">{error}</p>}
      <p>
        <select value={repoChoice} onChange={(e) => setRepoChoice(e.target.value)}>
          {repos.map((r) => (
            <option key={r.name} value={r.name}>{r.name}</option>
          ))}
        </select>{' '}
        <button onClick={startChat}>Open chat</button>
      </p>
      <ul>
        {mission.chats.map((c) => (
          <li key={c.context_id}>
            <a href="#" onClick={(e) => { e.preventDefault(); setChat(c) }}>
              {c.agent}
            </a>
          </li>
        ))}
      </ul>
      {chat && <p>chat bound: {chat.context_id}</p>}
    </main>
  )
}
```

- [ ] **write-css**

`a2a-orchestrator/frontend/src/index.css` (replaces the scaffolded one):

```css
:root {
  font-family: system-ui, sans-serif;
  color-scheme: light dark;
}

body {
  margin: 0 auto;
  padding: 1rem 2rem;
  max-width: 60rem;
}

p.error {
  color: crimson;
}

ol.log {
  list-style: none;
  padding: 0;
}

ol.log li {
  margin: 0.25rem 0;
  white-space: pre-wrap;
}

ol.log li.system {
  opacity: 0.6;
  font-size: 0.9em;
}

aside.approval {
  border: 1px solid darkorange;
  border-radius: 4px;
  padding: 0.5rem 1rem;
  margin: 0.5rem 0;
}

aside.approval button {
  margin-right: 0.5rem;
}
```

- [ ] **build-check**

Run: `cd frontend && npm run build`
Expected: `tsc` and Vite both succeed, `dist/` appears.

- [ ] **manual-shell-check**

With the rig and service running (three terminals from `a2a-orchestrator/`):

```bash
uv run rig-serve --repos ../a2a-rig/repos --port 9200      # terminal 1
uv run orch-serve                                          # terminal 2
cd frontend && npm run dev                                 # terminal 3
```

Open http://localhost:5173 — create a mission, open a chat with `billing-api`, see
`chat bound: <contextId>` render. (rig-serve resolves from this project's venv because
a2a-rig is an editable dev dependency.)

- [ ] **commit-shell**

```bash
git add a2a-orchestrator/frontend
git commit -m "Add the cockpit shell: mission list and chat opening over REST"
```

---

### chat-pane-and-approvals

The conversation plane in the browser: a real a2a-js client per chat, streamed turns
distilled into renderable events, and the approval card answering `input-required` with
allow/deny. Completes the milestone's 👀 demo.

**Files:**
- Create: `a2a-orchestrator/frontend/src/a2a.ts`
- Create: `a2a-orchestrator/frontend/src/ChatPane.tsx`
- Create: `a2a-orchestrator/frontend/src/ApprovalCard.tsx`
- Modify: `a2a-orchestrator/frontend/src/App.tsx` (replace the placeholder)

**Interfaces:**
- Consumes: `ChatRef` from `api.ts`; the proxy's per-chat base (`chat.a2a_url`, trailing
  slash included); `@a2a-js/sdk` 1.0.1 shapes, verified against the published package
  2026-08-10: `StreamResponse.payload.$case` ∈ `task | message | statusUpdate |
  artifactUpdate`; `TaskStatusUpdateEvent {taskId, contextId, status}`; `TaskStatus
  {state: TaskState, message}`; `Message.metadata` is a **plain JS object**
  (`{[key: string]: any} | undefined` — no protobuf Struct decoding);
  `Part.content.$case === 'text'`; `SendMessageRequest` requires `tenant`,
  `configuration`, `metadata` fields; `taskStateToJSON(TaskState) -> "TASK_STATE_…"`.
- Produces: `connect(a2aUrl) -> Promise<Client>`; `sendTurn(client, text, {contextId,
  taskId?}) -> AsyncGenerator<ChatEvent>` with `ChatEvent` =
  `{kind:'task', taskId, contextId} | {kind:'status', state, text} |
  {kind:'permission', taskId, contextId, permission: Permission} |
  {kind:'artifact-text', text}` and `Permission = {tool, request_id, input}`;
  components `ChatPane({chat})`, `ApprovalCard({approval, onAnswer})`.

- [ ] **write-a2a-client-layer**

`a2a-orchestrator/frontend/src/a2a.ts`:

```ts
// The conversation plane: a real a2a-js client per chat, talking through the
// service's contextId-routed proxy, distilled into renderable events.
//
// The approval payload rides the status message's metadata under
// `a2acode_permission` ({tool, request_id, input}) — metadata is a plain JS
// object in a2a-js, so no Struct decoding. The stream ends when a task parks
// in input_required; answering is a new message on the same taskId.

import { Role, TaskState, taskStateToJSON, type Part } from '@a2a-js/sdk'
import { ClientFactory, type Client } from '@a2a-js/sdk/client'

export interface Permission {
  tool: string
  request_id: string
  input: Record<string, unknown>
}

export type ChatEvent =
  | { kind: 'task'; taskId: string; contextId: string }
  | { kind: 'status'; state: string; text: string }
  | { kind: 'permission'; taskId: string; contextId: string; permission: Permission }
  | { kind: 'artifact-text'; text: string }

// createFromUrl resolves the card relative to its argument, so the trailing
// slash the service puts on a2a_url is load-bearing — without it the last
// path segment drops and the card fetch 404s.
export function connect(a2aUrl: string): Promise<Client> {
  const base = new URL(a2aUrl, window.location.origin).toString()
  return new ClientFactory().createFromUrl(base)
}

function textOf(parts: Part[] | undefined): string {
  return (parts ?? [])
    .map((part) => (part.content?.$case === 'text' ? part.content.value : ''))
    .join('')
}

function stateName(state: TaskState | undefined): string {
  if (state === undefined) return 'unknown'
  return taskStateToJSON(state).replace('TASK_STATE_', '').toLowerCase()
}

export async function* sendTurn(
  client: Client,
  text: string,
  ids: { contextId: string; taskId?: string },
): AsyncGenerator<ChatEvent> {
  const stream = client.sendMessageStream({
    tenant: '',
    configuration: undefined,
    metadata: undefined,
    message: {
      messageId: crypto.randomUUID(),
      contextId: ids.contextId,
      taskId: ids.taskId ?? '',
      role: Role.ROLE_USER,
      parts: [
        {
          content: { $case: 'text', value: text },
          metadata: undefined,
          filename: '',
          mediaType: '',
        },
      ],
      metadata: undefined,
      extensions: [],
      referenceTaskIds: [],
    },
  })
  for await (const response of stream) {
    const payload = response.payload
    if (!payload) continue
    if (payload.$case === 'task') {
      yield { kind: 'task', taskId: payload.value.id, contextId: payload.value.contextId }
    } else if (payload.$case === 'statusUpdate') {
      const { taskId, contextId, status } = payload.value
      const state = stateName(status?.state)
      const permission = status?.message?.metadata?.['a2acode_permission'] as
        | Permission
        | undefined
      if (state === 'input_required' && permission) {
        yield { kind: 'permission', taskId, contextId, permission }
      } else {
        yield { kind: 'status', state, text: textOf(status?.message?.parts) }
      }
    } else if (payload.$case === 'artifactUpdate') {
      yield { kind: 'artifact-text', text: textOf(payload.value.artifact?.parts) }
    }
  }
}
```

- [ ] **write-approval-card**

`a2a-orchestrator/frontend/src/ApprovalCard.tsx`:

```tsx
import type { Permission } from './a2a'

export function ApprovalCard({
  permission,
  onAnswer,
}: {
  permission: Permission
  onAnswer: (decision: 'allow' | 'deny') => void
}) {
  return (
    <aside className="approval">
      <p>
        <b>Approval requested:</b> {permission.tool}
      </p>
      <pre>{JSON.stringify(permission.input, null, 2)}</pre>
      <button onClick={() => onAnswer('allow')}>Allow</button>
      <button onClick={() => onAnswer('deny')}>Deny</button>
    </aside>
  )
}
```

- [ ] **write-chat-pane**

`a2a-orchestrator/frontend/src/ChatPane.tsx`:

```tsx
import { useRef, useState } from 'react'
import type { Client } from '@a2a-js/sdk/client'
import type { ChatRef } from './api'
import { connect, sendTurn, type Permission } from './a2a'
import { ApprovalCard } from './ApprovalCard'

interface LogItem {
  who: 'you' | 'agent' | 'system'
  text: string
}

interface PendingApproval {
  taskId: string
  permission: Permission
}

export function ChatPane({ chat }: { chat: ChatRef }) {
  const clientRef = useRef<Promise<Client> | null>(null)
  const [log, setLog] = useState<LogItem[]>([])
  const [approval, setApproval] = useState<PendingApproval | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  if (clientRef.current === null) clientRef.current = connect(chat.a2a_url)

  const append = (item: LogItem) => setLog((prev) => [...prev, item])

  // One turn: send, then drain the stream. The stream ends on terminal
  // states and on input_required alike, so this always returns; a parked
  // approval is left in state for the card to answer as its own turn.
  const runTurn = async (text: string, taskId?: string) => {
    setBusy(true)
    try {
      const client = await clientRef.current!
      const turn = sendTurn(client, text, { contextId: chat.context_id, taskId })
      for await (const event of turn) {
        if (event.kind === 'artifact-text' && event.text) {
          append({ who: 'agent', text: event.text })
        } else if (event.kind === 'permission') {
          setApproval({ taskId: event.taskId, permission: event.permission })
        } else if (event.kind === 'status') {
          append({
            who: 'system',
            text: event.text ? `${event.state} — ${event.text}` : event.state,
          })
        }
      }
    } catch (error) {
      append({ who: 'system', text: `error: ${String(error)}` })
    } finally {
      setBusy(false)
    }
  }

  const sendDraft = async () => {
    const text = draft.trim()
    if (!text) return
    append({ who: 'you', text })
    setDraft('')
    await runTurn(text)
  }

  const answer = async (decision: 'allow' | 'deny') => {
    if (!approval) return
    const parked = approval
    setApproval(null)
    append({ who: 'you', text: decision })
    await runTurn(decision, parked.taskId)
  }

  return (
    <section>
      <h2>{chat.agent}</h2>
      <ol className="log">
        {log.map((item, i) => (
          <li key={i} className={item.who}>
            <b>{item.who}</b> {item.text}
          </li>
        ))}
      </ol>
      {approval && (
        <ApprovalCard permission={approval.permission} onAnswer={answer} />
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          sendDraft()
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={busy || approval !== null}
          placeholder={`Message ${chat.agent}`}
          size={60}
        />
        <button disabled={busy || approval !== null}>Send</button>
      </form>
    </section>
  )
}
```

- [ ] **replace-placeholder-in-app**

In `a2a-orchestrator/frontend/src/App.tsx`, add the import:

```tsx
import { ChatPane } from './ChatPane'
```

and replace the placeholder line

```tsx
      {chat && <p>chat bound: {chat.context_id}</p>}
```

with (the `key` remounts the pane — fresh client, fresh log — when switching chats):

```tsx
      {chat && <ChatPane chat={chat} key={chat.context_id} />}
```

- [ ] **build-check-conversation**

Run: `cd frontend && npm run build`
Expected: clean `tsc` + Vite build.

- [ ] **manual-conversation-check**

With the three processes from `frontend-shell` still running, at http://localhost:5173:

1. Open a chat with `billing-api`, send `hello` → system status lines stream, agent
   text ends with "Ready when you are."
2. Send `please run the tests` → the approval card appears naming **Bash** with the
   `pytest tests/ -q` input; input box disables.
3. Click **Allow** → the run resumes and completes.
4. Open a chat with `infra-terraform`, send anything → the turn fails visibly
   (status `failed`).

If step 2's card never appears, check the browser console for the raw statusUpdate —
the metadata key must read `a2acode_permission` end to end.

- [ ] **commit-conversation**

```bash
git add a2a-orchestrator/frontend/src
git commit -m "Add the chat pane and approval card over a real a2a-js client"
```

---

### static-serve-readme-demo

The self-contained README, the one-process demo mode, and the milestone's exit check.

**Files:**
- Create: `a2a-orchestrator/README.md`
- Verify (no change expected): static serving via `serve.py`'s `--frontend-dist`

**Interfaces:**
- Consumes: everything above.
- Produces: the milestone's 👀 demo, reproducible from the README alone.

- [ ] **write-readme**

`a2a-orchestrator/README.md`:

```markdown
# a2a-orchestrator

The cockpit: coordinate agent work across repos over A2A — chat with repo
agents, watch sessions stream, answer approvals from one place. This is the
`direct-sessions` walking skeleton: direct chats with repo agents through a
contextId-routed pass-through proxy, against
[a2a-rig](../a2a-rig/README.md)'s deterministic fakes. The design of record
is [the spec](../docs/superpowers/specs/2026-08-09-a2a-orchestrator-design.md).

## Layout

Two toolchains, each self-contained at its own root: `src/` + `tests/` are a
uv project (the service), `frontend/` is an npm project (the cockpit UI).
`catalog.yaml` names the index the service discovers repos from. Runtime
state lives in `var/` (gitignored).

## Run it

Three terminals, from this directory:

    uv run rig-serve --repos ../a2a-rig/repos --port 9200
    uv run orch-serve
    cd frontend && npm install && npm run dev

Open http://localhost:5173. (`rig-serve` resolves here because a2a-rig is an
editable dev dependency.)

One-process demo mode — build the frontend and the service serves it
statically at http://127.0.0.1:9300:

    (cd frontend && npm run build)
    uv run orch-serve

## The demo

1. **New mission** — describe nothing, configure nothing; a mission is
   created by starting one.
2. Open a chat with `billing-api` and say hello — the reply streams over
   genuine A2A (JSON-RPC + SSE) through the service's proxy.
3. Say `please run the tests` — the task parks in `input-required` and an
   approval card appears naming the tool and its input.
4. **Allow** — the run resumes to completion. (Deny works too; the scenario
   answers "Skipped the test run.")
5. Open a chat with `infra-terraform` and say anything — its default play
   fails, and the turn renders as failed.

## Tests

    uv run pytest

pytest drives real subprocesses: a `playback` rig serving `../a2a-rig/repos`
and `orch-serve` in front of it — proxy routing, the agent-card rewrite,
and the missions API, zero inference.
```

- [ ] **verify-one-process-demo**

```bash
(cd frontend && npm run build)
uv run orch-serve
```

Open http://127.0.0.1:9300 (rig still running on 9200) and click through demo steps 1–3.
Expected: identical behavior to the Vite dev server — the card rewrite keys on the
request's Host, so both origins work.

- [ ] **final-suite-run**

Run: `uv run pytest && (cd frontend && npm run build)`
Expected: 32 passed; clean build. Also confirm the rig's own suite still passes
untouched: `cd ../a2a-rig && uv run pytest` → 165 passed / 4 xfailed.

- [ ] **commit-readme**

```bash
git add a2a-orchestrator/README.md
git commit -m "Add the a2a-orchestrator README with the direct-sessions demo"
```

- [ ] **milestone-exit**

The 👀 demo, performed end to end in a browser: start a fresh mission, chat with a fake
repo in free text over genuine A2A, answer an approval via `input-required` — use cases
1 and 4, zero inference. Then hand off per
`superpowers:finishing-a-development-branch` (merge to `main`, clean up the worktree).
DEVLOG entry and PLAN.md notes happen at session close per repo convention; the PLAN.md
Phase 6 bullet stays **unchecked** until `e2e-suite`.
```

---

## Deviations to expect

- **Version floors** (`starlette>=0.47`, `uvicorn>=0.30`, npm scaffold versions) are
  floors, not verified pins — take whatever `uv`/`npm` resolves and commit the locks.
- **Test counts** in "Expected" lines assume the file contents above; if a count drifts
  but everything passes, the count is what's wrong.
- The **a2a-js scaffold's strict tsconfig** may flag unused scaffold files not deleted in
  `scaffold-vite` — delete what it names rather than loosening the config.
