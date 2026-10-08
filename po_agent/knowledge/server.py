"""An MCP server (stdio) that serves the enterprise knowledge catalogue.

    python -m po_agent.knowledge.server

Tools return JSON lists. Filtering by capability ids is done here, server-side, as a real
enterprise knowledge server would. The data comes from data/catalogue.yaml; swap the file, or
the whole server, without touching the graph.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

DATA = Path(__file__).parent / "data" / "catalogue.yaml"
mcp = FastMCP("enterprise-knowledge")


def load_catalogue(path: Path = DATA) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _by_capability(items: list[dict], capability_ids: list[str] | None) -> list[dict]:
    if not capability_ids:
        return items
    wanted = set(capability_ids)
    return [i for i in items if wanted & set(i.get("capability_ids", []))]


@mcp.tool()
def get_capabilities() -> list[dict]:
    """The business capability catalogue: id, name, description, owner."""
    return load_catalogue()["capabilities"]


@mcp.tool()
def get_channels() -> list[dict]:
    """Customer and operational channels: id, name, description."""
    return load_catalogue()["channels"]


@mcp.tool()
def get_personas() -> list[dict]:
    """The canonical persona catalogue: id, name, role, description, goals, pain_points."""
    return load_catalogue()["personas"]


@mcp.tool()
def search_policies(capability_ids: list[str] | None = None) -> list[dict]:
    """Policies, regulations and constraints that touch the given capabilities (all if none given)."""
    return _by_capability(load_catalogue()["policies"], capability_ids)


@mcp.tool()
def get_existing_features(capability_ids: list[str] | None = None) -> list[dict]:
    """Existing products and features for the given capabilities (all if none given)."""
    return _by_capability(load_catalogue()["features"], capability_ids)


@mcp.tool()
def get_services(capability_ids: list[str] | None = None) -> list[dict]:
    """Architecture: services and systems behind the given capabilities (all if none given)."""
    return _by_capability(load_catalogue()["services"], capability_ids)


@mcp.tool()
def get_glossary() -> list[dict]:
    """Enterprise terminology: term, definition."""
    return load_catalogue()["glossary"]


TOOLS = {"get_capabilities": get_capabilities, "get_channels": get_channels, "get_personas": get_personas,
         "search_policies": search_policies, "get_existing_features": get_existing_features,
         "get_services": get_services, "get_glossary": get_glossary}

if __name__ == "__main__":
    mcp.run()
