from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from postgrest.exceptions import APIError
from supabase import Client, create_client

from .agents import MockCheckerAgent, MockConstructionAgent, OpenAICheckerAgent, OpenAIConstructionAgent
from .agent_trace import AgentRunTracer, make_step
from .checker_harness import run_checker_harness
from .checker_policy import apply_checker_review
from .config import get_settings
from .document_text import extract_document_text
from .embeddings import embedder_from_settings, to_pgvector_literal
from .fact_graph import build_fact_graph
from .llm_limits import configure_llm_limiter_from_settings, get_llm_limiter
from .memory_recall import fetch_scoped_feedback_memories, filter_memory_hits, memory_is_usable
from .persist import persist_candidate_core
from .skill_ontology import (
    DOMAIN_PRIORITY_SKILLS,
    GENERAL_LANGUAGE_SKILLS,
    expand_text_skills,
)
from .task_lease import TaskLeaseClient
from .web_research import JobResearchService

logger = logging.getLogger("worker")

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


def _candidate_trace_step(candidate: dict[str, Any], step_id: str, label: str, **kwargs: Any) -> dict[str, Any]:
    """Tag a live step with candidate identity so parallel traces do not collide."""
    cid = str(candidate.get("id") or "")
    name = (candidate.get("profile") or {}).get("name") or cid or "候选人"
    extra = dict(kwargs.pop("extra", None) or {})
    extra.setdefault("candidate_id", cid)
    extra.setdefault("candidate_name", name)
    unique_id = f"{step_id}.{cid}" if cid else step_id
    return make_step(unique_id, label, extra=extra, **kwargs)


class ScreeningWorker:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client: Client = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
        self.fact_graph = build_fact_graph(settings)
        self.job_research = JobResearchService(settings)
        self.tracer = AgentRunTracer(self.client, agent_mode=settings.agent_mode)
        self.embedder = embedder_from_settings(settings)
        self.task_lease = TaskLeaseClient(
            self.client,
            int(getattr(settings, "task_lease_sec", 300) or 300),
        )
        self._db_lock = threading.Lock()
        self.construction_agent = self._build_construction_agent(settings)
        self.checker_agent = self._build_checker_agent(settings)
        configure_llm_limiter_from_settings(settings)

    def _build_construction_agent(self, settings: Any):
        cfg = settings.construction_llm()
        if settings.agent_mode == "openai" and cfg.get("api_key") and cfg.get("base_url"):
            return OpenAIConstructionAgent(
                settings,
                job_research=self.job_research,
                memory_retriever=self._retrieve_memory,
                related_skills_fn=self.fact_graph.related_skills,
            )
        return MockConstructionAgent()

    @staticmethod
    def _build_checker_agent(settings: Any):
        cfg = settings.checker_llm()
        if settings.agent_mode == "openai" and cfg.get("api_key") and cfg.get("base_url"):
            return OpenAICheckerAgent(settings)
        return MockCheckerAgent()

    def _retrieve_memory(
        self,
        workspace_id: str,
        query: str,
        query_vec: list[float],
        *,
        job_id: str | None = None,
        candidate_id: str | None = None,
        job_title: str | None = None,
        skills: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        literal = to_pgvector_literal(query_vec)
        hits: list[dict[str, Any]] = []
        try:
            response = self.client.rpc(
                "match_agent_memory",
                {
                    "target_workspace_id": workspace_id,
                    "query_embedding": literal,
                    "match_count": 4,
                    "target_memory_type": None,
                },
            ).execute()
            for row in response.data or []:
                hits.append({**row, "trust_level": row.get("trust_level") or "human_or_source_verified"})
        except Exception as exc:
            logger.warning("match_agent_memory failed: %s", str(exc)[:160])

        try:
            soft = self.client.rpc(
                "match_agent_memory_soft",
                {
                    "target_workspace_id": workspace_id,
                    "query_embedding": literal,
                    "match_count": 4,
                },
            ).execute()
            for row in soft.data or []:
                hits.append({**row, "trust_level": row.get("trust_level") or "model_checked"})
        except Exception:
            # Soft RPC may be absent before migration; ignore.
            pass

        try:
            hits.extend(
                fetch_scoped_feedback_memories(
                    self.client,
                    workspace_id,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    job_title=job_title,
                    skills=skills,
                )
            )
        except Exception:
            pass

        usable = filter_memory_hits(hits)
        if usable:
            return usable

        # Fail closed when vector RPCs are unavailable: never revive untrusted,
        # revoked, or expired memory merely because it is recent.
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = (
            self.client.table("agent_memory_chunks")
            .select("id,content,metadata,source_revision,trusted,trust_level,expires_at")
            .eq("workspace_id", workspace_id)
            .in_("trust_level", ["model_checked", "source_verified", "human_verified"])
            .or_(f"expires_at.is.null,expires_at.gt.{now_iso}")
            .order("created_at", desc=True)
            .limit(6)
            .execute()
            .data
        ) or []
        return [
            {**row, "similarity": None, "trust_level": row.get("trust_level") or "model_checked"}
            for row in rows
            if memory_is_usable(row)
        ]

    def _trace(self, task: dict[str, Any], step: dict[str, Any], **kwargs: Any) -> None:
        try:
            with self._db_lock:
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
                    self._complete_task(task)
                    return {"processed": True, "task_id": task["id"], "task_type": "match", "deferred": True}
            else:
                raise ValueError(f"Unsupported task type: {task['task_type']}")
            self._complete_task(task)
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
                logger.exception("process_job_until_done task failed job_id=%s", job_id)
                idle_rounds += 1
                time.sleep(0.4)
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
        task = self._claim_task_for_job(job_id)
        if not task:
            return {"processed": False, "reason": "queue_empty"}

        # Fan-out: claim and run multiple parse_resume tasks in parallel.
        if task.get("task_type") == "parse_resume":
            workers = max(1, int(getattr(self.settings, "fanout_workers", 4) or 4))
            resume_batch = [task]
            for _ in range(workers - 1):
                extra = self._claim_task_for_job(job_id, task_type="parse_resume")
                if not extra:
                    break
                resume_batch.append(extra)
            if len(resume_batch) > 1:
                return self._fanout_parse_resumes(job_id, resume_batch)
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
                    self._complete_task(task)
                    return {"processed": True, "task_id": task["id"], "task_type": "match", "deferred": True}
            else:
                raise ValueError(f"Unsupported task type: {task['task_type']}")
            self._complete_task(task)
            if task["task_type"] == "parse_resume":
                self._enqueue_match_task_if_ready(task["screening_job_id"], task["workspace_id"])
            return {"processed": True, "task_id": task["id"], "task_type": task["task_type"]}
        except Exception as exc:
            self._fail_task(task, str(exc))
            raise

    def _fanout_parse_resumes(self, job_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        claimed = candidates

        self.client.table("screening_jobs").update({"status": "processing"}).eq("id", job_id).in_(
            "status", ["queued", "uploading"]
        ).execute()

        ok = 0
        errors: list[str] = []

        def _run(task: dict[str, Any]) -> tuple[str, str | None]:
            try:
                self._parse_resume(task)
                self._complete_task(task)
                return task["id"], None
            except Exception as exc:  # noqa: BLE001
                self._fail_task(task, str(exc))
                return task["id"], str(exc)

        workers = max(1, min(len(claimed), int(getattr(self.settings, "fanout_workers", 4) or 4)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run, task) for task in claimed]
            for fut in as_completed(futures):
                task_id, err = fut.result()
                if err:
                    errors.append(f"{task_id}:{err[:120]}")
                else:
                    ok += 1

        workspace_id = claimed[0]["workspace_id"]
        self._enqueue_match_task_if_ready(job_id, workspace_id)
        return {
            "processed": True,
            "task_type": "parse_resume",
            "fanout": True,
            "claimed": len(claimed),
            "ok": ok,
            "errors": errors[:5],
        }

    def _claim_task(self) -> dict[str, Any] | None:
        return self.task_lease.claim()

    def _claim_task_for_job(self, job_id: str, *, task_type: str | None = None) -> dict[str, Any] | None:
        return self.task_lease.claim_for_job(job_id, task_type)

    def _heartbeat_task(self, task: dict[str, Any]) -> None:
        try:
            if not self.task_lease.heartbeat(task):
                raise RuntimeError("task lease no longer owned")
        except Exception:
            logger.warning("task heartbeat failed id=%s", task.get("id"), exc_info=True)

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
        try:
            related = self.fact_graph.related_skills(task["workspace_id"], task["screening_job_id"])
            if related:
                requirements["job_context"]["related_skills"] = related
        except Exception:
            pass
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
        # Rematch / legacy rows may still carry years=0 even when raw_text says「满 3 年」.
        for row in profiles:
            original = dict(row.get("profile") or {})
            refreshed = _refresh_profile_years(original)
            if int(refreshed.get("years_experience") or 0) != int(original.get("years_experience") or 0):
                row["profile"] = refreshed
                try:
                    with self._db_lock:
                        self.client.table("candidate_profiles").update(
                            {
                                "profile": refreshed,
                                "display_name": refreshed.get("name") or row.get("display_name"),
                            }
                        ).eq("id", row["id"]).execute()
                except Exception:
                    logger.warning("profile years refresh persist skipped id=%s", row.get("id"))
            else:
                row["profile"] = refreshed
        try:
            self.tracer.ensure_run(task["workspace_id"], job_id, status="running")
        except Exception:
            pass
        limiter = get_llm_limiter()
        # Cap to platform maxDuration (Vercel serverless ≤300s) so we fail before a silent kill.
        deadline_sec = min(int(getattr(self.settings, "job_deadline_sec", 280) or 280), 280)
        deadline_at = time.monotonic() + max(60, deadline_sec)
        limiter.set_deadline(deadline_at)
        lease_sec = max(30, min(int(getattr(self.settings, "task_lease_sec", 180) or 180), 600))
        heartbeat_interval = max(10, min(45, lease_sec // 3))
        stop_heartbeat = threading.Event()

        def _heartbeat_loop() -> None:
            while not stop_heartbeat.wait(heartbeat_interval):
                self._heartbeat_task(task)

        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            name=f"task-heartbeat-{task['id']}",
            daemon=True,
        )
        try:
            self._heartbeat_task(task)
            heartbeat_thread.start()
            self._trace(
                task,
                make_step(
                    "match.start",
                    "开始候选人匹配",
                    status="completed",
                    detail=(
                        f"candidates={len(profiles)} mode={self.settings.agent_mode} "
                        f"fanout={min(len(profiles), int(getattr(self.settings, 'fanout_workers', 4) or 4))}"
                    ),
                    model=getattr(self.construction_agent, "model_name", None),
                ),
                status="running",
                stage="candidate_analysis",
            )

            def _analyze_one(candidate: dict[str, Any]) -> tuple[dict[str, Any], Any, dict[str, Any]]:
                with limiter.deadline_context(deadline_at):
                    return _analyze_one_with_deadline(candidate)

            def _analyze_one_with_deadline(
                candidate: dict[str, Any],
            ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
                name = (candidate.get("profile") or {}).get("name") or candidate.get("id")
                def _emit_react_trace(output) -> None:
                    for item in output.trace:
                        action = item.get("action") or item.get("tool") or "step"
                        tool = item.get("tool")
                        label = {
                            "react_plan": "ReAct Plan 规划",
                            "act_observe": f"Act+Observe · {tool or 'tool'}",
                            "reflect": "Reflect 反思",
                            "decision_generate": "Decision + Generate",
                            "revise_from_checker": "按 Checker 修正",
                        }.get(action, str(action))
                        self._trace(
                            task,
                            _candidate_trace_step(
                                candidate,
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

                def _run_construction(*, revision_feedback: list[dict[str, Any]] | None = None):
                    t_c = time.perf_counter()
                    suffix = "（按 Checker 反馈修正）" if revision_feedback else ""
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "construction.analyze",
                            f"Construction 匹配与出题 · {name}{suffix}",
                            status="running",
                            model=getattr(self.construction_agent, "model_name", None),
                        ),
                        stage="construction",
                    )
                    kwargs: dict[str, Any] = {
                        "job_context": requirement["requirements"].get("job_context"),
                        "screening_config": screening_config,
                        "workspace_id": task["workspace_id"],
                        "job_id": job_id,
                    }
                    if revision_feedback is not None:
                        kwargs["revision_feedback"] = revision_feedback
                    try:
                        output = self.construction_agent.analyze(
                            requirement["requirements"],
                            candidate["profile"],
                            **kwargs,
                        )
                    except TypeError:
                        kwargs.pop("revision_feedback", None)
                        output = self.construction_agent.analyze(
                            requirement["requirements"],
                            candidate["profile"],
                            **kwargs,
                        )
                    _emit_react_trace(output)
                    llm_trace = next(
                        (
                            t
                            for t in reversed(output.trace)
                            if t.get("tool") in {"llm_judge", "generate_questions"}
                            or t.get("action") in {"decision_generate", "revise_from_checker"}
                        ),
                        {},
                    )
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "construction.analyze",
                            f"Construction 匹配与出题 · {name}{suffix}",
                            status="completed",
                            duration_ms=int((time.perf_counter() - t_c) * 1000),
                            model=llm_trace.get("model") or getattr(self.construction_agent, "model_name", None),
                            detail=(
                                f"decision={output.match_result.get('decision')} "
                                f"score={output.match_result.get('score')} "
                                f"llm_source={(output.match_result.get('score_breakdown') or {}).get('score_llm_source')} "
                                f"tools={(output.match_result.get('react') or {}).get('tools_used')} "
                                f"questions={len(output.questions)}"
                            ),
                            ended_at=_now(),
                        ),
                        stage="construction",
                    )
                    return output

                def _run_checker(checker_input):
                    t_k = time.perf_counter()
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "checker.review",
                            f"Checker 质检 · {name}",
                            status="running",
                            model=getattr(self.checker_agent, "model_name", None),
                        ),
                        stage="checker",
                    )
                    review = self.checker_agent.review(checker_input)
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "checker.review",
                            f"Checker 质检 · {name}",
                            status="completed",
                            duration_ms=int((time.perf_counter() - t_k) * 1000),
                            model=review.get("model"),
                            detail=(
                                f"status={review.get('status')} issues={len(review.get('issues') or [])} "
                                f"fallback={review.get('fallback') or 'none'} degraded={bool(review.get('degraded'))}"
                            ),
                            ended_at=_now(),
                        ),
                        stage="checker",
                    )
                    return review

                def _revise_from_checker(issues: list[dict[str, Any]]):
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "checker.revise",
                            f"Checker 触发 Construction 修正 · {name}",
                            status="running",
                            detail=f"issues={len(issues)}",
                        ),
                        stage="checker",
                    )
                    revised_output = _run_construction(revision_feedback=issues)
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "checker.revise",
                            f"Checker 触发 Construction 修正 · {name}",
                            status="completed",
                            detail="重新构建后将进行第 2 轮质检",
                        ),
                        stage="checker",
                    )
                    return revised_output

                harness = run_checker_harness(
                    initial_output=_run_construction(),
                    requirements=requirement["requirements"],
                    raw_candidate_profile=candidate["profile"],
                    review=_run_checker,
                    revise=_revise_from_checker,
                    max_rounds=int(getattr(self.settings, "checker_max_revisions", 2) or 2),
                )
                output, review = harness.output, harness.review
                review["revised"] = harness.rounds > 1
                return candidate, output, review

            workers = max(1, min(len(profiles), int(getattr(self.settings, "fanout_workers", 4) or 4)))
            analyzed: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
            if workers == 1 or len(profiles) == 1:
                analyzed = [_analyze_one(candidate) for candidate in profiles]
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_analyze_one, candidate) for candidate in profiles]
                    for fut in as_completed(futures):
                        analyzed.append(fut.result())

            persist_errors: list[str] = []
            optional_warnings: list[str] = []
            core_ok = 0  # candidates with match + question_pack + checker_review
            for candidate, output, review in analyzed:
                name = (candidate.get("profile") or {}).get("name") or candidate.get("id")
                result = dict(output.match_result)
                breakdown = dict(result.get("score_breakdown") or {})
                checker_policy = apply_checker_review(str(result.get("decision") or "reject"), review)
                result["decision"] = checker_policy["decision"]
                output.match_result["decision"] = checker_policy["decision"]
                breakdown.update(checker_policy)
                breakdown["checker_audit"] = {
                    "summary": review.get("summary") or "",
                    "reasoning_path": review.get("reasoning_path") or [],
                    "assumptions": review.get("assumptions") or [],
                    "evidence_summary": review.get("evidence_summary") or [],
                    "issues": review.get("issues") or [],
                    "revised_decision": checker_policy["decision"],
                }
                breakdown["questions"] = output.questions
                breakdown["followups"] = output.followups
                breakdown["checker_status"] = review.get("status")

                try:
                    interview_question = (
                        (output.questions[0]["question"] if output.questions else None)
                        or (output.followups[0]["question"] if output.followups else None)
                    )
                    match_payload = {
                        "score": result["score"],
                        "decision": result["decision"],
                        "hard_gate_pass": result["hard_gate_pass"],
                        "score_breakdown": breakdown,
                        "evidence": result.get("evidence") or [],
                        "risks": result.get("risks") or [],
                        "interview_question": interview_question,
                    }
                    persist_candidate_core(
                        self.client,
                        workspace_id=task["workspace_id"],
                        screening_job_id=job_id,
                        candidate_profile_id=candidate["id"],
                        match_payload=match_payload,
                        questions=output.questions,
                        followups=output.followups,
                        review=review,
                        claims=None,
                        db_lock=self._db_lock,
                    )
                    core_ok += 1
                except Exception as exc:
                    logger.exception("core persist failed candidate=%s", name)
                    persist_errors.append(f"core:{name}:{exc}")

                try:
                    with self._db_lock:
                        self._persist_optional_artifacts(task, candidate["id"], output, review)
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "persist.artifacts",
                            f"落库 claims/memory · {name}",
                            status="completed",
                        ),
                        stage="persist",
                    )
                except Exception as exc:
                    logger.warning("optional artifacts skipped candidate=%s err=%s", name, str(exc)[:160])
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "persist.artifacts",
                            f"落库 claims/memory · {name}",
                            status="skipped",
                            detail=str(exc)[:160],
                        ),
                    )
                    optional_warnings.append(f"artifacts:{name}")

                try:
                    self.fact_graph.upsert_candidate(
                        task["workspace_id"], job_id, candidate["id"], candidate["profile"], output.claims
                    )
                    self.fact_graph.record_match(
                        task["workspace_id"], job_id, candidate["id"], requirement["requirements"], result
                    )
                    self.fact_graph.record_review(task["workspace_id"], job_id, candidate["id"], review)
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "fact_graph.candidate",
                            f"写入候选人事实图 · {name}",
                            status="completed",
                        ),
                        stage="fact_graph",
                    )
                except Exception as exc:
                    logger.warning("fact_graph skipped candidate=%s err=%s", name, str(exc)[:160])
                    self._trace(
                        task,
                        _candidate_trace_step(
                            candidate,
                            "fact_graph.candidate",
                            f"写入候选人事实图 · {name}",
                            status="skipped",
                            detail=str(exc)[:160],
                        ),
                    )
                    optional_warnings.append(f"fact_graph:{name}")

            # Core contract: every candidate needs match_results + question_packs + checker_reviews.
            if core_ok < len(profiles):
                message = (
                    f"core persist incomplete: {core_ok}/{len(profiles)} "
                    f"(require match+questions+checker); "
                    + "; ".join(persist_errors[:5])
                )
                logger.error("job %s %s", job_id, message)
                self.client.table("screening_jobs").update(
                    {
                        "status": "failed",
                        "candidate_count": len(profiles),
                        "processed_count": core_ok,
                        "error_message": message[:500],
                    }
                ).eq("id", job_id).execute()
                try:
                    self.tracer.fail(
                        task["workspace_id"],
                        job_id,
                        reason=message,
                        merge_state={
                            "persist_errors": persist_errors[:8],
                            "optional_warnings": optional_warnings[:8],
                        },
                    )
                except Exception:
                    logger.exception("tracer.fail after core persist failure job_id=%s", job_id)
                return False

            partial_note = None
            if optional_warnings:
                partial_note = f"completed_with_warnings: {'; '.join(optional_warnings[:5])}"
                logger.warning("job %s %s", job_id, partial_note)

            self.client.table("screening_jobs").update(
                {
                    "status": "completed",
                    "candidate_count": len(profiles),
                    "processed_count": core_ok,
                    "error_message": partial_note,
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
                        "optional_warnings": optional_warnings[:8],
                    },
                )
                self._trace(
                    task,
                    make_step(
                        "pipeline.completed",
                        "筛选链路完成",
                        status="completed",
                        detail=f"candidates={core_ok}"
                        + (f" warnings={len(optional_warnings)}" if optional_warnings else ""),
                        ended_at=_now(),
                    ),
                    status="completed",
                    stage="completed",
                )
            except Exception:
                logger.exception("pipeline complete bookkeeping failed job_id=%s", job_id)
            try:
                self._audit(
                    task["workspace_id"],
                    "screening.completed",
                    "screening_job",
                    job_id,
                    {"candidate_count": core_ok, "warnings": len(optional_warnings)},
                )
            except Exception:
                logger.exception("audit screening.completed failed job_id=%s", job_id)
            # False = processed (caller should _complete_task). True = deferred only.
            return False
        finally:
            stop_heartbeat.set()
            if heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=2)
            limiter.clear_deadline()

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

    def _persist_optional_artifacts(
        self, task: dict[str, Any], candidate_profile_id: str, output: Any, review: dict[str, Any]
    ) -> None:
        """Persist optional claims + graded memory (not required for job completed).

        Core rows (match_results / question_packs / checker_reviews) are written
        separately and decide job success/failure.
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
                # Checker pass ≠ human truth. Only escalate to model_checked.
                "status": "model_checked" if review["status"] == "pass" else claim["status"],
                "evidence": claim["evidence"],
                "producer": self.construction_agent.model_name,
                "producer_version": self.settings.agent_mode,
            }
            for claim in output.claims
        ]
        if claims:
            try:
                self.client.table("fact_claims").insert(claims).execute()
            except Exception:
                for row in claims:
                    if row["status"] == "model_checked":
                        row["status"] = "proposed"
                self.client.table("fact_claims").insert(claims).execute()

        # Graded memory: Checker pass → model_checked only (trusted=false).
        if review["status"] == "pass":
            knowledge_points = sorted({question["knowledge_point"] for question in output.questions})
            content = f"已审核题目模式；考点：{', '.join(knowledge_points) or '澄清追问'}"
            embedding = self.embedder.embed(content)
            payload = {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "memory_type": "question",
                "content": content,
                "metadata": {
                    "candidate_profile_id": candidate_profile_id,
                    "checker_status": review["status"],
                    "source": "question_pack",
                    "embedding_source": self.embedder.last_source,
                    "trust_level": "model_checked",
                },
                "source_revision": f"{job_id}:{candidate_profile_id}:v1",
                "trusted": False,
                "embedding": to_pgvector_literal(embedding),
            }
            try:
                payload["trust_level"] = "model_checked"
                self.client.table("agent_memory_chunks").insert(payload).execute()
            except Exception:
                payload.pop("embedding", None)
                payload.pop("trust_level", None)
                try:
                    self.client.table("agent_memory_chunks").insert(payload).execute()
                except Exception:
                    logger.warning("agent_memory_chunks insert skipped job=%s", job_id)

    # Back-compat alias for older call sites / tests.
    def _persist_agent_artifacts(self, *args: Any, **kwargs: Any) -> None:
        return self._persist_optional_artifacts(*args, **kwargs)


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
            raise ValueError("文件无法提取足够的文本，请上传清晰的 PDF、DOC 或 DOCX。")
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
            .select("id,document_id,status,attempts")
            .eq("screening_job_id", job_id)
            .eq("task_type", "parse_resume")
            .execute()
            .data
        ) or []
        for row in resume_tasks:
            if row.get("document_id") not in profiled_docs:
                attempts = int(row.get("attempts") or 0)
                if attempts >= 3:
                    # Already exhausted retries — leave failed rather than violating attempts check.
                    self.client.table("processing_tasks").update(
                        {
                            "status": "failed",
                            "error_message": "requeue_blocked_max_attempts",
                            "completed_at": _now(),
                        }
                    ).eq("id", row["id"]).execute()
                    continue
                self.client.table("processing_tasks").update(
                    {
                        "status": "queued",
                        "available_at": _now(),
                        "completed_at": None,
                        "error_message": "requeue_missing_profile",
                        # Keep attempts; claim_processing_task increments on next claim.
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

    def _complete_task(self, task: dict[str, Any]) -> None:
        if not self.task_lease.complete(task):
            raise RuntimeError(f"task lease no longer owned id={task['id']}")

    def _fail_task(self, task: dict[str, Any], message: str) -> None:
        if not self.task_lease.fail(task, message):
            logger.warning("skip fail update for lost task lease id=%s", task["id"])
            return
        attempts = int(task.get("attempts") or 0)
        if attempts >= 3:
            self.client.table("screening_jobs").update(
                {"status": "failed", "error_message": "部分文件处理失败，请检查任务详情。"}
            ).eq("id", task["screening_job_id"]).execute()
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
        title = (
            _first_match(text, r"【\s*岗位名称\s*】\s*([^\n【]+)")
            or _first_match(text, r"岗位名称[：:]\s*([^\n【]+)")
            or _first_match(text, r"【\s*职位名称\s*】\s*([^\n【]+)")
            or _first_match(text, r"职位名称[：:]\s*([^\n【]+)")
            or "未命名岗位"
        )
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
        years = _estimate_years_experience(text)
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


def _refresh_profile_years(profile: dict[str, Any]) -> dict[str, Any]:
    """Re-estimate years from raw_text when stored years look stale/under-extracted."""
    out = dict(profile or {})
    raw = str(out.get("raw_text") or "")
    if not raw.strip():
        return out
    estimated = _estimate_years_experience(raw)
    current = int(out.get("years_experience") or 0)
    if estimated > current:
        out["years_experience"] = estimated
        out["years_reestimated"] = True
    return out


def _estimate_years_experience(text: str) -> int:
    """Estimate years from explicit phrases and employment date ranges."""
    cn_digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    explicit = 0
    for pattern in (
        r"(\d+)\s*年(?:相关|开发|工作|持续)?经验",
        r"(?:满|约|近)\s*(\d+)\s*年",
        r"工作年限[：:\s]*(\d+)\s*年",
        r"(\d+)\s*年持续",
    ):
        hit = _first_match(text, pattern)
        if hit:
            explicit = max(explicit, int(hit))
    for pattern in (
        r"(?:满|约|近)\s*([一二两三四五六七八九十])\s*年",
        r"([一二两三四五六七八九十])\s*年(?:相关|开发|工作)?经验",
    ):
        hit = _first_match(text, pattern)
        if hit and hit in cn_digits:
            explicit = max(explicit, cn_digits[hit])

    now = datetime.now(timezone.utc)
    ranges: list[tuple[int, int]] = []  # (start_month_index, end_month_index)
    # 2022.07-2025.03 / 2022/07—至今 / 2022年7月-2024年6月
    range_pat = re.compile(
        r"(20\d{2})\s*[.\-/年]\s*(\d{1,2})?\s*(?:月)?\s*[-–—~至到]+\s*"
        r"(?:(20\d{2})\s*[.\-/年]\s*(\d{1,2})?\s*(?:月)?|至今|现在|目前)",
        re.IGNORECASE,
    )
    for match in range_pat.finditer(text):
        y1 = int(match.group(1))
        m1 = int(match.group(2) or 1)
        if match.group(3):
            y2 = int(match.group(3))
            m2 = int(match.group(4) or 12)
        else:
            y2, m2 = now.year, now.month
        start = y1 * 12 + max(1, min(12, m1)) - 1
        end = y2 * 12 + max(1, min(12, m2)) - 1
        if end >= start:
            ranges.append((start, end))

    inferred = 0
    if ranges:
        # Merge overlapping intervals so concurrent jobs don't inflate years.
        ranges.sort()
        merged = [list(ranges[0])]
        for start, end in ranges[1:]:
            if start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        months = sum(end - start + 1 for start, end in merged)
        inferred = max(0, months // 12)

    if explicit <= 0 and inferred <= 0:
        starts = [int(y) for y in re.findall(r"(20\d{2})\s*[.\-/年]", text)]
        if starts:
            inferred = max(0, now.year - min(starts))

    return max(explicit, inferred)


def _split_jd_skills(text: str, title: str) -> tuple[list[str], list[str]]:
    """Split JD skills into must-have vs nice-to-have.

    Keep must-have small and core. Mentions of every tool in a long JD should not
    all become hard gates — overflow goes to nice-to-have.
    """
    found = set(expand_text_skills(text))
    for skill in SUPPORTED_SKILLS:
        if re.search(re.escape(skill), text, re.IGNORECASE):
            found.add(skill)

    must_section = _section_blob(text, r"(?:必备|必须|硬性|任职要求|岗位要求|任职资格)[^\n]{0,8}[：:\n】]")
    nice_section = _section_blob(text, r"(?:加分|优先|加分项|优先条件|更好)[^\n]{0,8}[：:\n】]")
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

    # Never weaken a JD's explicit 必备/必须清单. Cap only inferred skills so
    # broad role heuristics cannot turn an arbitrary technology laundry list into
    # an unreviewable number of hard gates.
    max_inferred_must = 4
    inferred_must = must_have - explicit_must
    if len(inferred_must) > max_inferred_must:
        keep_inferred = set(sorted(inferred_must, key=_rank)[:max_inferred_must])
        overflow = inferred_must - keep_inferred
        must_have = set(explicit_must) | keep_inferred
        nice_to_have |= overflow

    return sorted(must_have, key=_rank), sorted(nice_to_have, key=_rank)


def _section_blob(text: str, header_pattern: str) -> str:
    match = re.search(header_pattern, text, re.IGNORECASE)
    if not match:
        return ""
    start = match.end()
    rest = text[start:]
    # Keep numbered list items inside a section; stop only at the next semantic
    # heading. The prior expression stopped after item 1, causing later explicit
    # requirements and all 【加分项】 skills to be misclassified.
    stop = re.search(r"\n(?:【|加分|优先|福利|岗位职责|工作地点|任职要求|岗位要求)", rest)
    return rest[: stop.start()] if stop else rest[:800]


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _education_rank(value: str | None) -> int:
    return {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}.get(value or "", 0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
