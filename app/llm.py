"""AMD Token Factory LLM client.

Single shared client used by every module. Features:
- Async semaphore (≤ 4 concurrent) + token bucket (~15 RPM)
- Exponential backoff + jitter on 429 only
- Fallback chain: Gemma-4-31B-IT → Qwen3.8-Flash-Next → DeepSeek-V4-Flash
- Structured output via json_object + pydantic validation + one repair retry
- Per-call audit logging (model, latency, tokens, fallback events)
- Fails loudly at startup if primary model is missing from /v1/models
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, TypeVar

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_BASE_URL = os.environ.get(
    "TOKEN_FACTORY_BASE_URL", "https://developer.amd.com.cn/radeon/api/v1"
)
_API_KEY = os.environ.get("TOKEN_FACTORY_API_KEY", "")
_PRIMARY = os.environ.get("LLM_PRIMARY_MODEL", "Gemma-4-31B-IT")
_FALLBACKS = [
    m.strip()
    for m in os.environ.get(
        "LLM_FALLBACK_MODELS", "Qwen3.8-Flash-Next,DeepSeek-V4-Flash"
    ).split(",")
    if m.strip()
]

_MAX_CONCURRENT = int(os.environ.get("LLM_MAX_CONCURRENT", "4"))
_TARGET_RPM = float(os.environ.get("LLM_TARGET_RPM", "15"))

_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

# Token bucket state (best-effort; shared only within this process)
_bucket_lock = asyncio.Lock()
_bucket_tokens: float = _TARGET_RPM
_bucket_last_refill: float = time.monotonic()

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=_BASE_URL, api_key=_API_KEY)
    return _client


async def _acquire_token() -> None:
    global _bucket_tokens, _bucket_last_refill
    async with _bucket_lock:
        now = time.monotonic()
        elapsed = now - _bucket_last_refill
        _bucket_tokens = min(_TARGET_RPM, _bucket_tokens + elapsed * (_TARGET_RPM / 60.0))
        _bucket_last_refill = now
        if _bucket_tokens < 1:
            wait = (1 - _bucket_tokens) / (_TARGET_RPM / 60.0)
            await asyncio.sleep(wait)
            _bucket_tokens = 0
        else:
            _bucket_tokens -= 1


async def verify_primary_model_available() -> None:
    """Called at startup. Raises if primary model is not in /v1/models."""
    client = _get_client()
    try:
        models = await client.models.list()
        ids = [m.id for m in models.data]
        if _PRIMARY not in ids:
            raise RuntimeError(
                f"Primary model '{_PRIMARY}' not found in Token Factory. "
                f"Available: {ids}. Check TOKEN_FACTORY_API_KEY and LLM_PRIMARY_MODEL."
            )
        logger.info("Token Factory: primary model '%s' confirmed available.", _PRIMARY)
    except Exception as exc:
        raise RuntimeError(f"Token Factory startup check failed: {exc}") from exc


async def chat(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    json_mode: bool = False,
    audit_callback: Any = None,
) -> tuple[str, dict[str, Any]]:
    """Make a chat completion call with automatic fallback and rate limiting.

    Returns (content, meta) where meta contains model, latency_ms, tokens_in, tokens_out,
    fallback_used.
    """
    models = [_PRIMARY] + _FALLBACKS
    last_exc: Exception | None = None

    for model in models:
        for attempt in range(3):
            await _acquire_token()
            async with _semaphore:
                t0 = time.monotonic()
                try:
                    kwargs: dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}

                    response = await _get_client().chat.completions.create(**kwargs)
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    content = response.choices[0].message.content or ""
                    meta = {
                        "model": model,
                        "latency_ms": latency_ms,
                        "tokens_in": response.usage.prompt_tokens if response.usage else None,
                        "tokens_out": response.usage.completion_tokens if response.usage else None,
                        "fallback_used": model != _PRIMARY,
                        "finish_reason": response.choices[0].finish_reason,
                    }
                    if model != _PRIMARY:
                        logger.warning("LLM fallback: using '%s' (primary '%s' failed).", model, _PRIMARY)
                    if audit_callback:
                        await audit_callback(meta)
                    return content, meta

                except Exception as exc:
                    last_exc = exc
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status == 429:
                        retry_after = float(
                            getattr(getattr(exc, "response", None), "headers", {}).get(
                                "Retry-After", 2 ** attempt
                            )
                        )
                        logger.warning(
                            "429 on model '%s' attempt %d — sleeping %.1fs", model, attempt, retry_after
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    logger.error("LLM error on model '%s': %s", model, exc)
                    break  # non-429 error — try next model

    raise RuntimeError(f"All LLM models exhausted. Last error: {last_exc}")


async def chat_json(
    messages: list[dict[str, str]],
    schema: type[T],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.1,
    audit_callback: Any = None,
) -> tuple[T, dict[str, Any]]:
    """Chat with json_object mode + pydantic validation + one repair retry."""
    content, meta = await chat(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=True,
        audit_callback=audit_callback,
    )
    try:
        return schema.model_validate_json(content), meta
    except Exception as parse_exc:
        logger.warning("JSON parse failed (%s) — attempting repair retry.", parse_exc)
        repair_messages = messages + [
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    f"Your response could not be parsed. Error: {parse_exc}. "
                    "Please return ONLY valid JSON that matches the required schema, with no markdown."
                ),
            },
        ]
        content2, meta2 = await chat(
            repair_messages,
            max_tokens=max_tokens,
            temperature=0.0,
            json_mode=True,
            audit_callback=audit_callback,
        )
        try:
            return schema.model_validate_json(content2), meta2
        except Exception as exc2:
            raise ValueError(
                f"LLM output could not be parsed after repair: {exc2}\nRaw: {content2}"
            ) from exc2


async def get_usage() -> dict[str, Any]:
    """Fetch /v1/usage from Token Factory (daily spend cap etc.)."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_BASE_URL}/usage",
            params={"include_recent": "true"},
            headers={"Authorization": f"Bearer {_API_KEY}"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
