"""Controlled JD-only web research for the Construction ReAct agent."""

from __future__ import annotations

import re
from typing import Any

import httpx

from .config import Settings


class JobResearchService:
    """Tavily-backed research with a safe mock fallback.

    Candidate text and PII are never included in the query. The returned
    citation bundle is contextual support only; it cannot alter hard gates.
    """

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.tavily_api_key
        self._mode = settings.agent_mode

    def research(self, requirements: dict[str, Any]) -> dict[str, Any]:
        title = str(requirements.get("title") or "岗位")
        skills = [str(skill) for skill in requirements.get("must_have_skills", [])[:5]]
        query = f"{title} {' '.join(skills)} 技术能力实践"
        if self._mode == "mock" or not self._api_key:
            return {
                "mode": "mock",
                "query": query,
                "sources": [],
                "status": "mock_ready",
                "reason": "TAVILY_API_KEY is not configured or AGENT_MODE=mock",
            }

        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self._api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": 5,
                "include_answer": False,
            },
            timeout=15,
        )
        response.raise_for_status()
        sources = [
            {
                "source_id": _source_id(item.get("url", "")),
                "title": _clean_text(item.get("title", "")),
                "url": item.get("url", ""),
                "excerpt": _clean_text(item.get("content", ""))[:1000],
                "score": item.get("score"),
            }
            for item in response.json().get("results", [])
            if item.get("url")
        ]
        return {
            "mode": "tavily",
            "query": query,
            "sources": sources,
            "status": "researched",
        }


def _clean_text(value: str) -> str:
    """Strip common prompt-injection phrasing before storing web excerpts."""
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(
        r"(?i)(ignore (all |any )?(previous|above) instructions|system prompt|developer message)",
        "[filtered]",
        text,
    )
    return text


def _source_id(url: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", url)[-48:] or "source"
