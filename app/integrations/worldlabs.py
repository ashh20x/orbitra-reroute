"""World Labs integration — W4 on-demand world generation.

Only active when FEATURE_WORLDLABS_API=true.
W2 cached lookup lives in app/spatial/lookup.py (no API call needed).
"""

from __future__ import annotations

import os
from typing import Any

_API_KEY = os.environ.get("WORLDLABS_API_KEY", "")
_GENERATE_URL = "https://api.worldlabs.ai/marble/v1/worlds:generate"
_OPERATIONS_BASE = "https://api.worldlabs.ai/marble/v1/operations"
_WORLDS_BASE = "https://api.worldlabs.ai/marble/v1/worlds"
_ENABLED = os.environ.get("FEATURE_WORLDLABS_API", "false").lower() == "true"


async def generate_world(
    port: str, prompt: str, input_image_urls: list[str] | None = None
) -> str:
    """Start async world generation. Returns operation_id.

    Never blocks the Trade Ops notification — call this as a fire-and-forget
    side branch and fall back to the cached world if it doesn't finish in time.
    """
    if not _ENABLED:
        raise RuntimeError("FEATURE_WORLDLABS_API is disabled. Enable to use W4.")
    if not _API_KEY:
        raise RuntimeError("WORLDLABS_API_KEY is not set.")
    raise NotImplementedError(
        "TODO W4: POST to /marble/v1/worlds:generate with WLT-Api-Key header. "
        "Return operation_id for polling."
    )


async def poll_operation(operation_id: str) -> dict[str, Any]:
    """Poll GET /marble/v1/operations/{operation_id} until done or failed."""
    raise NotImplementedError("TODO W4: poll loop.")


async def get_world(world_id: str) -> dict[str, Any]:
    """GET /marble/v1/worlds/{world_id} — returns world_marble_url + thumbnail."""
    raise NotImplementedError("TODO W4: fetch world details.")
