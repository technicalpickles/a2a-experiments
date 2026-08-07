"""`rig-serve` — a2acode's server with a scripted brain.

Thin on purpose. a2acode's backends are constructor-injected into
``build_app()``, so standing up a fake producer needs no fork and no patch:
construct the backend, hand it over, run it.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn
from a2acode.server import build_app
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .backend import PlaybackBackend
from .mounting import mount_lifespans
from .repo import Repo, RepoError, load_repo, load_repos
from .scenario import ScenarioError


def repo_mount_path(repo_id: str) -> str:
    """Where a repo lives under a multi-repo rig: mount point and card URL alike.

    One helper so the mount and the URL the index advertises for it cannot
    drift apart — they used to be spelled separately in three places.
    """
    return f"/repos/{repo_id}"


def build_repo_app(repo: Repo, *, url: str):
    """One repo's a2acode app, with the playback backend injected."""
    backend = PlaybackBackend(repo)
    return build_app(
        backend,
        url=url,
        card_name=repo.card_name,
        card_description=repo.card_description,
    )


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
                    f"{base}{repo_mount_path(repo.repo_id)}/"
                    ".well-known/agent-card.json"
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
        build_repo_app(repo, url=f"{base}{repo_mount_path(repo.repo_id)}/")
        for repo in repos
    ]
    routes = [Route("/", index)]
    routes += [
        Mount(repo_mount_path(repo.repo_id), app=child)
        for repo, child in zip(repos, children)
    ]
    # Mounted apps do not get their lifespans run by the parent for free, and
    # a2acode's lifespan is what closes each backend and push-notification
    # client on shutdown. See mounting.py.
    return Starlette(routes=routes, lifespan=mount_lifespans(children))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-serve", description="Serve scripted A2A agents from a repo directory."
    )
    parser.add_argument("--repo", help="Path to one repo directory.")
    parser.add_argument("--repos", help="Path to a directory of repo directories.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument(
        "--log-level", default="info", help="uvicorn log level (default: info)."
    )
    args = parser.parse_args(argv)

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
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
