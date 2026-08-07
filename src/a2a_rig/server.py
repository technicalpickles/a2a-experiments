"""Launch an ``a2acode serve`` subprocess for tests to drive.

a2acode lives in its own checkout with its own (3.14) virtualenv, so the rig
does not import it — it shells out via ``uv run --project``. That keeps the two
dependency trees independent and means the harness drives a2acode exactly the
way a real client would: over the wire, no in-process shortcuts.
"""

from __future__ import annotations

import os
import socket
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


@contextmanager
def serve(
    backend: str = "echo",
    cwd: str | Path | None = None,
    extra_args: list[str] | None = None,
    port: int | None = None,
):
    """Run ``a2acode serve`` for the duration of the block, yielding its base URL."""
    port = port or free_port()
    url = f"http://127.0.0.1:{port}/"
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
