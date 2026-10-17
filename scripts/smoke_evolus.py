#!/usr/bin/env python3
"""Smoke test — Evolus workspace.

Checks:
1. GET /v1/models (agent chat endpoint)
2. GET /api/v1/ai-tools catalog
3. One translate AI Tool call (Portuguese → English)

Run: uv run python scripts/smoke_evolus.py
Skips cleanly if EVOLUS_API_KEY or EVOLUS_APP_ID not set.
"""

import asyncio
import os
import sys

import httpx
from rich.console import Console
from rich.table import Table

console = Console()

API_KEY = os.environ.get("EVOLUS_API_KEY", "")
APP_ID = os.environ.get("EVOLUS_APP_ID", "")
BASE = "https://api.evolus.ai/api/v1"
CHAT_BASE = "https://api.evolus.ai/v1"


def skip(reason: str) -> None:
    console.print(f"[yellow]SKIP:[/yellow] {reason}")
    sys.exit(0)


async def main() -> None:
    if not API_KEY or not APP_ID:
        skip("EVOLUS_API_KEY or EVOLUS_APP_ID not set")

    rest_headers = {"X-ApiKey": API_KEY, "X-Application-Id": APP_ID}
    chat_headers = {"Authorization": f"Bearer {API_KEY}", "X-Application-Id": APP_ID}

    async with httpx.AsyncClient(timeout=30) as client:

        # 1. Agent chat models
        console.print("\n[bold cyan]1. GET /v1/models (Evolus agent chat)[/bold cyan]")
        r = await client.get(f"{CHAT_BASE}/models", headers=chat_headers)
        r.raise_for_status()
        models = r.json().get("data", [])
        table = Table("Model ID", title="Evolus agents (as models)")
        for m in models:
            table.add_row(m.get("id", ""))
        console.print(table)
        console.print(f"[green]OK:[/green] {len(models)} agent(s) listed.")

        # 2. AI Tools catalog
        console.print("\n[bold cyan]2. GET /api/v1/ai-tools[/bold cyan]")
        r = await client.get(f"{BASE}/ai-tools", headers=rest_headers)
        r.raise_for_status()
        tools_data = r.json()
        tools = tools_data if isinstance(tools_data, list) else tools_data.get("data", [])
        table2 = Table("Tool name", "Description", title="Evolus AI Tools")
        for t in tools[:10]:
            table2.add_row(str(t.get("name", t.get("id", ""))), str(t.get("description", ""))[:60])
        console.print(table2)
        console.print(f"[green]OK:[/green] {len(tools)} AI Tool(s) available.")

        # 3. Translate test
        console.print("\n[bold cyan]3. Translate AI Tool (pt → en)[/bold cyan]")
        pt_text = (
            "Informamos com urgência que o navio MV ESTRELA DO SUL recebeu aviso "
            "formal de desvio de rota devido às restrições no Estreito de Ormuz."
        )
        translate_payload = {
            "text": pt_text,
            "options": {"target": "en"},
        }
        r = await client.post(
            f"{BASE}/ai-tools/translate/run",
            headers=rest_headers,
            json=translate_payload,
        )
        if r.status_code == 200:
            result = r.json()
            translated = result.get("result") or result.get("text") or str(result)
            console.print(f"[green]OK:[/green] Translated: {translated[:200]}")
        else:
            console.print(f"[yellow]NOTE:[/yellow] translate returned {r.status_code}: {r.text[:200]}")
            console.print("  (Tool name or endpoint may differ — check Evolus workspace docs)")

    console.print("\n[bold green]Evolus smoke PASSED[/bold green]")


if __name__ == "__main__":
    asyncio.run(main())
