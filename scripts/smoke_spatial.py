#!/usr/bin/env python3
"""Smoke test — spatial layer.

Checks:
1. Validate config/worlds.json schema
2. Report which ports have a marble_url filled in
3. If WORLDLABS_API_KEY is set: auth check only (does NOT generate a world)

Run: uv run python scripts/smoke_spatial.py
Always runs (no skip condition).
"""

import json
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

WORLDS_PATH = Path(__file__).parent.parent / "config" / "worlds.json"
WL_KEY = os.environ.get("WORLDLABS_API_KEY", "")
FEATURE_API = os.environ.get("FEATURE_WORLDLABS_API", "false").lower() == "true"

REQUIRED_FIELDS = {"id", "port", "scope", "title", "marble_url", "source"}


def main() -> None:
    # 1. Validate worlds.json
    console.print("\n[bold cyan]1. Validate config/worlds.json[/bold cyan]")
    if not WORLDS_PATH.exists():
        console.print(f"[red]FAIL:[/red] {WORLDS_PATH} not found.")
        sys.exit(1)

    with open(WORLDS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    worlds = data.get("worlds", [])
    console.print(f"[green]OK:[/green] {len(worlds)} world entries found.")

    errors = []
    for i, w in enumerate(worlds):
        missing = REQUIRED_FIELDS - set(w.keys())
        if missing:
            errors.append(f"Entry {i} missing fields: {missing}")
    if errors:
        for e in errors:
            console.print(f"[red]SCHEMA ERROR:[/red] {e}")
        sys.exit(1)
    console.print("[green]OK:[/green] Schema valid.")

    # 2. Report marble_url status
    console.print("\n[bold cyan]2. World link status[/bold cyan]")
    table = Table("Port", "Scope", "Title", "marble_url", title="Worlds registry")
    for w in worlds:
        url = w.get("marble_url", "")
        status = f"[green]{url[:50]}[/green]" if url else "[red]EMPTY — W1 pending[/red]"
        table.add_row(w["port"], w["scope"], w["title"][:40], status)
    console.print(table)

    filled = sum(1 for w in worlds if w.get("marble_url"))
    console.print(
        f"{'[green]' if filled > 0 else '[yellow]'}"
        f"{filled}/{len(worlds)} world(s) have a marble_url.[/{'green]' if filled > 0 else 'yellow]'}"
    )
    if filled == 0:
        console.print("[yellow]NOTE:[/yellow] W1 not yet complete — teammate needs to generate worlds in Marble.")

    # 3. World API auth check (if key present)
    if WL_KEY and FEATURE_API:
        console.print("\n[bold cyan]3. World Labs API auth check[/bold cyan]")
        import httpx
        try:
            r = httpx.get(
                "https://api.worldlabs.ai/marble/v1/worlds",
                headers={"WLT-Api-Key": WL_KEY},
                timeout=10,
            )
            if r.status_code in (200, 404):
                console.print(f"[green]OK:[/green] World API reachable (status {r.status_code}).")
            else:
                console.print(f"[yellow]NOTE:[/yellow] World API returned {r.status_code} — check key.")
        except Exception as exc:
            console.print(f"[yellow]NOTE:[/yellow] World API unreachable: {exc}")
    elif WL_KEY:
        console.print("\n[yellow]NOTE:[/yellow] WORLDLABS_API_KEY is set but FEATURE_WORLDLABS_API=false — skipping W4 auth check.")
    else:
        console.print("\n[yellow]SKIP:[/yellow] WORLDLABS_API_KEY not set — W4 off.")

    console.print("\n[bold green]Spatial smoke PASSED[/bold green]")


if __name__ == "__main__":
    main()
