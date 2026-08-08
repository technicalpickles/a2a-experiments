"""Scenario files: parse and validate one document of plays.

A scenario is a *script*, and only a script — a list of **plays**, each with
match rules and a list of events written in a2acode's own ``BackendEvent``
vocabulary. Who the agent running it is, and how it is paced, live in
``repo.py``: a repo has scenarios, it is not one. Keeping the vocabulary
identical to a2acode's is what will let recorded scenarios (M3) and
hand-written ones be the same format.

The top level is a mapping rather than a bare list of plays for exactly this
reason: it leaves room for a ``recorded`` block (when it was captured, the
prompt that produced it, which backend) without another format change. A
scenario file may carry ``plays`` and, optionally, ``recorded`` — nothing
else.

Validation is deliberately strict and up-front. A scenario with a typo'd event
name should fail when the server starts, not halfway through a turn that a
frontend is watching. The one rule that is *not* here is
catch-all-must-be-last: that is a property of a repo's whole concatenated play
list, so it lives in ``repo.py``.
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
    """One document of plays, and where it came from."""

    plays: list[Play]
    path: Path | None = None
    recorded: dict[str, Any] = field(default_factory=dict)


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path}: expected a mapping at the top level")
    return parse_scenario(raw, path=path)


def parse_scenario(raw: dict[str, Any], *, path: Path | None = None) -> Scenario:
    where = str(path) if path else "<scenario>"

    raw_plays = raw.get("plays")
    if not isinstance(raw_plays, list) or not raw_plays:
        raise ScenarioError(f"{where}: scenario needs a non-empty `plays` list")

    unknown = set(raw) - {"plays", "recorded"}
    if unknown:
        raise ScenarioError(
            f"{where}: scenario has unexpected keys {sorted(unknown)}; a scenario "
            f"holds `plays` and, optionally, `recorded` provenance — everything "
            f"else, like identity and defaults, belongs in repo.yaml"
        )

    recorded = raw.get("recorded", {})
    if recorded is None:
        recorded = {}
    if not isinstance(recorded, dict):
        raise ScenarioError(f"{where}: scenario `recorded` should be a mapping")

    return Scenario(
        plays=[_parse_play(p, i, where) for i, p in enumerate(raw_plays, start=1)],
        path=path,
        recorded=recorded,
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


def _validate_plan(body: Any, play_index: int, where: str) -> None:
    """A plan is one of three things, or nothing at all.

    An empty plan is legitimate — it is how an agent says it abandoned the
    checklist. Two of them at once is not: a2acode's renderer prefers markdown,
    then uri, and drops whatever else was set without a word, so a scenario
    written with both would ship a plan its author never actually wrote.
    """
    if body is None:
        return
    if not isinstance(body, dict):
        raise ScenarioError(f"{where}: play #{play_index} `plan` should be a mapping")

    set_fields = [f for f in ("steps", "markdown", "uri") if body.get(f)]
    if len(set_fields) > 1:
        raise ScenarioError(
            f"{where}: play #{play_index} `plan` sets {sorted(set_fields)}; a plan "
            f"is one of `steps`, `markdown`, or `uri`. a2acode renders the first "
            f"of those it finds and discards the rest"
        )

    steps = body.get("steps") or []
    if not isinstance(steps, list):
        raise ScenarioError(f"{where}: play #{play_index} `plan.steps` should be a list")
    for position, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or not step.get("content"):
            raise ScenarioError(
                f"{where}: play #{play_index} `plan` step #{position} needs "
                f"`content` — the line a reader actually sees"
            )


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
    if kind == "plan":
        _validate_plan(body, play_index, where)
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
        if not any(body.get(b) for b in ("on_allow", "on_deny", "on_timeout")):
            raise ScenarioError(
                f"{where}: play #{play_index} `permission` has no branches — it "
                f"needs at least one branch: `on_allow`, `on_deny`, or `on_timeout`. "
                f"A gate that does nothing on every answer cannot be what was meant"
            )
        for branch in ("on_allow", "on_deny", "on_timeout"):
            for nested in body.get(branch) or []:
                _validate_event(nested, play_index, where)
