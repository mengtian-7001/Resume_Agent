"""Skill synonym ontology for resume–JD matching.

Inspired by open-source ATS patterns (HireLens ESCO-style aliases,
ResumeIQ semantic skill matching): normalize surface forms to canonical
skills before set coverage is computed.
"""

from __future__ import annotations

import re
from typing import Iterable

# canonical skill -> aliases (lowercased, punctuation-stripped for lookup)
SKILL_GROUPS: dict[str, list[str]] = {
    "Python": ["python", "py"],
    "Java": ["java"],
    "Scala": ["scala"],
    "LangChain": ["langchain", "lang chain", "lcel"],
    "Function Calling": [
        "function calling",
        "tool calling",
        "tool use",
        "tools calling",
        "工具调用",
        "函数调用",
    ],
    "Multi-Agent": [
        "multi-agent",
        "multi agent",
        "multiagent",
        "多智能体",
        "多代理",
    ],
    "Prompt Engineering": [
        "prompt engineering",
        "prompt",
        "提示词工程",
        "提示词",
    ],
    "LangGraph": ["langgraph", "lang graph"],
    "FastAPI": ["fastapi", "fast api", "asgi", "starlette"],
    "MCP": ["mcp", "model context protocol"],
    "PostgreSQL": ["postgresql", "postgres", "pg"],
    "Redis": ["redis", "redis 集群", "redis集群"],
    "Docker": ["docker", "容器化", "container", "containerization"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Celery": ["celery"],
    "gRPC": ["grpc"],
    "Airflow": ["airflow", "气流调度"],
    "Spark": ["spark", "pyspark", "apache spark"],
    "SQL": ["sql"],
    "Hive": ["hive"],
    "Kafka": ["kafka"],
    "ETL": ["etl", "extract transform load", "数据抽取", "数据清洗", "数据同步"],
    "数仓": ["数仓", "数据仓库", "data warehouse", "dw", "ods", "dwd", "dws", "ads"],
    "Hadoop": ["hadoop", "hdfs"],
    "数据治理": ["数据治理", "data governance", "元数据"],
    "数据建模": ["数据建模", "kimball", "scd2", "dimensional modeling", "维度建模"],
    "Flink": ["flink"],
    "dbt": ["dbt"],
    "ClickHouse": ["clickhouse"],
    "React": ["react", "react 视图层", "hooks", "函数组件"],
    "TypeScript": ["typescript", "ts"],
    "CSS": ["css", "样式工程化"],
    "Webpack": ["webpack", "模块打包器", "vite"],
    "状态管理": ["状态管理", "redux", "zustand"],
    "Jest": ["jest"],
    "Next.js": ["next.js", "nextjs"],
    "无障碍": ["无障碍", "无障碍基础", "a11y", "accessibility"],
    "微前端": ["微前端", "micro frontend"],
    "Node.js": ["node.js", "nodejs", "node 运行时", "node"],
    "Express": ["express", "express 中间件"],
    "MySQL": ["mysql", "innodb"],
    "REST API": ["rest api", "rest", "资源型接口", "openapi"],
    "GraphQL": ["graphql"],
    "RAG": ["rag", "检索增强生成", "retrieval augmented"],
    "Embedding": ["embedding", "embeddings", "句向量", "sentence embedding"],
    "向量数据库": ["向量数据库", "vector db", "vectordb", "milvus", "faiss"],
    "文档解析": ["文档解析", "pdf 解析", "pdf parsing", "ocr"],
    "OCR": ["ocr"],
    "检索评测": ["检索评测", "recall@k", "ndcg", "mrr"],
    "Rerank": ["rerank", "re-rank", "重排序"],
    "LlamaIndex": ["llamaindex", "llama index"],
    "多路召回": ["多路召回", "混合检索", "hybrid retrieval"],
    "评测体系": ["评测体系", "evaluation", "eval harness"],
    "OpenTelemetry": ["opentelemetry", "otel", "tracing"],
}

# General-purpose languages: useful but rarely the sole "must-have" for domain roles.
GENERAL_LANGUAGE_SKILLS = frozenset({"Python", "Java", "Scala", "TypeScript", "Node.js"})

# Domain skills that should outrank generic languages when present in the JD.
DOMAIN_PRIORITY_SKILLS = frozenset(
    {
        "ETL",
        "数仓",
        "SQL",
        "Spark",
        "Hive",
        "Kafka",
        "Airflow",
        "Flink",
        "dbt",
        "ClickHouse",
        "Hadoop",
        "数据建模",
        "数据治理",
        "LangChain",
        "LangGraph",
        "Function Calling",
        "Multi-Agent",
        "Prompt Engineering",
        "FastAPI",
        "MCP",
        "RAG",
        "向量数据库",
        "PostgreSQL",
        "MySQL",
        "Redis",
        "Docker",
        "Kubernetes",
    }
)


def _norm_token(value: str) -> str:
    text = value.strip().lower()
    text = text.replace("／", "/").replace("（", "(").replace("）", ")")
    text = re.sub(r"[\s_\-]+", " ", text)
    return text


def build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for canonical, aliases in SKILL_GROUPS.items():
        index[_norm_token(canonical)] = canonical
        for alias in aliases:
            index[_norm_token(alias)] = canonical
    return index


ALIAS_TO_CANONICAL = build_alias_index()


def canonicalize_skill(skill: str) -> str:
    key = _norm_token(skill)
    if key in ALIAS_TO_CANONICAL:
        return ALIAS_TO_CANONICAL[key]
    # prefix / containment soft match for compound labels like "Redis 集群"
    for alias, canonical in ALIAS_TO_CANONICAL.items():
        if alias and (alias in key or key in alias):
            if len(alias) >= 3 and len(key) >= 3:
                return canonical
    return skill.strip()


def canonicalize_skills(skills: Iterable[str]) -> set[str]:
    return {canonicalize_skill(skill) for skill in skills if skill and str(skill).strip()}


def expand_text_skills(text: str) -> set[str]:
    """Detect canonical skills mentioned in free text (raw resume / bullets)."""
    if not text:
        return set()
    blob = _norm_token(text)
    found: set[str] = set()
    # Longer aliases first to prefer specific matches.
    aliases = sorted(ALIAS_TO_CANONICAL.items(), key=lambda item: len(item[0]), reverse=True)
    for alias, canonical in aliases:
        if len(alias) < 2:
            continue
        if alias in blob:
            found.add(canonical)
    return found
