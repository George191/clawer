"""Chat completions for the ontology and collection assistants."""

import json
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import HTTPException

from app.config.ai_settings import LLMProvider, ai_settings


async def complete(
    messages: list[dict],
    model: str,
    *,
    json_output: bool = False,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    if not ai_settings.ai_enabled:
        raise HTTPException(503, "AI is disabled in the web service configuration")
    if ai_settings.llm_provider not in (LLMProvider.OPENAI, LLMProvider.CUSTOM):
        raise HTTPException(503, "Configure an OpenAI-compatible gateway for Helio and Scout")
    url, key = ai_settings.active_base_url.strip(), ai_settings.active_api_key
    if not url or (ai_settings.llm_provider == LLMProvider.OPENAI and not key):
        raise HTTPException(
            503,
            "Configure SPIDER_AI_LLM_PROVIDER, the gateway base URL and its credentials on the web service",
        )
    body: dict[str, Any] = {"model": model, "messages": messages}
    if json_output:
        body["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    endpoint = url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=ai_settings.llm_request_timeout) as client:
            if on_chunk is None:
                response = await client.post(endpoint, headers=headers, json=body)
                response.raise_for_status()
                choice = response.json()["choices"][0]
                text, finish = choice["message"]["content"], choice["finish_reason"]
            else:
                parts = []
                finish = None
                async with client.stream(
                    "POST", endpoint, headers=headers, json={**body, "stream": True}
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        choices = json.loads(data)["choices"]
                        if not choices:
                            continue
                        delta = choices[0]["delta"].get("content")
                        if delta:
                            parts.append(delta)
                            on_chunk(delta)
                        if choices[0].get("finish_reason") is not None:
                            finish = choices[0]["finish_reason"]
                text = "".join(parts)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"The model {model} timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"The gateway rejected the request for {model} (HTTP {exc.response.status_code})"
        ) from exc
    except (httpx.RequestError, httpx.InvalidURL) as exc:
        raise HTTPException(502, "The configured model gateway could not be reached") from exc
    except (TypeError, KeyError, IndexError, ValueError, AttributeError) as exc:
        raise HTTPException(502, "The gateway returned an invalid chat completion") from exc
    if finish != "stop" or not isinstance(text, str) or not text.strip():
        raise HTTPException(502, f"The model {model} did not return a complete reply")
    return text
