"""256-d embeddings for semantic scoring + pgvector memory.

Tries an OpenAI-compatible /embeddings endpoint when configured; otherwise uses a
deterministic local hashing embedder so offline / mock mode still works.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Optional

import httpx

EMBED_DIM = 256


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z0-9\+\#\.]{1,}|[\u4e00-\u9fff]{2,}", (text or "").lower())


def local_embed(text: str, *, dim: int = EMBED_DIM) -> list[float]:
    """Bag-of-tokens hashing embedder (stable, dependency-free)."""
    vec = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + (digest[5] / 255.0)
        vec[idx] += sign * weight
    return _l2_normalize(vec)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right))))


class EmbeddingService:
    """Prefer remote embeddings; always able to fall back to local 256-d vectors."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or "text-embedding-3-small").strip()
        self.timeout = timeout
        self.last_source = "local_hash"

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        cleaned = [str(t or "")[:6000] for t in texts]
        if self.base_url and self.api_key:
            try:
                remote = self._remote_embed(cleaned)
                if remote and len(remote) == len(cleaned):
                    self.last_source = "api"
                    return [_l2_normalize(_fit_dim(row)) for row in remote]
            except Exception as exc:
                import logging

                logging.getLogger("embeddings").warning(
                    "remote embedding failed model=%s error=%s; falling back to local_hash",
                    self.model,
                    str(exc)[:200],
                )
        self.last_source = "local_hash"
        return [local_embed(text) for text in cleaned]

    def semantic_similarity(self, left: str, right: str) -> tuple[float, str]:
        a, b = self.embed_many([left, right])
        return cosine(a, b), self.last_source

    def _remote_embed(self, texts: list[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        payload: dict[str, Any] = {"model": self.model, "input": texts if len(texts) > 1 else texts[0]}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        rows = data.get("data") or []
        rows = sorted(rows, key=lambda row: int(row.get("index") or 0))
        return [list(row.get("embedding") or []) for row in rows]


def _fit_dim(vec: list[float], dim: int = EMBED_DIM) -> list[float]:
    if len(vec) == dim:
        return vec
    if len(vec) > dim:
        # Fold overflow dims so API vectors still fit pgvector(256).
        out = vec[:dim]
        for i, value in enumerate(vec[dim:]):
            out[i % dim] += value
        return out
    return vec + [0.0] * (dim - len(vec))


def embedder_from_settings(settings: Any) -> EmbeddingService:
    """Build embedder. Never reuse a chat model name for /embeddings."""
    cfg = {}
    if hasattr(settings, "construction_llm"):
        cfg = settings.construction_llm() or {}
    configured = (getattr(settings, "embedding_model", None) or "").strip()
    # Chat models (gpt-4o-mini, gpt-5.x, etc.) are invalid for embeddings APIs.
    chat_like = configured.lower().startswith("gpt-") and "embedding" not in configured.lower()
    if configured and not chat_like:
        model = configured
    else:
        model = "text-embedding-3-small"
    return EmbeddingService(
        base_url=cfg.get("base_url"),
        api_key=cfg.get("api_key"),
        model=str(model),
    )


def to_pgvector_literal(vec: list[float]) -> str:
    fitted = _fit_dim(vec)
    return "[" + ",".join(f"{v:.8f}" for v in fitted) + "]"
