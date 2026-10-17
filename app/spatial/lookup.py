"""W2/W3 spatial asset lookup — reads config/worlds.json.

No API calls. Returns SpatialAsset or None.
The Trade Ops notification never blocks on this.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from app.domain.cdm import SpatialAsset, SpatialSource

_WORLDS_PATH = pathlib.Path(__file__).parent.parent.parent / "config" / "worlds.json"
_LABEL = "AI-generated spatial approximation — verify with provider / port authority."

_cache: list[dict[str, Any]] | None = None


def _load() -> list[dict[str, Any]]:
    global _cache
    if _cache is None:
        if _WORLDS_PATH.exists():
            with open(_WORLDS_PATH, encoding="utf-8") as f:
                _cache = json.load(f).get("worlds", [])
        else:
            _cache = []
    return _cache


def get_for_port(port: str) -> SpatialAsset | None:
    """Return the cached spatial asset for a port, or None."""
    port_lower = port.lower()
    for entry in _load():
        if entry.get("port", "").lower() == port_lower and entry.get("scope") == "port":
            marble_url = entry.get("marble_url", "")
            if not marble_url:
                return None
            return SpatialAsset(
                id=entry.get("id", ""),
                port=entry["port"],
                scope="port",
                title=entry.get("title", port),
                marble_url=marble_url,
                thumbnail_url=entry.get("thumbnail_url"),
                source=SpatialSource(entry.get("source", "marble_app")),
                prompt=entry.get("prompt"),
                created_at=None,
                link_verified_at=None,
                label=_LABEL,
            )
    return None


def get_for_provider(provider_ref: str) -> SpatialAsset | None:
    """Return the spatial asset for a specific provider (W3), or None."""
    for entry in _load():
        if entry.get("provider_ref") == provider_ref and entry.get("scope") == "provider":
            marble_url = entry.get("marble_url", "")
            if not marble_url:
                return None
            return SpatialAsset(
                id=entry.get("id", ""),
                port=entry.get("port", ""),
                scope="provider",
                provider_ref=provider_ref,
                title=entry.get("title", provider_ref),
                marble_url=marble_url,
                thumbnail_url=entry.get("thumbnail_url"),
                source=SpatialSource(entry.get("source", "marble_app")),
                label=_LABEL,
            )
    return None
