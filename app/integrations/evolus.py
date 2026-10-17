"""Evolus integration — workflow execute, extractor, AI Tools, agent chat."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

_BASE      = "https://api.evolus.ai/api/v1"
_CHAT_BASE = "https://api.evolus.ai/v1"
_API_KEY   = os.environ.get("EVOLUS_API_KEY", "")
_APP_ID    = os.environ.get("EVOLUS_APP_ID", "")


def _headers() -> dict[str, str]:
    return {"X-ApiKey": _API_KEY, "X-Application-Id": _APP_ID}


def _chat_headers(conversation_id: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {_API_KEY}", "X-Application-Id": _APP_ID}
    if conversation_id:
        h["X-Conversation-Id"] = conversation_id
    return h


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

async def execute_workflow(
    workflow_id: str,
    input_text: str,
    callback_url: str | None = None,
    files: list[tuple[str, bytes, str]] | None = None,
) -> dict[str, Any]:
    """POST /workflows/{id}/execute → {executionId, ...}"""
    data: dict[str, Any] = {"text": input_text}
    if callback_url:
        data["callbackUrl"] = callback_url

    async with httpx.AsyncClient(timeout=30) as client:
        if files:
            form = {k: (None, str(v)) for k, v in data.items()}
            for name, content, mime in files:
                form[name] = (name, content, mime)
            r = await client.post(
                f"{_BASE}/workflows/{workflow_id}/execute",
                headers=_headers(),
                files=form,
            )
        else:
            r = await client.post(
                f"{_BASE}/workflows/{workflow_id}/execute",
                headers=_headers(),
                json=data,
            )
        r.raise_for_status()
        return r.json()


async def poll_workflow_execution(
    workflow_id: str,
    execution_id: str,
    timeout_s: float = 120,
    poll_interval_s: float = 3,
) -> dict[str, Any]:
    """Poll GET /workflows/{id}/executions/{executionId} until Completed or Failed."""
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=15) as client:
        while time.monotonic() < deadline:
            r = await client.get(
                f"{_BASE}/workflows/{workflow_id}/executions/{execution_id}",
                headers=_headers(),
            )
            r.raise_for_status()
            payload = r.json()
            status = payload.get("status", "")
            if status in ("Completed", "Failed", "completed", "failed"):
                return payload
            await asyncio.sleep(poll_interval_s)
    raise TimeoutError(
        f"Workflow execution {execution_id} did not complete within {timeout_s}s"
    )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

async def run_extractor(
    agent_id: str,
    extractor_id: str,
    text: str,
) -> dict[str, Any]:
    """POST /agents/{agentId}/json/{extractorId}/ask-file?useWebhook=false

    Sends the text as a plain-text file upload (multipart/form-data).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_BASE}/agents/{agent_id}/json/{extractor_id}/ask-file",
            headers=_headers(),
            params={"useWebhook": "false"},
            files={"file": ("email.txt", text.encode("utf-8"), "text/plain")},
        )
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# AI Tools
# ---------------------------------------------------------------------------

async def run_ai_tool(tool: str, text: str, secondary_text: str = "", options: dict[str, Any] | None = None) -> str:
    """POST /ai-tools/{tool}/run → processed text.

    Known tools and their options:
      translate  — options: {"target": "<lang_code>", "style": "natural|literal|formal"}
      summarize  — options: {"length": "short|medium|long", "style": "prose|bullets"}
      email      — options: {"mode": "write|reply", "tone": "formal|friendly|commercial|assertive"}
      rewrite    — options: {"style": "fix|clear|formal|friendly"}
    """
    payload: dict[str, Any] = {"text": text}
    if secondary_text:
        payload["secondaryText"] = secondary_text
    if options:
        payload["options"] = options

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_BASE}/ai-tools/{tool}/run",
            headers=_headers(),
            json=payload,
        )
        r.raise_for_status()
        result = r.json()
        return result.get("result") or result.get("text") or str(result)


async def list_ai_tools() -> list[dict[str, Any]]:
    """GET /ai-tools — returns catalog of available AI Tools."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{_BASE}/ai-tools", headers=_headers())
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", [])


# ---------------------------------------------------------------------------
# Agent chat
# ---------------------------------------------------------------------------

async def agent_chat(
    agent_id: str,
    message: str,
    conversation_id: str | None = None,
) -> tuple[str, str]:
    """OpenAI-compatible chat with an Evolus agent.

    Returns (reply_text, conversation_id).
    Send conversation_id back on follow-up turns to continue the same session.
    model format: "<appId>:<agentId>"
    """
    payload = {
        "model": f"{_APP_ID}:{agent_id}",
        "messages": [{"role": "user", "content": message}],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{_CHAT_BASE}/chat/completions",
            headers=_chat_headers(conversation_id),
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        reply = data["choices"][0]["message"]["content"]
        new_conv_id = r.headers.get("X-Conversation-Id", conversation_id or "")
        return reply, new_conv_id


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

async def add_library_document(
    library_id: str,
    text: str,
    title: str,
) -> dict[str, Any]:
    """POST /memories/{libraryId}/documents?language=en — multipart file upload."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_BASE}/memories/{library_id}/documents",
            headers=_headers(),
            params={"language": "en"},
            files={"file": (f"{title}.txt", text.encode(), "text/plain")},
            data={"title": title},
        )
        r.raise_for_status()
        return r.json()
