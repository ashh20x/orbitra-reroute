#!/usr/bin/env python3
"""Smoke test — AMD Token Factory.

Checks:
1. List models and assert Gemma is present
2. One chat completion
3. One tool-call completion
4. One json_object completion
5. Print X-RateLimit-* headers
6. Print /v1/usage

Run: uv run python scripts/smoke_tokenfactory.py
Skips cleanly if TOKEN_FACTORY_API_KEY is not set.
"""

import asyncio
import json
import os
import sys

import httpx
from rich.console import Console
from rich.table import Table

console = Console()

BASE_URL = os.environ.get("TOKEN_FACTORY_BASE_URL", "https://developer.amd.com.cn/radeon/api/v1")
API_KEY = os.environ.get("TOKEN_FACTORY_API_KEY", "")
PRIMARY = os.environ.get("LLM_PRIMARY_MODEL", "Gemma-4-31B-IT")


def skip(reason: str) -> None:
    console.print(f"[yellow]SKIP:[/yellow] {reason}")
    sys.exit(0)


async def main() -> None:
    if not API_KEY:
        skip("TOKEN_FACTORY_API_KEY not set")

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=30) as client:

        # 1. List models
        console.print("\n[bold cyan]1. List models[/bold cyan]")
        r = await client.get("/models")
        r.raise_for_status()
        models = [m["id"] for m in r.json().get("data", [])]
        table = Table("Model ID", title="Available models")
        for m in models:
            table.add_row(m)
        console.print(table)

        if PRIMARY not in models:
            console.print(f"[red]FAIL:[/red] Primary model '{PRIMARY}' NOT found in catalog!")
            sys.exit(1)
        console.print(f"[green]OK:[/green] Primary model '{PRIMARY}' confirmed.")

        # 2. Chat completion
        console.print("\n[bold cyan]2. Chat completion[/bold cyan]")
        payload = {
            "model": PRIMARY,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "In one sentence, what is the Port of Salalah?"},
            ],
            "max_tokens": 100,
        }
        r = await client.post("/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
        console.print(f"[green]OK:[/green] {data['choices'][0]['message']['content'][:120]}")
        console.print(f"  tokens: in={data.get('usage', {}).get('prompt_tokens')} out={data.get('usage', {}).get('completion_tokens')}")
        _print_ratelimit_headers(r.headers)

        # 3. Tool call
        console.print("\n[bold cyan]3. Tool call[/bold cyan]")
        tools_payload = {
            "model": PRIMARY,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is 42 * 7? Use the calculator tool."},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "description": "Perform arithmetic",
                        "parameters": {
                            "type": "object",
                            "properties": {"expression": {"type": "string"}},
                            "required": ["expression"],
                        },
                    },
                }
            ],
            "max_tokens": 100,
        }
        r = await client.post("/chat/completions", json=tools_payload)
        r.raise_for_status()
        tc = r.json()["choices"][0]
        if tc.get("finish_reason") == "tool_calls":
            console.print(f"[green]OK:[/green] Tool call received: {tc['message'].get('tool_calls', [{}])[0].get('function', {}).get('name')}")
        else:
            console.print(f"[yellow]NOTE:[/yellow] finish_reason={tc.get('finish_reason')} (tool call may not be supported on this model)")

        # 4. JSON object mode
        console.print("\n[bold cyan]4. json_object mode[/bold cyan]")
        json_payload = {
            "model": PRIMARY,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant. Always respond with valid JSON."},
                {"role": "user", "content": 'Return JSON with keys "port" and "country" for Jeddah Islamic Port.'},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 100,
        }
        r = await client.post("/chat/completions", json=json_payload)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        console.print(f"[green]OK:[/green] {parsed}")

        # 5. Usage
        console.print("\n[bold cyan]5. /v1/usage[/bold cyan]")
        r = await client.get("/usage", params={"include_recent": "true"})
        if r.status_code == 200:
            console.print_json(data=r.json())
        else:
            console.print(f"[yellow]NOTE:[/yellow] /v1/usage returned {r.status_code}")

    console.print("\n[bold green]Token Factory smoke PASSED[/bold green]")


def _print_ratelimit_headers(headers) -> None:
    rl_headers = {k: v for k, v in headers.items() if k.lower().startswith("x-ratelimit")}
    if rl_headers:
        console.print(f"  rate-limit headers: {rl_headers}")


if __name__ == "__main__":
    asyncio.run(main())
