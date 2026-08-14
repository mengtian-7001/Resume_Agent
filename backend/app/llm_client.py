"""OpenAI-compatible chat client (Evolink / OpenAI / any /v1 base URL)."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger("agent_chain")


from .llm_limits import LLMBudgetExceeded, get_llm_limiter


@dataclass
class ChatResult:
    content: str
    model: str
    duration_ms: int
    raw: dict[str, Any]


class LLMClientError(RuntimeError):
    pass


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating markdown fences."""
    blob = (text or "").strip()
    if not blob:
        raise LLMClientError("empty model response")
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?\s*", "", blob)
        blob = re.sub(r"\s*```$", "", blob)
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", blob)
    if not match:
        raise LLMClientError("no JSON object in model response")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise LLMClientError("JSON root is not an object")
    return data


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 90.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 2500,
    ) -> tuple[dict[str, Any], ChatResult]:
        result = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        return extract_json_object(result.content), result

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 2500,
        max_retries: int = 3,
    ) -> ChatResult:
        if not self.base_url or not self.api_key:
            raise LLMClientError("missing base_url or api_key")
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        last_error = ""
        limiter = get_llm_limiter()
        for attempt in range(max(1, max_retries)):
            try:
                remaining = limiter.remaining_deadline_sec()
                timeout = self.timeout
                if remaining is not None:
                    if remaining <= 0.5:
                        raise LLMBudgetExceeded("job_deadline_exceeded")
                    timeout = min(timeout, max(1.0, remaining))
                with limiter.slot(acquire_timeout=min(60.0, timeout)):
                    with httpx.Client(timeout=timeout) as client:
                        response = client.post(url, headers=headers, json=payload)
            except LLMBudgetExceeded as exc:
                raise LLMClientError(str(exc)) from exc
            except httpx.HTTPError as exc:
                last_error = f"HTTP error: {exc}"
                limiter.record_failure()
                if attempt + 1 >= max_retries:
                    raise LLMClientError(last_error) from exc
                time.sleep(min(8.0, 0.6 * (2**attempt)))
                continue

            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = f"status={response.status_code} body={response.text[:400]}"
                limiter.record_failure(is_rate_limit=response.status_code == 429)
                if attempt + 1 >= max_retries:
                    raise LLMClientError(last_error)
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(8.0, 0.8 * (2**attempt))
                logger.warning("llm_chat retry attempt=%s delay=%.1fs %s", attempt + 1, delay, last_error[:120])
                time.sleep(delay)
                continue

            if response.status_code >= 400:
                limiter.record_failure()
                raise LLMClientError(f"status={response.status_code} body={response.text[:400]}")

            duration_ms = int((time.perf_counter() - started) * 1000)
            raw = response.json()
            try:
                content = raw["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                limiter.record_failure()
                raise LLMClientError(f"unexpected response shape: {raw!r}") from exc
            model = str(raw.get("model") or self.model)
            limiter.record_success()
            logger.info("llm_chat model=%s duration_ms=%s chars=%s", model, duration_ms, len(content or ""))
            return ChatResult(content=content or "", model=model, duration_ms=duration_ms, raw=raw)

        raise LLMClientError(last_error or "llm request failed")


def client_from_llm_config(cfg: dict[str, Optional[str]], *, timeout: float = 90.0) -> OpenAICompatibleClient | None:
    base_url = (cfg.get("base_url") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip() or "gpt-4o-mini"
    if not base_url or not api_key:
        return None
    return OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
