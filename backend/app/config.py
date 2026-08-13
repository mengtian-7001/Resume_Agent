from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str
    supabase_service_role_key: str
    internal_api_token: str
    allowed_origins: str = "http://localhost:4173,http://localhost:4174,http://127.0.0.1:4173,http://127.0.0.1:4174"
    agent_mode: str = "mock"

    # Shared OpenAI-compatible fallback (used when per-agent values are empty).
    openai_base_url: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = "gpt-4o-mini"

    # Agent 1: Construction（匹配打分 / 生成面试题）
    construction_openai_base_url: Optional[str] = None
    construction_openai_api_key: Optional[str] = None
    construction_model: Optional[str] = None

    # Agent 2: Checker（质检 Construction 输出）
    checker_openai_base_url: Optional[str] = None
    checker_openai_api_key: Optional[str] = None
    checker_model: Optional[str] = None

    neo4j_uri: Optional[str] = None
    neo4j_user: Optional[str] = None
    neo4j_password: Optional[str] = None
    tavily_api_key: Optional[str] = None
    agent_max_react_steps: int = 3
    checker_max_retries: int = 1
    auto_process_queue: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    def construction_llm(self) -> dict[str, Optional[str]]:
        return {
            "base_url": self.construction_openai_base_url or self.openai_base_url,
            "api_key": self.construction_openai_api_key or self.openai_api_key,
            "model": self.construction_model or self.openai_model or "gpt-4o-mini",
        }

    def checker_llm(self) -> dict[str, Optional[str]]:
        return {
            "base_url": self.checker_openai_base_url or self.openai_base_url,
            "api_key": self.checker_openai_api_key or self.openai_api_key,
            "model": self.checker_model or self.openai_model or "gpt-4o-mini",
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
