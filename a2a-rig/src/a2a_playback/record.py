"""`rig-record` — a real agent, served, with everything it does written down.

Recording taps at the BackendEvent level *inside* the server, so something has
to drive it from outside. That something has to be a real A2A client, because
the permission round trip *is* an `input-required` exchange with a caller.
Running one-shot would mean either --permission-mode acceptEdits (which never
records a gate at all) or inventing a console prompt. Serving means a recorded
run went through the same path Phase 2 and Phase 5 did.

`--backend playback` is supported on purpose: recording the scripted backend is
how the recorder is tested end to end without spending a cent on inference, and
making that a real mode beats bolting test-only scaffolding onto the side.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from a2acode.server import build_app

from .backend import PlaybackBackend
from .recording import RecordingBackend
from .repo import SCENARIOS_DIR, RepoError, load_repo
from .scenario import ScenarioError


def build_recording_backend(args) -> RecordingBackend:
    """The backend under test, wrapped. Raises RepoError/ScenarioError/ValueError."""
    if args.backend == "playback":
        inner = PlaybackBackend(load_repo(args.repo))
        label = "playback"
    else:
        from a2acode.backends import make_backend

        if args.backend == "acp":
            inner = make_backend("acp", agent=args.agent, cwd=args.cwd)
            label = f"acp:{args.agent}"
        elif args.backend == "claude":
            inner = make_backend(
                "claude", cwd=args.cwd, max_budget_usd=args.max_budget_usd
            )
            label = "claude"
        else:
            inner = make_backend(args.backend)
            label = args.backend

    return RecordingBackend(
        inner,
        out=args.out,
        cwd=args.cwd,
        provenance={
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "backend": label,
            # Which producer's event vocabulary this recording describes. The
            # refresh loop's whole premise is re-recording after an upstream
            # bump and diffing, which needs to know what it is diffing against.
            "a2acode": _a2acode_version(),
        },
    )


def _a2acode_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("a2acode")
    except PackageNotFoundError:  # a bare checkout on sys.path
        return "unknown"


def _check_out(path: str, parser) -> None:
    """`--out` is a staging path, and that is enforced.

    A raw recording carries unscrubbed absolute paths and possibly a shadowing
    match, so landing it live means the next rig-serve boot fails on a file
    nobody has read yet. Promotion is a deliberate `mv` after the scrub; a flag
    that could skip it is a flag that will.
    """
    if SCENARIOS_DIR in Path(path).parts:
        parser.error(
            f"--out {path} is inside a {SCENARIOS_DIR}/ directory. Record to a "
            f"staging path, read the file, then move it in — an unscrubbed "
            f"recording landing live can fail the repo at boot"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-record", description="Serve a real agent and record it as a scenario."
    )
    parser.add_argument("--backend", default="acp", help="acp, claude, echo, or playback.")
    parser.add_argument("--agent", default="claude", help="ACP agent the acp backend fronts.")
    parser.add_argument("--cwd", default=".", help="Project directory the agent works in.")
    parser.add_argument("--repo", help="Repo directory, required when --backend playback.")
    parser.add_argument("--out", help="Where to write the scenario file (a staging path).")
    parser.add_argument("--max-budget-usd", type=float, default=None,
                        help="Cost ceiling per run. Honored by --backend claude only; "
                             "ACPBackend takes no ceiling.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9300)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    # Argument-validation errors: parser.error() prints and raises SystemExit(2).
    # Let it propagate, same contract argparse itself uses everywhere else.
    if not args.out:
        parser.error("--out is required: recording with nowhere to write is a no-op")
    _check_out(args.out, parser)
    if args.backend == "playback" and not args.repo:
        parser.error("--backend playback needs a --repo to play")
    if args.max_budget_usd is not None and args.backend != "claude":
        # ACPBackend (and anything else make_backend() hands back) takes no
        # budget kwarg at all — passing --max-budget-usd would silently do
        # nothing, and the operator would find out only after the run
        # already spent without limit.
        parser.error(
            f"--max-budget-usd is only honored by --backend claude; "
            f"--backend {args.backend} has no cost ceiling to set"
        )

    url = f"http://{args.host}:{args.port}/"
    try:
        backend = build_recording_backend(args)
    except (RepoError, ScenarioError, ValueError) as exc:
        # Config problems are user errors, not crashes: say what is wrong and
        # exit, rather than burying it in a traceback. Distinct from the
        # parser.error() cases above — these are caught and reported, not
        # raised, so a bad --repo or unknown --backend cannot start a server.
        print(f"rig-record: {exc}", file=sys.stderr)
        return 2

    app = build_app(backend, url=url)
    print(f"rig-record: backend={args.backend} out={args.out} card={url}", flush=True)
    if args.backend not in ("claude", "playback"):
        # The only backend with a cost ceiling is `claude` (`--max-budget-usd`
        # above); `playback` spends nothing at all, so it needs no warning.
        # Everything else — `acp` foremost, since that's what the paid run
        # uses — starts fine and spends without limit.
        label = getattr(backend, "_provenance", {}).get("backend", args.backend)
        print(
            f"rig-record: backend={label} — no cost ceiling on this backend; "
            f"watch cost_usd in each result",
            flush=True,
        )
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
