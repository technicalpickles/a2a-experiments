"""The index provider, against a live rig."""

from __future__ import annotations

import pytest

from a2a_orchestrator.catalog import Catalog


async def test_repos_lists_the_rig_index(rig_url, http):
    catalog = Catalog(index_url=rig_url)

    entries = await catalog.repos(http)

    names = {entry.name for entry in entries}
    assert {"billing-api", "checkout-web", "infra-terraform"} <= names


async def test_resolve_returns_a_servable_base_url(rig_url, http):
    catalog = Catalog(index_url=rig_url)

    entry = await catalog.resolve(http, "billing-api")

    assert entry.base_url.endswith("/repos/billing-api/")
    card = await http.get(f"{entry.base_url}.well-known/agent-card.json")
    assert card.status_code == 200


async def test_resolve_unknown_repo_raises_lookup_error(rig_url, http):
    catalog = Catalog(index_url=rig_url)

    with pytest.raises(LookupError, match="no-such-repo"):
        await catalog.resolve(http, "no-such-repo")


def test_load_parses_catalog_yaml(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("provider: index\nurl: http://127.0.0.1:9200/\n")

    catalog = Catalog.load(path)

    assert catalog.index_url == "http://127.0.0.1:9200/"


def test_load_rejects_unknown_providers(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("provider: spawn\nurl: http://example/\n")

    with pytest.raises(ValueError, match="spawn"):
        Catalog.load(path)


STATIC_YAML = """\
provider: static
repos:
  - name: demo-app
    description: A real a2acode serving ~/scratch/demo-app
    card_url: http://127.0.0.1:9400/.well-known/agent-card.json
"""


async def test_static_catalog_lists_its_inline_repos(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(STATIC_YAML)

    catalog = Catalog.load(path)
    entries = await catalog.repos(http=None)

    assert [entry.name for entry in entries] == ["demo-app"]
    assert entries[0].base_url == "http://127.0.0.1:9400/"


async def test_static_catalog_resolves_without_http(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(STATIC_YAML)

    catalog = Catalog.load(path)
    entry = await catalog.resolve(http=None, name="demo-app")

    assert entry.card_url == "http://127.0.0.1:9400/.well-known/agent-card.json"

    with pytest.raises(LookupError, match="no-such-repo"):
        await catalog.resolve(http=None, name="no-such-repo")
