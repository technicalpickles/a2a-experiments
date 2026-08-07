"""`rig-serve` — a2acode's server with a scripted brain.

Thin on purpose. a2acode's backends are constructor-injected into
``build_app()``, so standing up a fake producer needs no fork and no patch:
construct the backend, hand it over, run it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from a2acode.server import build_app

from .backend import PlaybackBackend
from .scenario import ScenarioError, load


def build(scenario_path: str | Path, *, url: str):
    scenario = load(scenario_path)
    backend = PlaybackBackend(scenario)
    return build_app(
        backend,
        url=url,
        card_name=scenario.card_name,
        card_description=scenario.card_description,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-serve", description="Serve a scripted A2A agent from a scenario file."
    )
    parser.add_argument("--scenario", required=True, help="Path to a scenario YAML.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument(
        "--log-level", default="info", help="uvicorn log level (default: info)."
    )
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}/"
    try:
        app = build(args.scenario, url=url)
    except ScenarioError as exc:
        # Scenario problems are user errors, not crashes: say what is wrong and
        # exit, rather than burying it in a traceback.
        print(f"rig-serve: {exc}", file=sys.stderr)
        return 2

    print(f"rig-serve: scenario={args.scenario} card={url}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
