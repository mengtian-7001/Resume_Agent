from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import fitz
from docx import Document as DocxDocument
from supabase import Client, create_client

from .agents import MockCheckerAgent, MockConstructionAgent
from .config import get_settings
from .fact_graph import build_fact_graph
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
        self.construction_agent = MockConstructionAgent()
        self.checker_agent = MockCheckerAgent()
        self.fact_graph = build_fact_graph(settings)
        self.job_research = JobResearchService(settings)

    def run_once(self) -> dict[str, Any]:
        task = self._claim_task()
        if not task:
            return {"processed": False, "reason": "queue_empty"}

        try:
            if task["task_type"] == "parse_jd":
                self._parse_jd(task)
            elif task["task_type"] == "parse_resume":
                self._parse_resume(task)
            elif task["task_type"] == "match":
                self._match_candidate(task)
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
        document = self._get_document(task["document_id"])
        text = self._extract_and_store(document)
        requirements = self._extract_requirements(text)
        requirements["job_context"] = self.job_research.research(requirements)
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
        self._upsert_agent_run(task, status="running", state={"jd_requirements": requirements})
        self.fact_graph.upsert_job(task["workspace_id"], task["screening_job_id"], requirements)
        self._enqueue_resume_tasks(task["screening_job_id"], task["workspace_id"])

    def _parse_resume(self, task: dict[str, Any]) -> None:
        document = self._get_document(task["document_id"])
        text = self._extract_and_store(document)
        profile = self._extract_profile(text)
        self.client.table("candidate_profiles").upsert(
            {
                "screening_job_id": task["screening_job_id"],
                "source_document_id": document["id"],
                "display_name": profile["name"],
                "profile": profile,
            },
            on_conflict="source_document_id",
        ).execute()

    def _match_candidate(self, task: dict[str, Any]) -> None:
        job_id = task["screening_job_id"]
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
        )
        self._upsert_agent_run(task, status="running", state={"stage": "candidate_analysis"})
        for candidate in profiles:
            output = self.construction_agent.analyze(
                requirement["requirements"],
                candidate["profile"],
                job_context=requirement["requirements"].get("job_context"),
            )
            review = self.checker_agent.review(output)
            result = output.match_result
            self.client.table("match_results").upsert(
                {
                    "screening_job_id": job_id,
                    "candidate_profile_id": candidate["id"],
                    **result,
                    "interview_question": output.followups[0]["question"] if output.followups else None,
                },
                on_conflict="candidate_profile_id",
            ).execute()
            self._persist_agent_artifacts(task, candidate["id"], output, review)
            self.fact_graph.upsert_candidate(
                task["workspace_id"], job_id, candidate["id"], candidate["profile"], output.claims
            )
            self.fact_graph.record_match(task["workspace_id"], job_id, candidate["id"], requirement["requirements"], result)
            self.fact_graph.record_review(task["workspace_id"], job_id, candidate["id"], review)

        self.client.table("screening_jobs").update(
            {
                "status": "completed",
                "candidate_count": len(profiles),
                "processed_count": len(profiles),
                "error_message": None,
            }
        ).eq("id", job_id).execute()
        self._upsert_agent_run(
            task,
            status="completed",
            state={"stage": "completed", "candidate_count": len(profiles), "agent_mode": self.settings.agent_mode},
        )
        self._audit(task["workspace_id"], "screening.completed", "screening_job", job_id, {"candidate_count": len(profiles)})

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
        )
        payload = [
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "document_id": resume["id"],
                "dedupe_key": f"parse_resume:{resume['id']}",
                "task_type": "parse_resume",
            }
            for resume in resumes
        ]
        if payload:
            self.client.table("processing_tasks").upsert(
                payload, on_conflict="dedupe_key"
            ).execute()

    def _enqueue_match_task_if_ready(self, job_id: str, workspace_id: str) -> None:
        remaining = (
            self.client.table("processing_tasks")
            .select("id", count="exact")
            .eq("screening_job_id", job_id)
            .eq("task_type", "parse_resume")
            .neq("status", "completed")
            .execute()
        )
        if remaining.count == 0:
            self.client.table("processing_tasks").upsert(
                {
                    "workspace_id": workspace_id,
                    "screening_job_id": job_id,
                    "dedupe_key": f"match:{job_id}",
                    "task_type": "match",
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
        skills = [skill for skill in SUPPORTED_SKILLS if re.search(re.escape(skill), text, re.IGNORECASE)]
        years = int(_first_match(text, r"(\d+)\s*年(?:及以上|以上).{0,12}(?:经验|开发)") or 0)
        education = _first_match(text, r"(本科|硕士|博士|大专)(?:及以上)?") or None
        return {
            "title": title.strip(),
            "must_have_skills": skills,
            "min_years": years,
            "education": education,
            "hard_gates": [
                {"field": "min_years", "op": ">=", "value": years},
                {"field": "education", "op": ">=", "value": education},
                {"field": "must_have_skills", "op": "covers_all", "value": skills},
            ],
        }

    @staticmethod
    def _extract_profile(text: str) -> dict[str, Any]:
        name = _first_match(text, r"姓名[：:]\s*([^\n\s]+)") or "未命名候选人"
        years = int(_first_match(text, r"(\d+)\s*年(?:相关|开发|工作)?经验") or 0)
        education = _first_match(text, r"(本科|硕士|博士|大专)") or None
        skills = [skill for skill in SUPPORTED_SKILLS if re.search(re.escape(skill), text, re.IGNORECASE)]
        return {"name": name.strip(), "years_experience": years, "education": education, "skills": skills}

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


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _education_rank(value: str | None) -> int:
    return {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}.get(value or "", 0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
