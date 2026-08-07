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


def _wait_until_serving(url: str, proc: subprocess.Popen, deadline: float) -> None:
    card_url = f"{url.rstrip('/')}/.well-known/agent-card.json"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"a2acode exited with code {proc.returncode} before serving.\n"
                f"{_drain(proc)}"
            )
        try:
            if httpx.get(card_url, timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError as exc:  # not up yet
            last_error = exc
        time.sleep(0.1)
    proc.terminate()
    raise TimeoutError(f"a2acode did not serve {card_url} in time: {last_error}")


def _drain(proc: subprocess.Popen) -> str:
    if proc.stdout is None:
        return ""
    try:
        return proc.stdout.read() or ""
    except Exception:
        return ""


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = REPO_ROOT / "scenarios" / "billing-api.yaml"


def _playback_command(port: int, scenario: str | Path | None) -> list[str]:
    """`playback` is ours, so it is served by rig-serve, not the a2acode CLI.

    Uses the running interpreter rather than a console script so the harness
    works from a bare checkout without an install step.
    """
    return [
        sys.executable,
        "-m",
        "a2a_playback.serve",
        "--scenario",
        str(scenario or DEFAULT_SCENARIO),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


@contextmanager
def serve(
    backend: str = "echo",
    cwd: str | Path | None = None,
    extra_args: list[str] | None = None,
    port: int | None = None,
    scenario: str | Path | None = None,
):
    """Run a server for the duration of the block, yielding its base URL.

    `echo`, `claude`, and `acp` go through a2acode's own CLI; `playback` goes
    through rig-serve, which injects our backend into a2acode's `build_app()`.
    Either way what comes up is a2acode's real server.
    """
    port = port or free_port()
    url = f"http://127.0.0.1:{port}/"
    if backend == "playback":
        cmd = _playback_command(port, scenario)
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
    )
    try:
        _wait_until_serving(url, proc, time.monotonic() + STARTUP_TIMEOUT_S)
        yield url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
