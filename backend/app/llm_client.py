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
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise LLMClientError(f"HTTP error: {exc}") from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            raise LLMClientError(f"status={response.status_code} body={response.text[:400]}")
        raw = response.json()
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError(f"unexpected response shape: {raw!r}") from exc
        model = str(raw.get("model") or self.model)
        logger.info("llm_chat model=%s duration_ms=%s chars=%s", model, duration_ms, len(content or ""))
        return ChatResult(content=content or "", model=model, duration_ms=duration_ms, raw=raw)


def client_from_llm_config(cfg: dict[str, Optional[str]], *, timeout: float = 90.0) -> OpenAICompatibleClient | None:
    base_url = (cfg.get("base_url") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip() or "gpt-4o-mini"
    if not base_url or not api_key:
        return None
    return OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
