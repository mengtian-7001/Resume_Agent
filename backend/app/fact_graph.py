"""Neo4j adapter for the evidence-grounded Fact Graph.

The adapter is optional in mock mode. Supabase remains the canonical store for
raw files and audit records; Neo4j stores navigable facts and relationships.
"""

from __future__ import annotations

from typing import Any, Protocol

from .config import Settings


class FactGraph(Protocol):
    def upsert_job(self, workspace_id: str, job_id: str, requirements: dict[str, Any]) -> None: ...

    def upsert_candidate(
        self, workspace_id: str, job_id: str, candidate_id: str, profile: dict[str, Any], claims: list[dict[str, Any]]
    ) -> None: ...

    def record_match(
        self, workspace_id: str, job_id: str, candidate_id: str, requirements: dict[str, Any], result: dict[str, Any]
    ) -> None: ...

    def record_review(
        self, workspace_id: str, job_id: str, candidate_id: str, review: dict[str, Any]
    ) -> None: ...

    def related_skills(self, workspace_id: str, job_id: str) -> list[str]: ...

    def close(self) -> None: ...


class NullFactGraph:
    """Keeps the worker runnable before Neo4j is configured."""

    def upsert_job(self, workspace_id: str, job_id: str, requirements: dict[str, Any]) -> None:
        return None

    def upsert_candidate(
        self, workspace_id: str, job_id: str, candidate_id: str, profile: dict[str, Any], claims: list[dict[str, Any]]
    ) -> None:
        return None

    def record_match(
        self, workspace_id: str, job_id: str, candidate_id: str, requirements: dict[str, Any], result: dict[str, Any]
    ) -> None:
        return None

    def record_review(
        self, workspace_id: str, job_id: str, candidate_id: str, review: dict[str, Any]
    ) -> None:
        return None

    def related_skills(self, workspace_id: str, job_id: str) -> list[str]:
        return []

    def close(self) -> None:
        return None


class Neo4jFactGraph:
    def __init__(self, settings: Settings) -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))

    def upsert_job(self, workspace_id: str, job_id: str, requirements: dict[str, Any]) -> None:
        query = """
        MERGE (j:Job {workspace_id: $workspace_id, id: $job_id})
        SET j.title = $title, j.revision = coalesce(j.revision, 0) + 1
        WITH j
        UNWIND $skills AS skill_name
        MERGE (s:Skill {name: skill_name})
        MERGE (r:Requirement {workspace_id: $workspace_id, job_id: $job_id, key: 'must_have:' + skill_name})
        SET r.kind = 'must_have_skill', r.value = skill_name, r.status = 'verified'
        MERGE (j)-[:HAS_REQUIREMENT]->(r)
        MERGE (r)-[:REQUIRES_SKILL]->(s)
        """
        self._execute(
            query,
            workspace_id=workspace_id,
            job_id=job_id,
            title=requirements.get("title", "未命名岗位"),
            skills=requirements.get("must_have_skills", []),
        )

    def upsert_candidate(
        self, workspace_id: str, job_id: str, candidate_id: str, profile: dict[str, Any], claims: list[dict[str, Any]]
    ) -> None:
        query = """
        MERGE (c:Candidate {workspace_id: $workspace_id, job_id: $job_id, id: $candidate_id})
        SET c.display_name = $display_name, c.revision = coalesce(c.revision, 0) + 1
        WITH c
        UNWIND $skills AS skill_name
        MERGE (s:Skill {name: skill_name})
        MERGE (claim:Claim {workspace_id: $workspace_id, candidate_id: $candidate_id, key: 'skill:' + skill_name})
        SET claim.predicate = 'has_skill', claim.value = skill_name, claim.status = 'proposed', claim.confidence = 'high'
        MERGE (c)-[:HAS_CLAIM]->(claim)
        MERGE (claim)-[:ABOUT_SKILL]->(s)
        """
        self._execute(
            query,
            workspace_id=workspace_id,
            job_id=job_id,
            candidate_id=candidate_id,
            display_name=profile.get("name", "匿名候选人"),
            skills=profile.get("skills", []),
        )

    def record_match(
        self, workspace_id: str, job_id: str, candidate_id: str, requirements: dict[str, Any], result: dict[str, Any]
    ) -> None:
        query = """
        MERGE (d:Decision {workspace_id: $workspace_id, job_id: $job_id, candidate_id: $candidate_id})
        SET d.score = $score, d.route = $route, d.hard_gate_pass = $hard_gate_pass,
            d.uncertainty = $uncertainty, d.revision = coalesce(d.revision, 0) + 1
        WITH d
        MATCH (c:Candidate {workspace_id: $workspace_id, job_id: $job_id, id: $candidate_id})
        MERGE (d)-[:FOR_CANDIDATE]->(c)
        """
        self._execute(
            query,
            workspace_id=workspace_id,
            job_id=job_id,
            candidate_id=candidate_id,
            score=result["score"],
            route=result["decision"],
            hard_gate_pass=result["hard_gate_pass"],
            uncertainty=result.get("uncertainty", "medium"),
        )

    def record_review(
        self, workspace_id: str, job_id: str, candidate_id: str, review: dict[str, Any]
    ) -> None:
        query = """
        MERGE (d:Decision {workspace_id: $workspace_id, job_id: $job_id, candidate_id: $candidate_id})
        MERGE (review:Review {workspace_id: $workspace_id, job_id: $job_id, candidate_id: $candidate_id})
        SET review.status = $status, review.issues = $issues, review.model = $model
        MERGE (review)-[:REVIEWS]->(d)
        """
        self._execute(
            query,
            workspace_id=workspace_id,
            job_id=job_id,
            candidate_id=candidate_id,
            status=review["status"],
            issues=review["issues"],
            model=review["model"],
        )

    def related_skills(self, workspace_id: str, job_id: str) -> list[str]:
        query = """
        MATCH (:Job {workspace_id: $workspace_id, id: $job_id})-[:HAS_REQUIREMENT]->(:Requirement)-[:REQUIRES_SKILL]->(s:Skill)
        RETURN DISTINCT s.name AS name
        ORDER BY name
        LIMIT 24
        """
        with self._driver.session() as session:
            result = session.run(query, workspace_id=workspace_id, job_id=job_id)
            return [str(record["name"]) for record in result if record.get("name")]

    def _execute(self, query: str, **parameters: Any) -> None:
        with self._driver.session() as session:
            session.run(query, **parameters).consume()

    def close(self) -> None:
        self._driver.close()


def build_fact_graph(settings: Settings) -> FactGraph:
    if settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password:
        return Neo4jFactGraph(settings)
    return NullFactGraph()
