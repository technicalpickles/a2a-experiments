"""A deterministic A2A dev rig: drive a2acode over the real protocol from pytest."""

from .events import Capture, send, state_name
from .server import serve

__all__ = ["Capture", "send", "serve", "state_name"]
