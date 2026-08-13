from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import fitz
from docx import Document as DocxDocument
from postgrest.exceptions import APIError
from supabase import Client, create_client

from .agents import MockCheckerAgent, MockConstructionAgent, OpenAICheckerAgent, OpenAIConstructionAgent
from .agent_trace import AgentRunTracer, make_step
from .config import get_settings
from .fact_graph import build_fact_graph
from .skill_ontology import (
    DOMAIN_PRIORITY_SKILLS,
    GENERAL_LANGUAGE_SKILLS,
    expand_text_skills,
)
from .web_research import JobResearchService

BUCKET = "screening-documents"
SUPPORTED_SKILLS = [
    "Python",
    "LangChain",
    "Function Calling",
    "Multi-Agent",
    "Prompt Engineering",
    "LangGraph",
    "FastAPI",
    "MCP",
]


class ScreeningWorker:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client: Client = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
        self.construction_agent = self._build_construction_agent(settings)
        self.checker_agent = self._build_checker_agent(settings)
        self.fact_graph = build_fact_graph(settings)
        self.job_research = JobResearchService(settings)
        self.tracer = AgentRunTracer(self.client, agent_mode=settings.agent_mode)

    @staticmethod
    def _build_construction_agent(settings: Any):
        cfg = settings.construction_llm()
        if settings.agent_mode == "openai" and cfg.get("api_key") and cfg.get("base_url"):
            return OpenAIConstructionAgent(settings)
        return MockConstructionAgent()

    @staticmethod
    def _build_checker_agent(settings: Any):
        cfg = settings.checker_llm()
        if settings.agent_mode == "openai" and cfg.get("api_key") and cfg.get("base_url"):
            return OpenAICheckerAgent(settings)
        return MockCheckerAgent()

    def _trace(self, task: dict[str, Any], step: dict[str, Any], **kwargs: Any) -> None:
        try:
            self.tracer.append_step(
                task["workspace_id"],
                task["screening_job_id"],
                step,
                **kwargs,
            )
        except Exception:
            pass

    def run_once(self) -> dict[str, Any]:
        task = self._claim_task()
        if not task:
            return {"processed": False, "reason": "queue_empty"}

        try:
            self.client.table("screening_jobs").update({"status": "processing"}).eq(
                "id", task["screening_job_id"]
            ).in_("status", ["queued", "uploading"]).execute()
            if task["task_type"] == "parse_jd":
                self._parse_jd(task)
            elif task["task_type"] == "parse_resume":
                self._parse_resume(task)
            elif task["task_type"] == "match":
                deferred = self._match_candidate(task)
                if deferred:
                    self.client.table("processing_tasks").delete().eq("id", task["id"]).execute()
                    return {"processed": True, "task_id": task["id"], "task_type": "match", "deferred": True}
            else:
                raise ValueError(f"Unsupported task type: {task['task_type']}")
            self._complete_task(task["id"])
            if task["task_type"] == "parse_resume":
                self._enqueue_match_task_if_ready(task["screening_job_id"], task["workspace_id"])
            return {"processed": True, "task_id": task["id"], "task_type": task["task_type"]}
        except Exception as exc:
            self._fail_task(task, str(exc))
            raise

    def process_job_until_done(self, job_id: str, *, max_tasks: int = 60) -> dict[str, Any]:
        import time

        processed_tasks = 0
        idle_rounds = 0
        recovered = self._recover_empty_completed_job(job_id)
        for _ in range(max_tasks):
            job = (
                self.client.table("screening_jobs")
                .select("id,status,processed_count,candidate_count,error_message,workspace_id")
                .eq("id", job_id)
                .single()
                .execute()
                .data
            )
            if not job:
                return {"status": "not_found", "processed_tasks": processed_tasks}
            if job["status"] in ("completed", "failed", "cancelled"):
                if job["status"] in ("completed", "failed") and self._job_needs_rematch(job_id):
                    self._recover_empty_completed_job(job_id)
                    idle_rounds = 0
                    continue
                return {
                    "status": job["status"],
                    "processed_tasks": processed_tasks,
                    "job": job,
                    "recovered": recovered,
                }

            workspace_id = job.get("workspace_id")
            if workspace_id:
                self._ensure_resume_tasks(job_id, workspace_id)
                self._enqueue_match_task_if_ready(job_id, workspace_id)

            try:
                result = self.run_once_for_job(job_id)
            except Exception:
                continue
            if result.get("processed"):
                processed_tasks += 1
                idle_rounds = 0
            else:
                idle_rounds += 1
                pending = (
                    self.client.table("processing_tasks")
                    .select("id,task_type,status")
                    .eq("screening_job_id", job_id)
                    .in_("status", ["queued", "processing"])
                    .execute()
                )
                if not pending.data and idle_rounds >= 8:
                    break
                time.sleep(0.25 if idle_rounds < 5 else 0.6)

        job = (
            self.client.table("screening_jobs")
            .select("id,status,processed_count,candidate_count,error_message")
            .eq("id", job_id)
            .single()
            .execute()
            .data
        )
        return {
            "status": job["status"] if job else "unknown",
            "processed_tasks": processed_tasks,
            "job": job,
            "recovered": recovered,
        }

    def run_once_for_job(self, job_id: str) -> dict[str, Any]:
        """Claim and run the next queued task for a specific job (not the global queue)."""
        pending = (
            self.client.table("processing_tasks")
            .select("*")
            .eq("screening_job_id", job_id)
            .eq("status", "queued")
            .order("created_at")
            .limit(20)
            .execute()
            .data
        ) or []
        if not pending:
            return {"processed": False, "reason": "queue_empty"}
        priority = {"parse_jd": 0, "parse_resume": 1, "match": 2}
        pending.sort(key=lambda row: (priority.get(row.get("task_type"), 9), row.get("created_at") or ""))
        task_id = pending[0]["id"]
        claimed = (
            self.client.table("processing_tasks")
            .update(
                {
                    "status": "processing",
                    "attempts": (pending[0].get("attempts") or 0) + 1,
                    "started_at": _now(),
                }
            )
            .eq("id", task_id)
            .eq("status", "queued")
            .select("*")
            .execute()
            .data
        )
        if not claimed:
            return {"processed": False, "reason": "claim_lost"}
        task = claimed[0]
        try:
            self.client.table("screening_jobs").update({"status": "processing"}).eq("id", job_id).in_(
                "status", ["queued", "uploading"]
            ).execute()
            if task["task_type"] == "parse_jd":
                self._parse_jd(task)
            elif task["task_type"] == "parse_resume":
                self._parse_resume(task)
            elif task["task_type"] == "match":
                deferred = self._match_candidate(task)
                if deferred:
                    self.client.table("processing_tasks").delete().eq("id", task["id"]).execute()
                    return {"processed": True, "task_id": task["id"], "task_type": "match", "deferred": True}
            else:
                raise ValueError(f"Unsupported task type: {task['task_type']}")
            self._complete_task(task["id"])
            if task["task_type"] == "parse_resume":
                self._enqueue_match_task_if_ready(task["screening_job_id"], task["workspace_id"])
            return {"processed": True, "task_id": task["id"], "task_type": task["task_type"]}
        except Exception as exc:
            self._fail_task(task, str(exc))
            raise

    def _claim_task(self) -> dict[str, Any] | None:
        response = self.client.rpc("claim_processing_task").execute()
        return response.data[0] if response.data else None

    def _parse_jd(self, task: dict[str, Any]) -> None:
        try:
            self.tracer.ensure_run(task["workspace_id"], task["screening_job_id"], status="running")
        except Exception:
            pass
        t0 = time.perf_counter()
        self._trace(
            task,
            make_step("parse_jd.extract", "解析 JD 文本与硬门槛", status="running"),
            status="running",
            stage="parse_jd",
        )
        document = self._get_document(task["document_id"])
        text = self._extract_and_store(document)
        requirements = self._extract_requirements(text)
        requirements["raw_text"] = text
        self._trace(
            task,
            make_step(
                "parse_jd.extract",
                "解析 JD 文本与硬门槛",
                status="completed",
                duration_ms=int((time.perf_counter() - t0) * 1000),
                detail=f"title={requirements.get('title')} must={requirements.get('must_have_skills')}",
                ended_at=_now(),
            ),
            stage="parse_jd",
        )

        t1 = time.perf_counter()
        self._trace(
            task,
            make_step("jd_research", "JD 岗位研究", status="running"),
            stage="jd_research",
        )
        requirements["job_context"] = self.job_research.research(requirements)
        research_mode = (requirements["job_context"] or {}).get("mode") or "unknown"
        self._trace(
            task,
            make_step(
                "jd_research",
                "JD 岗位研究",
                status="completed",
                duration_ms=int((time.perf_counter() - t1) * 1000),
                detail=f"mode={research_mode}",
                ended_at=_now(),
            ),
            stage="jd_research",
            merge_state={"jd_requirements": {
                "title": requirements.get("title"),
                "must_have_skills": requirements.get("must_have_skills"),
                "nice_to_have_skills": requirements.get("nice_to_have_skills"),
                "min_years": requirements.get("min_years"),
                "education": requirements.get("education"),
            }},
        )

        self.client.table("job_requirements").upsert(
            {
                "screening_job_id": task["screening_job_id"],
                "source_document_id": document["id"],
                "title": requirements["title"],
                "requirements": requirements,
                "hard_gates": requirements["hard_gates"],
            },
            on_conflict="source_document_id",
        ).execute()
        try:
            self.fact_graph.upsert_job(task["workspace_id"], task["screening_job_id"], requirements)
            self._trace(
                task,
                make_step("fact_graph.job", "写入岗位事实图", status="completed", detail="upsert_job"),
                stage="parse_jd",
            )
        except Exception as exc:
            self._trace(
                task,
                make_step("fact_graph.job", "写入岗位事实图", status="skipped", detail=str(exc)[:160]),
            )
        self._enqueue_resume_tasks(task["screening_job_id"], task["workspace_id"])

    def _parse_resume(self, task: dict[str, Any]) -> None:
        t0 = time.perf_counter()
        self._trace(
            task,
            make_step("parse_resume.extract", "解析候选人简历", status="running"),
            status="running",
            stage="parse_resume",
        )
        document = self._get_document(task["document_id"])
        text = self._extract_and_store(document)
        profile = self._extract_profile(text)
        profile["raw_text"] = text
        self.client.table("candidate_profiles").upsert(
            {
                "screening_job_id": task["screening_job_id"],
                "source_document_id": document["id"],
                "display_name": profile["name"],
                "profile": profile,
            },
            on_conflict="source_document_id",
        ).execute()
        self._trace(
            task,
            make_step(
                "parse_resume.extract",
                "解析候选人简历",
                status="completed",
                duration_ms=int((time.perf_counter() - t0) * 1000),
                detail=f"name={profile.get('name')} skills={profile.get('skills')}",
                ended_at=_now(),
            ),
            stage="parse_resume",
        )

    def _workspace_screening_config(self, workspace_id: str) -> dict[str, Any] | None:
        try:
            workspace = (
                self.client.table("workspaces")
                .select("screening_config")
                .eq("id", workspace_id)
                .single()
                .execute()
                .data
            )
        except APIError as exc:
            missing_column = getattr(exc, "code", None) == "42703" or "screening_config" in str(exc)
            if missing_column:
                return None
            raise
        return workspace.get("screening_config") if workspace else None

    def _match_candidate(self, task: dict[str, Any]) -> bool:
        """Run matching + question generation. Returns True if deferred (not ready)."""
        job_id = task["screening_job_id"]
        screening_config = self._workspace_screening_config(task["workspace_id"])
        requirement = (
            self.client.table("job_requirements")
            .select("requirements, hard_gates")
            .eq("screening_job_id", job_id)
            .single()
            .execute()
            .data
        )
        profiles = (
            self.client.table("candidate_profiles")
            .select("id, profile")
            .eq("screening_job_id", job_id)
            .execute()
            .data
        ) or []
        if not profiles:
            # Match raced ahead of resume parsing — put the job back and wait.
            self._ensure_resume_tasks(job_id, task["workspace_id"])
            self.client.table("screening_jobs").update(
                {"status": "processing", "error_message": None}
            ).eq("id", job_id).execute()
            return True
        try:
            self.tracer.ensure_run(task["workspace_id"], job_id, status="running")
        except Exception:
            pass
        self._trace(
            task,
            make_step(
                "match.start",
                "开始候选人匹配",
                status="completed",
                detail=f"candidates={len(profiles)} mode={self.settings.agent_mode}",
                model=getattr(self.construction_agent, "model_name", None),
            ),
            status="running",
            stage="candidate_analysis",
        )
        for candidate in profiles:
            name = (candidate.get("profile") or {}).get("name") or candidate.get("id")
            t_c = time.perf_counter()
            self._trace(
                task,
                make_step(
                    "construction.analyze",
                    f"Construction 匹配与出题 · {name}",
                    status="running",
                    model=getattr(self.construction_agent, "model_name", None),
                ),
                stage="construction",
            )
            output = self.construction_agent.analyze(
                requirement["requirements"],
                candidate["profile"],
                job_context=requirement["requirements"].get("job_context"),
                screening_config=screening_config,
            )
            llm_trace = next(
                (
                    t
                    for t in reversed(output.trace)
                    if t.get("tool") in {"llm_judge", "generate_questions"} or t.get("action") == "decision_generate"
                ),
                {},
            )
            # Expand ReAct micro-steps into the live Agent 链 timeline.
            for item in output.trace:
                action = item.get("action") or item.get("tool") or "step"
                tool = item.get("tool")
                label = {
                    "react_plan": "ReAct Plan 规划",
                    "act_observe": f"Act+Observe · {tool or 'tool'}",
                    "reflect": "Reflect 反思",
                    "decision_generate": "Decision + Generate",
                }.get(action, str(action))
                self._trace(
                    task,
                    make_step(
                        f"react.{action}.{tool or 'core'}",
                        f"{label} · {name}",
                        status=str(item.get("status") or "completed"),
                        model=item.get("model"),
                        detail=str(
                            item.get("reason")
                            or item.get("next")
                            or item.get("decision")
                            or item.get("observation")
                            or item.get("ensemble")
                            or ""
                        )[:240],
                        duration_ms=item.get("duration_ms"),
                        ended_at=_now(),
                    ),
                    stage="react",
                )
            self._trace(
                task,
                make_step(
                    "construction.analyze",
                    f"Construction 匹配与出题 · {name}",
                    status="completed",
                    duration_ms=int((time.perf_counter() - t_c) * 1000),
                    model=llm_trace.get("model") or getattr(self.construction_agent, "model_name", None),
                    detail=(
                        f"decision={output.match_result.get('decision')} "
                        f"score={output.match_result.get('score')} "
                        f"llm_source={(output.match_result.get('score_breakdown') or {}).get('score_llm_source')} "
                        f"questions={len(output.questions)}"
                    ),
                    ended_at=_now(),
                ),
                stage="construction",
            )

            t_k = time.perf_counter()
            self._trace(
                task,
                make_step(
                    "checker.review",
                    f"Checker 质检 · {name}",
                    status="running",
                    model=getattr(self.checker_agent, "model_name", None),
                ),
                stage="checker",
            )
            review = self.checker_agent.review(output)
            self._trace(
                task,
                make_step(
                    "checker.review",
                    f"Checker 质检 · {name}",
                    status="completed",
                    duration_ms=int((time.perf_counter() - t_k) * 1000),
                    model=review.get("model"),
                    detail=f"status={review.get('status')} issues={len(review.get('issues') or [])} fallback={review.get('fallback') or 'none'}",
                    ended_at=_now(),
                ),
                stage="checker",
            )

            result = output.match_result
            breakdown = dict(result.get("score_breakdown") or {})
            # Persist exam content inside score_breakdown so the UI still works
            # even when question_packs / agent tables are missing on the remote DB.
            breakdown["questions"] = output.questions
            breakdown["followups"] = output.followups
            self.client.table("match_results").upsert(
                {
                    "screening_job_id": job_id,
                    "candidate_profile_id": candidate["id"],
                    "score": result["score"],
                    "decision": result["decision"],
                    "hard_gate_pass": result["hard_gate_pass"],
                    "score_breakdown": breakdown,
                    "evidence": result.get("evidence") or [],
                    "risks": result.get("risks") or [],
                    "interview_question": (output.questions[0]["question"] if output.questions else None)
                    or (output.followups[0]["question"] if output.followups else None),
                },
                on_conflict="candidate_profile_id",
            ).execute()
            try:
                self.client.table("question_packs").upsert(
                    {
                        "workspace_id": task["workspace_id"],
                        "screening_job_id": job_id,
                        "candidate_profile_id": candidate["id"],
                        "questions": output.questions,
                        "followups": output.followups,
                        "quality": {
                            "question_count": len(output.questions),
                            "followup_count": len(output.followups),
                            "checker_status": review["status"],
                        },
                    },
                    on_conflict="candidate_profile_id",
                ).execute()
            except Exception:
                pass
            try:
                self._persist_agent_artifacts(task, candidate["id"], output, review)
                self._trace(
                    task,
                    make_step("persist.artifacts", f"落库 claims/reviews · {name}", status="completed"),
                    stage="persist",
                )
            except Exception as exc:
                self._trace(
                    task,
                    make_step("persist.artifacts", f"落库 claims/reviews · {name}", status="skipped", detail=str(exc)[:160]),
                )
            try:
                self.fact_graph.upsert_candidate(
                    task["workspace_id"], job_id, candidate["id"], candidate["profile"], output.claims
                )
                self.fact_graph.record_match(task["workspace_id"], job_id, candidate["id"], requirement["requirements"], result)
                self.fact_graph.record_review(task["workspace_id"], job_id, candidate["id"], review)
                self._trace(
                    task,
                    make_step("fact_graph.candidate", f"写入候选人事实图 · {name}", status="completed"),
                    stage="fact_graph",
                )
            except Exception as exc:
                self._trace(
                    task,
                    make_step("fact_graph.candidate", f"写入候选人事实图 · {name}", status="skipped", detail=str(exc)[:160]),
                )

        self.client.table("screening_jobs").update(
            {
                "status": "completed",
                "candidate_count": len(profiles),
                "processed_count": len(profiles),
                "error_message": None,
            }
        ).eq("id", job_id).execute()
        try:
            self.tracer.complete(
                task["workspace_id"],
                job_id,
                merge_state={
                    "candidate_count": len(profiles),
                    "agent_mode": self.settings.agent_mode,
                    "construction": type(self.construction_agent).__name__,
                    "checker": type(self.checker_agent).__name__,
                },
            )
            self._trace(
                task,
                make_step(
                    "pipeline.completed",
                    "筛选链路完成",
                    status="completed",
                    detail=f"candidates={len(profiles)}",
                    ended_at=_now(),
                ),
                status="completed",
                stage="completed",
            )
        except Exception:
            pass
        try:
            self._audit(task["workspace_id"], "screening.completed", "screening_job", job_id, {"candidate_count": len(profiles)})
        except Exception:
            pass
        return False

    def _upsert_agent_run(self, task: dict[str, Any], *, status: str, state: dict[str, Any]) -> None:
        self.client.table("agent_runs").upsert(
            {
                "workspace_id": task["workspace_id"],
                "screening_job_id": task["screening_job_id"],
                "status": status,
                "mode": self.settings.agent_mode,
                "state": state,
                "started_at": _now() if status == "running" else None,
                "completed_at": _now() if status == "completed" else None,
            },
            on_conflict="screening_job_id",
        ).execute()

    def _persist_agent_artifacts(
        self, task: dict[str, Any], candidate_profile_id: str, output: Any, review: dict[str, Any]
    ) -> None:
        """Persist canonical facts, generated questions, reviews and memory metadata.

        Neo4j receives the graph projection separately. These tables make the
        workflow auditable even while Neo4j is disabled in mock development.
        """
        job_id = task["screening_job_id"]
        workspace_id = task["workspace_id"]
        self.client.table("fact_claims").delete().eq("candidate_profile_id", candidate_profile_id).execute()
        claims = [
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "candidate_profile_id": candidate_profile_id,
                "subject_type": claim["subject_type"],
                "predicate": claim["predicate"],
                "value": {"value": claim["value"]},
                "normalized_value": str(claim["value"]).lower(),
                "confidence": claim["confidence"],
                "status": "verified" if review["status"] == "pass" else claim["status"],
                "evidence": claim["evidence"],
                "producer": self.construction_agent.model_name,
                "producer_version": self.settings.agent_mode,
            }
            for claim in output.claims
        ]
        if claims:
            self.client.table("fact_claims").insert(claims).execute()

        self.client.table("question_packs").upsert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "candidate_profile_id": candidate_profile_id,
                "questions": output.questions,
                "followups": output.followups,
                "quality": {
                    "question_count": len(output.questions),
                    "followup_count": len(output.followups),
                    "checker_status": review["status"],
                },
            },
            on_conflict="candidate_profile_id",
        ).execute()
        self.client.table("checker_reviews").upsert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "candidate_profile_id": candidate_profile_id,
                "status": review["status"],
                "feedback": review["issues"],
                "model": review["model"],
            },
            on_conflict="candidate_profile_id",
        ).execute()
        # The mock embedding is intentionally omitted. The table still acts as
        # a versioned memory ledger until a real embedding adapter is enabled.
        if review["status"] == "pass":
            knowledge_points = sorted({question["knowledge_point"] for question in output.questions})
            self.client.table("agent_memory_chunks").insert(
                {
                    "workspace_id": workspace_id,
                    "screening_job_id": job_id,
                    "memory_type": "question",
                    "content": f"已审核题目模式；考点：{', '.join(knowledge_points) or '澄清追问'}",
                    "metadata": {
                        "candidate_profile_id": candidate_profile_id,
                        "checker_status": review["status"],
                        "source": "question_pack",
                    },
                    "source_revision": f"{job_id}:{candidate_profile_id}:v1",
                    "trusted": True,
                }
            ).execute()

    def _get_document(self, document_id: str) -> dict[str, Any]:
        return (
            self.client.table("documents")
            .select("*")
            .eq("id", document_id)
            .single()
            .execute()
            .data
        )

    def _extract_and_store(self, document: dict[str, Any]) -> str:
        self.client.table("documents").update({"status": "parsing"}).eq("id", document["id"]).execute()
        raw = self.client.storage.from_(BUCKET).download(document["storage_path"])
        text = extract_document_text(raw, document["mime_type"])
        if len(text.strip()) < 30:
            raise ValueError("文件无法提取足够的文本，请上传包含可复制文字的 PDF 或 DOCX。")
        self.client.table("documents").update(
            {
                "status": "parsed",
                "extracted_text": text,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "parse_error": None,
            }
        ).eq("id", document["id"]).execute()
        return text

    def _enqueue_resume_tasks(self, job_id: str, workspace_id: str) -> None:
        resumes = (
            self.client.table("documents")
            .select("id")
            .eq("screening_job_id", job_id)
            .eq("document_type", "resume")
            .execute()
            .data
        ) or []
        if not resumes:
            return
        existing = (
            self.client.table("processing_tasks")
            .select("document_id,status")
            .eq("screening_job_id", job_id)
            .eq("task_type", "parse_resume")
            .execute()
            .data
        ) or []
        known = {row["document_id"] for row in existing}
        payload = [
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "document_id": resume["id"],
                "dedupe_key": f"parse_resume:{resume['id']}",
                "task_type": "parse_resume",
            }
            for resume in resumes
            if resume["id"] not in known
        ]
        if payload:
            self.client.table("processing_tasks").upsert(
                payload, on_conflict="dedupe_key"
            ).execute()

    def _ensure_resume_tasks(self, job_id: str, workspace_id: str) -> None:
        """Create any missing resume-parse tasks for this job."""
        self._enqueue_resume_tasks(job_id, workspace_id)

    def _job_needs_rematch(self, job_id: str) -> bool:
        resumes = (
            self.client.table("documents")
            .select("id")
            .eq("screening_job_id", job_id)
            .eq("document_type", "resume")
            .execute()
            .data
        ) or []
        if not resumes:
            return False
        matches = (
            self.client.table("match_results")
            .select("id")
            .eq("screening_job_id", job_id)
            .limit(1)
            .execute()
            .data
        ) or []
        return len(matches) == 0

    def _recover_empty_completed_job(self, job_id: str) -> bool:
        """Re-open jobs that finished with zero match_results despite having resumes."""
        job = (
            self.client.table("screening_jobs")
            .select("id,status,workspace_id")
            .eq("id", job_id)
            .single()
            .execute()
            .data
        )
        if not job or job["status"] not in ("completed", "failed", "queued", "processing"):
            return False
        if not self._job_needs_rematch(job_id):
            return False
        workspace_id = job["workspace_id"]
        self.client.table("screening_jobs").update(
            {
                "status": "queued",
                "processed_count": 0,
                "error_message": None,
            }
        ).eq("id", job_id).execute()
        # Drop a premature match task so it is recreated only after profiles exist.
        self.client.table("processing_tasks").delete().eq("dedupe_key", f"match:{job_id}").execute()
        self._ensure_resume_tasks(job_id, workspace_id)
        # Re-queue completed resume tasks that never produced profiles.
        profiles = (
            self.client.table("candidate_profiles")
            .select("source_document_id")
            .eq("screening_job_id", job_id)
            .execute()
            .data
        ) or []
        profiled_docs = {row["source_document_id"] for row in profiles}
        resume_tasks = (
            self.client.table("processing_tasks")
            .select("id,document_id,status")
            .eq("screening_job_id", job_id)
            .eq("task_type", "parse_resume")
            .execute()
            .data
        ) or []
        for row in resume_tasks:
            if row.get("document_id") not in profiled_docs:
                self.client.table("processing_tasks").update(
                    {
                        "status": "queued",
                        "available_at": _now(),
                        "completed_at": None,
                        "error_message": "requeue_missing_profile",
                    }
                ).eq("id", row["id"]).execute()
        requirements = (
            self.client.table("job_requirements")
            .select("id")
            .eq("screening_job_id", job_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if not requirements:
            jd = (
                self.client.table("documents")
                .select("id")
                .eq("screening_job_id", job_id)
                .eq("document_type", "jd")
                .limit(1)
                .execute()
                .data
            ) or []
            if jd:
                self.client.table("processing_tasks").upsert(
                    {
                        "workspace_id": workspace_id,
                        "screening_job_id": job_id,
                        "document_id": jd[0]["id"],
                        "dedupe_key": f"parse_jd:{jd[0]['id']}",
                        "task_type": "parse_jd",
                        "status": "queued",
                        "error_message": None,
                        "available_at": _now(),
                        "completed_at": None,
                    },
                    on_conflict="dedupe_key",
                ).execute()
        return True

    def _enqueue_match_task_if_ready(self, job_id: str, workspace_id: str) -> None:
        """Only enqueue match after JD + all resumes are parsed and profiles exist.

        Previously, "no remaining parse_resume tasks" was also true when resume
        tasks had never been created yet — that raced match to completion with
        zero candidates and no interview questions.
        """
        jd_pending = (
            self.client.table("processing_tasks")
            .select("id")
            .eq("screening_job_id", job_id)
            .eq("task_type", "parse_jd")
            .neq("status", "completed")
            .execute()
            .data
        )
        if jd_pending:
            return

        resumes = (
            self.client.table("documents")
            .select("id")
            .eq("screening_job_id", job_id)
            .eq("document_type", "resume")
            .execute()
            .data
        ) or []
        if not resumes:
            return

        self._ensure_resume_tasks(job_id, workspace_id)

        resume_tasks = (
            self.client.table("processing_tasks")
            .select("id,status")
            .eq("screening_job_id", job_id)
            .eq("task_type", "parse_resume")
            .execute()
            .data
        ) or []
        if len(resume_tasks) < len(resumes):
            return
        if any(task["status"] != "completed" for task in resume_tasks):
            return

        profiles = (
            self.client.table("candidate_profiles")
            .select("id")
            .eq("screening_job_id", job_id)
            .execute()
            .data
        ) or []
        if not profiles:
            return

        self.client.table("processing_tasks").upsert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "dedupe_key": f"match:{job_id}",
                "task_type": "match",
                "status": "queued",
                "error_message": None,
                "available_at": _now(),
                "completed_at": None,
            },
            on_conflict="dedupe_key",
        ).execute()

    def _complete_task(self, task_id: str) -> None:
        self.client.table("processing_tasks").update(
            {"status": "completed", "completed_at": _now()}
        ).eq("id", task_id).execute()

    def _fail_task(self, task: dict[str, Any], message: str) -> None:
        attempts = task["attempts"]
        retry = attempts < 3
        payload: dict[str, Any] = {
            "attempts": attempts,
            "status": "queued" if retry else "failed",
            "error_message": message[:1000],
            "available_at": _now(),
        }
        if not retry:
            payload["completed_at"] = _now()
            self.client.table("screening_jobs").update(
                {"status": "failed", "error_message": "部分文件处理失败，请检查任务详情。"}
            ).eq("id", task["screening_job_id"]).execute()
        self.client.table("processing_tasks").update(payload).eq("id", task["id"]).execute()
        if task.get("document_id"):
            self.client.table("documents").update(
                {"status": "failed", "parse_error": message[:1000]}
            ).eq("id", task["document_id"]).execute()

    def _audit(
        self, workspace_id: str, action: str, resource_type: str, resource_id: str, metadata: dict[str, Any]
    ) -> None:
        self.client.table("audit_logs").insert(
            {
                "workspace_id": workspace_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "metadata": metadata,
            }
        ).execute()

    @staticmethod
    def _extract_requirements(text: str) -> dict[str, Any]:
        title = _first_match(text, r"岗位名称[：:]\s*([^\n]+)") or _first_match(text, r"职位名称[：:]\s*([^\n]+)") or "未命名岗位"
        years = int(_first_match(text, r"(\d+)\s*年(?:及以上|以上).{0,12}(?:经验|开发)") or 0)
        education = _first_match(text, r"(本科|硕士|博士|大专)(?:及以上)?") or None

        must_have, nice_to_have = _split_jd_skills(text, title)
        return {
            "title": title.strip(),
            "must_have_skills": must_have,
            "nice_to_have_skills": nice_to_have,
            "min_years": years,
            "education": education,
            "raw_text": text[:4000],
            "hard_gates": [
                {"field": "min_years", "op": ">=", "value": years},
                {"field": "education", "op": ">=", "value": education},
                {"field": "must_have_skills", "op": "covers_all", "value": must_have},
            ],
        }

    @staticmethod
    def _extract_profile(text: str) -> dict[str, Any]:
        name = _first_match(text, r"姓名[：:]\s*([^\n\s]+)") or "未命名候选人"
        years = int(_first_match(text, r"(\d+)\s*年(?:相关|开发|工作)?经验") or 0)
        education = _first_match(text, r"(本科|硕士|博士|大专)") or None
        skills = sorted(expand_text_skills(text) | {
            skill for skill in SUPPORTED_SKILLS if re.search(re.escape(skill), text, re.IGNORECASE)
        })
        return {
            "name": name.strip(),
            "years_experience": years,
            "education": education,
            "skills": skills,
            "raw_text": text[:6000],
        }

    @staticmethod
    def _score(requirements: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        required_skills = set(requirements.get("must_have_skills", []))
        profile_skills = set(profile.get("skills", []))
        coverage = len(required_skills & profile_skills) / len(required_skills) if required_skills else 1
        years_ok = profile.get("years_experience", 0) >= requirements.get("min_years", 0)
        education_ok = _education_rank(profile.get("education")) >= _education_rank(requirements.get("education"))
        hard_gate_pass = years_ok and education_ok and coverage == 1
        score = round(100 * (0.55 * coverage + 0.25 * int(years_ok) + 0.20 * int(education_ok)), 2)
        decision = "recommend" if hard_gate_pass and score >= 75 else "review" if hard_gate_pass else "reject"
        evidence = [
            {"type": "skills", "text": f"必备技能覆盖 {len(required_skills & profile_skills)}/{len(required_skills)}"},
            {"type": "experience", "text": f"{profile.get('years_experience', 0)} 年相关经验"},
        ]
        risks = []
        if not years_ok:
            risks.append("未满足最低工作年限")
        if not education_ok:
            risks.append("未满足最低学历要求")
        if coverage < 1:
            risks.append("必备技能覆盖不完整")
        return {
            "score": score,
            "decision": decision,
            "hard_gate_pass": hard_gate_pass,
            "score_breakdown": {
                "skill_coverage": round(coverage * 100, 2),
                "experience": 100 if years_ok else 0,
                "education": 100 if education_ok else 0,
            },
            "evidence": evidence,
            "risks": risks,
            "interview_question": "请结合一个实际项目说明你在工具调用、失败重试与结果审校中的具体职责。",
        }


def extract_document_text(raw: bytes, mime_type: str) -> str:
    if mime_type == "application/pdf":
        pdf = fitz.open(stream=raw, filetype="pdf")
        return "\n".join(page.get_text() for page in pdf)
    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "\n".join(paragraph.text for paragraph in DocxDocument(BytesIO(raw)).paragraphs)
    raise ValueError("不支持的文件格式")


def _split_jd_skills(text: str, title: str) -> tuple[list[str], list[str]]:
    """Split JD skills into must-have vs nice-to-have.

    Keep must-have small and core. Mentions of every tool in a long JD should not
    all become hard gates — overflow goes to nice-to-have.
    """
    found = set(expand_text_skills(text))
    for skill in SUPPORTED_SKILLS:
        if re.search(re.escape(skill), text, re.IGNORECASE):
            found.add(skill)

    must_section = _section_blob(text, r"(?:必备|必须|硬性|任职要求|岗位要求|任职资格)[^\n]{0,8}[：:\n]")
    nice_section = _section_blob(text, r"(?:加分|优先|加分项|优先条件|更好)[^\n]{0,8}[：:\n]")
    explicit_must = expand_text_skills(must_section) if must_section else set()
    explicit_nice = expand_text_skills(nice_section) if nice_section else set()

    domain = {skill for skill in found if skill in DOMAIN_PRIORITY_SKILLS}
    general = {skill for skill in found if skill in GENERAL_LANGUAGE_SKILLS}
    other = found - domain - general

    title_blob = f"{title}\n{text[:240]}"
    role_is_data = bool(re.search(r"数据工程|数仓|ETL|数据仓库|数据分析|数据开发", title_blob, re.IGNORECASE))
    role_is_agent = bool(re.search(r"Agent|LangChain|LangGraph|多智能体", title_blob, re.IGNORECASE))

    data_core = {"ETL", "数仓", "SQL", "数据建模", "Spark"}
    agent_core = {"LangChain", "LangGraph", "Function Calling", "Multi-Agent", "Prompt Engineering", "FastAPI"}

    must_have: set[str] = set(explicit_must)
    nice_to_have: set[str] = set(explicit_nice) | (other - explicit_must)

    if role_is_data:
        for skill in domain:
            if skill in data_core and skill not in explicit_nice:
                must_have.add(skill)
            else:
                nice_to_have.add(skill)
    elif role_is_agent:
        for skill in domain:
            if skill in agent_core and skill not in explicit_nice:
                must_have.add(skill)
            else:
                nice_to_have.add(skill)
    else:
        # Generic role: keep a few domain hits as must, rest preferred.
        ranked_domain = sorted(domain - explicit_nice)
        must_have |= set(ranked_domain[:3])
        nice_to_have |= set(ranked_domain[3:])

    for skill in general:
        if skill in explicit_must:
            must_have.add(skill)
        else:
            nice_to_have.add(skill)

    # Sparse data JD: still surface the role cores when wording is light.
    if role_is_data:
        for hint, skill in (("ETL", "ETL"), ("数仓", "数仓"), ("数据仓库", "数仓"), ("SQL", "SQL")):
            if re.search(hint, text, re.IGNORECASE) and skill not in explicit_nice:
                must_have.add(skill)
                nice_to_have.discard(skill)

    # Explicit must always wins over nice.
    must_have |= explicit_must
    nice_to_have -= must_have

    if not must_have and found:
        seed = list(domain or found)[:3]
        must_have = set(seed)
        nice_to_have |= found - must_have

    def _rank(skill: str) -> tuple[int, str]:
        if skill in data_core or skill in agent_core:
            return (0, skill)
        if skill in DOMAIN_PRIORITY_SKILLS:
            return (1, skill)
        if skill in GENERAL_LANGUAGE_SKILLS:
            return (3, skill)
        return (2, skill)

    # Cap must-have so a laundry-list JD cannot require 10+ hard skills.
    max_must = 4
    ranked_must = sorted(must_have, key=_rank)
    if len(ranked_must) > max_must:
        # Prefer skills that appeared in an explicit 必备 section.
        keep: list[str] = []
        for skill in sorted(explicit_must, key=_rank):
            if skill in must_have and skill not in keep:
                keep.append(skill)
        for skill in ranked_must:
            if skill not in keep:
                keep.append(skill)
            if len(keep) >= max_must:
                break
        overflow = must_have - set(keep)
        must_have = set(keep)
        nice_to_have |= overflow

    return sorted(must_have, key=_rank), sorted(nice_to_have, key=_rank)


def _section_blob(text: str, header_pattern: str) -> str:
    match = re.search(header_pattern, text, re.IGNORECASE)
    if not match:
        return ""
    start = match.end()
    rest = text[start:]
    stop = re.search(r"\n(?:[一二三四五六七八九十\d]+[、.\s]|加分|优先|福利|职责|岗位职责)", rest)
    return rest[: stop.start()] if stop else rest[:800]


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _education_rank(value: str | None) -> int:
    return {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}.get(value or "", 0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
