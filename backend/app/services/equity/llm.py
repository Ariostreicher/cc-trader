"""OpenAI chat wrapper for the equity model.

Encapsulates: model selection, JSON-mode request, retry on transient errors,
and AI-analysis-history accounting.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...models.audit import AIAnalysisHistory

logger = logging.getLogger(__name__)

# Rough public-pricing snapshot for gpt-4o (USD per 1M tokens). Used only
# for cost accounting; update as pricing changes.
_PRICING_PER_MILLION = {
    "gpt-4o": (2.50, 10.00),       # (input, output)
    "gpt-4o-mini": (0.15, 0.60),
}


async def run_chat_json(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.2,
) -> tuple[str, dict[str, Any]]:
    """Returns (raw_content, usage_dict). Writes one AIAnalysisHistory row."""
    chosen = model or settings.OPENAI_MODEL_CHAT
    if not settings.OPENAI_API_KEY:
        # Some providers (notably Ollama) don't actually need a key; allow an
        # explicit "ollama" sentinel for clarity.
        raise RuntimeError(
            "OPENAI_API_KEY not configured — set it (or any compatible-provider key) "
            "to run the equity model. See docs/FREE_SETUP.md for free options."
        )

    from openai import APIError, AsyncOpenAI

    client_kwargs: dict = {"api_key": settings.OPENAI_API_KEY}
    if settings.OPENAI_BASE_URL:
        client_kwargs["base_url"] = settings.OPENAI_BASE_URL
    client = AsyncOpenAI(**client_kwargs)
    started = time.perf_counter()
    last_exc: Exception | None = None

    create_kwargs: dict = {
        "model": chosen,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # JSON-mode is supported by OpenAI, Groq, and most Together/OpenRouter
    # routes but not by older Ollama versions. Set LLM_DISABLE_JSON_MODE=true
    # if your provider rejects the flag — the parser strips fences itself.
    if not settings.LLM_DISABLE_JSON_MODE:
        create_kwargs["response_format"] = {"type": "json_object"}

    for attempt in range(3):
        try:
            response = await client.chat.completions.create(**create_kwargs)
            content = response.choices[0].message.content or ""
            usage = response.usage.model_dump() if response.usage else {}
            duration_ms = int((time.perf_counter() - started) * 1000)
            cost = _compute_cost(chosen, usage)
            db.add(
                AIAnalysisHistory(
                    user_id=user_id,
                    kind=kind,
                    model=chosen,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    cost_usd=cost,
                    input_payload={"system_len": len(system), "user_len": len(user)},
                    output_payload={"content_len": len(content)},
                    duration_ms=duration_ms,
                )
            )
            return content, {**usage, "cost_usd": cost, "duration_ms": duration_ms}
        except APIError as exc:
            last_exc = exc
            backoff = 0.5 * (2**attempt)
            logger.warning("OpenAI error attempt %d: %s; sleeping %.1fs", attempt, exc, backoff)
            await asyncio.sleep(backoff)
        except Exception as exc:
            last_exc = exc
            break

    db.add(
        AIAnalysisHistory(
            user_id=user_id,
            kind=kind,
            model=chosen,
            error=str(last_exc)[:2000] if last_exc else "unknown error",
        )
    )
    raise RuntimeError(f"OpenAI call failed: {last_exc}")


def _compute_cost(model: str, usage: dict[str, Any]) -> float | None:
    if model not in _PRICING_PER_MILLION:
        return None
    in_rate, out_rate = _PRICING_PER_MILLION[model]
    pt = usage.get("prompt_tokens") or 0
    ct = usage.get("completion_tokens") or 0
    return round((pt / 1_000_000) * in_rate + (ct / 1_000_000) * out_rate, 6)
