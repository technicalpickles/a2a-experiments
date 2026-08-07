"""Scenario files: parse, validate, and select a play for an incoming message.

The format is DESIGN-v3 §4 — a list of *plays*, each with match rules and a list
of events written in a2acode's own ``BackendEvent`` vocabulary. Keeping the
vocabulary identical is what will let recorded scenarios (M3) and hand-written
ones be the same thing.

Validation is deliberately strict and up-front. A scenario with a typo'd event
name should fail when the server starts, not halfway through a turn that a
frontend is watching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Event names a play may contain. Each maps onto a BackendEvent in backend.py;
# `permission` is special — it parks the turn and branches.
EVENT_NAMES = {
    "text",
    "thought",
    "tool_use",
    "tool_result",
    "file_change",
    "plan",
    "notice",
    "permission",
    "error",
    "result",
}

MATCH_KEYS = {"turn", "contains", "regex"}


class ScenarioError(Exception):
    """A scenario file is malformed, or no play matched a message."""


@dataclass(frozen=True)
class Match:
    turn: int | None = None
    contains: str | None = None
    regex: str | None = None

    def matches(self, prompt: str, turn: int) -> bool:
        if self.turn is not None and self.turn != turn:
            return False
        if self.contains is not None and self.contains.lower() not in prompt.lower():
            return False
        if self.regex is not None and not re.search(self.regex, prompt):
            return False
        return True

    @property
    def is_default(self) -> bool:
        return self.turn is None and self.contains is None and self.regex is None


@dataclass(frozen=True)
class Play:
    match: Match
    events: list[dict[str, Any]]
    index: int = 0

    def describe(self) -> str:
        bits = []
        if self.match.turn is not None:
            bits.append(f"turn={self.match.turn}")
        if self.match.contains is not None:
            bits.append(f"contains={self.match.contains!r}")
        if self.match.regex is not None:
            bits.append(f"regex={self.match.regex!r}")
        return f"play #{self.index} ({', '.join(bits) or 'default'})"


@dataclass
class Scenario:
    name: str
    plays: list[Play]
    card_name: str | None = None
    card_description: str | None = None
    defaults: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @property
    def default_delay_ms(self) -> float:
        return float(self.defaults.get("delay_ms", 0) or 0)

    def select(self, prompt: str, turn: int) -> Play:
        """First match wins. No match is an error, never a plausible answer."""
        for play in self.plays:
            if play.match.matches(prompt, turn):
                return play
        raise ScenarioError(
            f"scenario {self.name!r}: no play matched turn {turn} of {prompt!r}. "
            f"Add a matching play, or a `- match: {{}}` default if you want a "
            f"catch-all. Refusing to guess."
        )


def load(path: str | Path) -> Scenario:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path}: expected a mapping at the top level")
    return parse(raw, path=path)


def parse(raw: dict[str, Any], *, path: Path | None = None) -> Scenario:
    where = str(path) if path else "<scenario>"

    name = raw.get("name")
    if not name:
        raise ScenarioError(f"{where}: scenario needs a `name`")

    raw_plays = raw.get("plays")
    if not isinstance(raw_plays, list) or not raw_plays:
        raise ScenarioError(f"{where}: scenario needs a non-empty `plays` list")

    plays = [_parse_play(p, i, where) for i, p in enumerate(raw_plays, start=1)]

    # A default play after which nothing can ever match is a scenario bug worth
    # naming, since the shadowed plays would silently never run.
    for play in plays[:-1]:
        if play.match.is_default:
            raise ScenarioError(
                f"{where}: {play.describe()} is a catch-all but is not last; "
                f"every play after it is unreachable"
            )

    card = raw.get("card") or {}
    return Scenario(
        name=str(name),
        plays=plays,
        card_name=card.get("name"),
        card_description=card.get("description"),
        defaults=raw.get("defaults") or {},
        path=path,
    )


def _parse_play(raw: Any, index: int, where: str) -> Play:
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: play #{index} should be a mapping")

    raw_match = raw.get("match", {})
    if raw_match is None:
        raw_match = {}
    if not isinstance(raw_match, dict):
        raise ScenarioError(f"{where}: play #{index} `match` should be a mapping")
    unknown = set(raw_match) - MATCH_KEYS
    if unknown:
        raise ScenarioError(
            f"{where}: play #{index} has unknown match keys {sorted(unknown)}; "
            f"expected any of {sorted(MATCH_KEYS)}"
        )

    events = raw.get("events")
    if not isinstance(events, list) or not events:
        raise ScenarioError(f"{where}: play #{index} needs a non-empty `events` list")
    for event in events:
        _validate_event(event, index, where)

    return Play(
        match=Match(
            turn=raw_match.get("turn"),
            contains=raw_match.get("contains"),
            regex=raw_match.get("regex"),
        ),
        events=events,
        index=index,
    )


def message_of(body: Any) -> str:
    """`- error: "boom"` and `- error: {message: "boom"}` should both work."""
    if isinstance(body, str):
        return body.strip()
    if isinstance(body, dict):
        return str(body.get("message") or body.get("text") or "").strip()
    return ""


def _validate_event(event: Any, play_index: int, where: str) -> None:
    if not isinstance(event, dict) or len(event) != 1:
        raise ScenarioError(
            f"{where}: play #{play_index} has an event that is not a single-key "
            f"mapping: {event!r}"
        )
    (kind, body), = event.items()
    if kind not in EVENT_NAMES:
        raise ScenarioError(
            f"{where}: play #{play_index} has unknown event {kind!r}; "
            f"expected one of {sorted(EVENT_NAMES)}"
        )
    if kind == "error" and not message_of(body):
        raise ScenarioError(
            f"{where}: play #{play_index} `error` needs a message saying how the "
            f"run failed"
        )
    if kind == "permission":
        if not isinstance(body, dict):
            raise ScenarioError(
                f"{where}: play #{play_index} `permission` should be a mapping"
            )
        if not body.get("tool"):
            raise ScenarioError(
                f"{where}: play #{play_index} `permission` needs a `tool`"
            )
        if body.get("on_timeout") and body.get("timeout_ms") is None:
            raise ScenarioError(
                f"{where}: play #{play_index} `permission` has an `on_timeout` "
                f"branch but no `timeout_ms` to reach it; nothing would ever run it"
            )
        for branch in ("on_allow", "on_deny", "on_timeout"):
            for nested in body.get(branch) or []:
                _validate_event(nested, play_index, where)
