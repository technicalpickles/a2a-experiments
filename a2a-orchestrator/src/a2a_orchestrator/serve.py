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
