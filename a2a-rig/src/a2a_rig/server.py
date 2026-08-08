"""Launch a server subprocess for tests to drive.

Two routes, both ending at a2acode's real server. a2acode's own backends
(``echo``, ``claude``, ``acp``) are launched through its CLI in its own
checkout, via ``uv run --project`` — the harness does not import a2acode to
run those, so it drives them exactly the way a real client would: over the
wire, no in-process shortcuts. ``playback`` is ours, so it goes through
``rig-serve``, which injects our backend into a2acode's ``build_app()``.

(The ``a2a_playback`` package *does* import a2acode — it has to, to implement
its Backend protocol. That is a different thing from the harness importing it
to take shortcuts around the network.)
"""

from __future__ import annotations

import os
import socket
import sys
import subprocess
import time
from contextlib import closing, contextmanager
from pathlib import Path

import httpx

DEFAULT_A2ACODE_PROJECT = Path.home() / "github.com" / "kanywst" / "a2acode"

# Generous: a cold `uv run` may resolve/build before the server binds.
STARTUP_TIMEOUT_S = 60.0


def a2acode_command() -> list[str]:
    """How to invoke the a2acode CLI.

    ``A2ACODE_CMD`` overrides wholesale (shell-style, split on spaces);
    ``A2ACODE_PROJECT`` just relocates the checkout.
    """
    override = os.environ.get("A2ACODE_CMD")
    if override:
        return override.split()
    project = os.environ.get("A2ACODE_PROJECT", str(DEFAULT_A2ACODE_PROJECT))
    return ["uv", "run", "--project", project, "a2acode"]


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_serving(
    url: str, proc: subprocess.Popen, deadline: float, ready_path: str = "/.well-known/agent-card.json"
) -> None:
    """Poll `ready_path` until it 200s.

    Defaults to the agent card, which is what a single-repo app serves at its
    root. A mounted rig has no card at its root by design (see
    `build_rig_app`), so callers serving `repos` pass `ready_path="/"` to poll
    the index document instead.
    """
    ready_url = f"{url.rstrip('/')}{ready_path}"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"a2acode exited with code {proc.returncode} before serving.\n"
                f"{_drain(proc)}"
            )
        try:
            if httpx.get(ready_url, timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError as exc:  # not up yet
            last_error = exc
        time.sleep(0.1)
    proc.terminate()
    raise TimeoutError(f"a2acode did not serve {ready_url} in time: {last_error}")


def _drain(proc: subprocess.Popen) -> str:
    if proc.stdout is None:
        return ""
    try:
        return proc.stdout.read() or ""
    except Exception:
        return ""


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = REPO_ROOT / "repos" / "billing-api"


def _playback_command(
    port: int, repo: str | Path | None, repos: str | Path | None
) -> list[str]:
    """`playback` is ours, so it is served by rig-serve, not the a2acode CLI.

    Uses the running interpreter rather than a console script so the harness
    works from a bare checkout without an install step.
    """
    if repo and repos:
        # rig-serve's own CLI treats this combination as an error (`parser.error`
        # in a2a_playback.serve.main); picking `repos` silently here would mean
        # the same both-passed call decided differently depending on which
        # entry point ran it.
        raise ValueError("pass at most one of repo or repos, not both")
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


def _record_command(port: int, repo: str | Path | None, out: str | Path) -> list[str]:
    """rig-record, launched the same way rig-serve is: module, not console script,
    so the harness works from a bare checkout without an install step."""
    return [
        sys.executable, "-m", "a2a_playback.record",
        "--backend", "playback",
        "--repo", str(repo or DEFAULT_REPO),
        "--out", str(out),
        "--host", "127.0.0.1",
        "--port", str(port),
    ]


@contextmanager
def serve(
    backend: str = "echo",
    cwd: str | Path | None = None,
    extra_args: list[str] | None = None,
    port: int | None = None,
    repo: str | Path | None = None,
    repos: str | Path | None = None,
    env: dict[str, str] | None = None,
    record_out: str | Path | None = None,
):
    """Run a server for the duration of the block, yielding its base URL.

    `echo`, `claude`, and `acp` go through a2acode's own CLI; `playback` goes
    through rig-serve, which injects our backend into a2acode's `build_app()`.
    `record_out` launches `rig-record` instead, wrapping the `playback` backend
    so a round-trip test can drive a real A2A client against it and get a
    scenario file back out. Either way what comes up is a2acode's real server.
    """
    port = port or free_port()
    url = f"http://127.0.0.1:{port}/"
    if record_out is not None:
        cmd = _record_command(port, repo, record_out)
    elif backend == "playback":
        cmd = _playback_command(port, repo, repos)
    else:
        cmd = [
            *a2acode_command(),
            "serve",
            "--backend",
            backend,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        if cwd is not None:
            cmd += ["--cwd", str(cwd)]
    cmd += extra_args or []

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        # Overlaid rather than replaced: the child still needs PATH, HOME, and
        # whatever `uv run` reads to resolve the checkout.
        env={**os.environ, **env} if env else None,
    )
    ready_path = "/" if repos else "/.well-known/agent-card.json"
    try:
        _wait_until_serving(url, proc, time.monotonic() + STARTUP_TIMEOUT_S, ready_path)
        yield url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
