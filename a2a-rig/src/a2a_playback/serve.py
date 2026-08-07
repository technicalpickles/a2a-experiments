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

from .backend import PlaybackBackend
from .repo import RepoError, load_repo
from .scenario import ScenarioError


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


if __name__ == "__main__":
    raise SystemExit(main())
