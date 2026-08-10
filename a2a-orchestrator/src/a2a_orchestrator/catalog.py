"""The catalog: which repositories the cockpit can reach.

The index provider points at an already-running index (the rig's ``GET /``
today, any a2acode index tomorrow) and resolves a repo name to the base URL
its agent serves at. The static provider inlines the same entries in
catalog.yaml for agents that serve a card but no index (a lone a2acode).
The spawn provider — launching a2acode per worktree — belongs to the
real-agents milestone, not here; ``load`` rejects anything else so a future
config typo fails loudly instead of half-working.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

CARD_SUFFIX = ".well-known/agent-card.json"


@dataclass
class RepoEntry:
    name: str
    description: str
    card_url: str

    @property
    def base_url(self) -> str:
        return self.card_url.removesuffix(CARD_SUFFIX)


class Catalog:
    def __init__(
        self,
        index_url: str | None = None,
        entries: list[RepoEntry] | None = None,
    ):
        self.index_url = index_url
        self.entries = entries

    @classmethod
    def load(cls, path: str | Path) -> Catalog:
        config = yaml.safe_load(Path(path).read_text())
        provider = config.get("provider")
        if provider == "index":
            return cls(index_url=config["url"])
        if provider == "static":
            return cls(
                entries=[
                    RepoEntry(entry["name"], entry["description"], entry["card_url"])
                    for entry in config["repos"]
                ]
            )
        raise ValueError(f"unknown catalog provider {provider!r}")

    async def repos(self, http: httpx.AsyncClient) -> list[RepoEntry]:
        if self.entries is not None:
            return self.entries
        response = await http.get(self.index_url)
        response.raise_for_status()
        return [
            RepoEntry(entry["name"], entry["description"], entry["card_url"])
            for entry in response.json()["repos"]
        ]

    async def resolve(self, http: httpx.AsyncClient, name: str) -> RepoEntry:
        entries = await self.repos(http)
        for entry in entries:
            if entry.name == name:
                return entry
        raise LookupError(f"no repo named {name!r} in the catalog")
