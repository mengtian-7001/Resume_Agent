"""ReAct Construction agent: Plan → Act/Observe → Reflect + LLM Judge scoring."""

from __future__ import annotations

from typing import Any

from .agents import (
    ConstructionOutput,
    MockConstructionAgent,
    _evidence,
    _normalize_followups,
    _normalize_questions,
)
from .matching import match_profile
from .skill_ontology import canonicalize_skills


class OpenAIConstructionAgent:
    """Construction ReAct agent with parallel deterministic + LLM Judge paths."""

    def __init__(self, settings: Any) -> None:
        from .llm_client import client_from_llm_config

        self.settings = settings
        self.fallback = MockConstructionAgent()
        self.client = client_from_llm_config(settings.construction_llm())
        self.model_name = settings.construction_llm().get("model") or "gpt-4o-mini"
        self.max_steps = int(getattr(settings, "agent_max_react_steps", 3) or 3)

    def analyze(
        self,
        requirements: dict[str, Any],
        profile: dict[str, Any],
        *,
        job_context: dict[str, Any] | None = None,
        screening_config: dict[str, Any] | None = None,
    ) -> ConstructionOutput:
        from .llm_client import LLMClientError

        trace: list[dict[str, Any]] = []
        uncertainties = self._initial_uncertainties(requirements, profile)
        trace.append(
            {
                "action": "react_plan",
                "status": "completed",
                "uncertainties": uncertainties,
                "budget": self.max_steps,
                "tool_order": ["score_deterministic", "llm_judge", "generate_questions", "finish"],
            }
        )

        # Act: deterministic controlled tool (cannot be overridden by free text).
        det = match_profile(
            requirements,
            profile,
            screening_config=screening_config,
            job_context=job_context,
            score_llm=None,
        )
        trace.append(
            {
                "action": "act_observe",
                "tool": "score_deterministic",
                "status": "completed",
                "observation": {
                    "score_deterministic": det["score_breakdown"]["score_deterministic"],
                    "missing_required": det.get("missing_required"),
                    "matched_required": det.get("matched_required"),
                    "hard_gate_pass": det.get("hard_gate_pass"),
                },
            }
        )

        need_llm = bool(self.client)
        trace.append(
            {
                "action": "reflect",
                "status": "completed",
                "enough_evidence": False,
                "next": "llm_judge" if need_llm else "generate_questions",
                "reason": "两路并行需要 LLM Judge" if need_llm else "无模型凭据，跳过 Judge",
            }
        )

        score_llm_value: float | None = None
        llm_evidence_rows: list[dict[str, Any]] = []
        llm_judge_payload: dict[str, Any] | None = None
        if need_llm and self.client:
            try:
                judge, chat = self._llm_judge(requirements, profile, det, job_context)
                score_llm_value = float(judge["score_llm"])
                llm_judge_payload = judge
                llm_evidence_rows = list(judge.get("evidence") or [])
                trace.append(
                    {
                        "action": "act_observe",
                        "tool": "llm_judge",
                        "status": "completed",
                        "model": chat.model,
                        "provider": "evolink",
                        "duration_ms": chat.duration_ms,
                        "observation": {
                            "score_llm": score_llm_value,
                            "dimensions": judge.get("dimensions"),
                            "evidence_count": len(llm_evidence_rows),
                        },
                    }
                )
            except Exception as exc:  # noqa: BLE001
                trace.append(
                    {
                        "action": "act_observe",
                        "tool": "llm_judge",
                        "status": "failed",
                        "fallback": "heuristic_proxy",
                        "error": str(exc)[:300],
                        "model": self.model_name,
                    }
                )

        scored = match_profile(
            requirements,
            profile,
            screening_config=screening_config,
            job_context=job_context,
            score_llm=score_llm_value,
        )
        trace.append(
            {
                "action": "reflect",
                "status": "completed",
                "enough_evidence": True,
                "next": "generate_questions",
                "ensemble": {
                    "score_total": scored["score"],
                    "score_llm": scored["score_breakdown"]["score_llm"],
                    "score_deterministic": scored["score_breakdown"]["score_deterministic"],
                    "score_llm_source": scored["score_breakdown"].get("score_llm_source"),
                },
            }
        )

        missing_required = list(scored.get("missing_required") or [])
        matched_required = list(scored.get("matched_required") or [])
        skills = canonicalize_skills(profile.get("skills") or [])
        years = int(profile.get("years_experience") or 0)
        evidence = list(scored.get("evidence") or [])
        for row in llm_evidence_rows:
            if isinstance(row, dict) and row.get("text"):
                evidence.append(
                    {
                        "type": str(row.get("type") or "llm_judge"),
                        "text": str(row["text"]),
                        "source": str(row.get("source") or "llm"),
                    }
                )
        risks = list(scored.get("risks") or [])
        decision = scored["decision"]

        match_result = {
            "score": scored["score"],
            "decision": decision,
            "hard_gate_pass": scored["hard_gate_pass"],
            "score_breakdown": scored["score_breakdown"],
            "evidence": evidence,
            "risks": risks,
            "uncertainty": scored.get("uncertainty", "medium"),
            "job_context": scored.get("job_context")
            or job_context
            or {"mode": "react-construction", "sources": []},
            "missing_required": missing_required,
            "matched_required": matched_required,
            "llm_judge": llm_judge_payload,
        }

        claims = [
            {
                "subject_type": "candidate",
                "predicate": "has_skill",
                "value": skill,
                "confidence": "high",
                "status": "proposed",
                "evidence": [_evidence("skill", f"简历列出技能：{skill}", "resume")],
            }
            for skill in sorted(skills)
        ]
        for skill in matched_required:
            claims.append(
                {
                    "subject_type": "candidate",
                    "predicate": "covers_required_skill",
                    "value": skill,
                    "confidence": "high",
                    "status": "proposed",
                    "evidence": [_evidence("skill", f"同义归一后覆盖必备技能：{skill}", "resume")],
                }
            )
        claims.append(
            {
                "subject_type": "candidate",
                "predicate": "years_experience",
                "value": years,
                "confidence": "medium",
                "status": "proposed",
                "evidence": [evidence[1]]
                if len(evidence) > 1
                else [_evidence("experience", f"{years} 年经验", "resume")],
            }
        )

        expected = 10 if decision == "recommend" else 5 if decision == "review" else 3
        questions, followups = self.fallback._generate_questions(
            requirements,
            profile,
            decision=decision,
            missing_required=missing_required,
            risks=risks,
        )
        if self.client:
            try:
                data, chat = self._llm_questions(
                    requirements, profile, decision, expected, missing_required, risks, job_context
                )
                questions = _normalize_questions(data.get("questions") or [], expected=expected)
                followups = _normalize_followups(data.get("followups") or [], limit=5)
                if len(questions) < max(1, expected // 2):
                    raise LLMClientError(f"too few questions: {len(questions)}")
                if len(questions) < expected:
                    pad, _ = self.fallback._generate_questions(
                        requirements,
                        profile,
                        decision=decision,
                        missing_required=missing_required,
                        risks=risks,
                    )
                    for q in pad:
                        if q["question"] not in {x["question"] for x in questions}:
                            questions.append(q)
                        if len(questions) >= expected:
                            break
                if len(followups) < 2:
                    _, followups = self.fallback._generate_questions(
                        requirements,
                        profile,
                        decision=decision,
                        missing_required=missing_required,
                        risks=risks,
                    )
                trace.append(
                    {
                        "action": "act_observe",
                        "tool": "generate_questions",
                        "status": "completed",
                        "model": chat.model,
                        "provider": "evolink",
                        "duration_ms": chat.duration_ms,
                        "observation": {
                            "question_count": len(questions),
                            "followup_count": len(followups),
                        },
                    }
                )
            except Exception as exc:  # noqa: BLE001
                trace.append(
                    {
                        "action": "act_observe",
                        "tool": "generate_questions",
                        "status": "failed",
                        "fallback": "mock",
                        "error": str(exc)[:300],
                    }
                )

        trace.append(
            {
                "action": "decision_generate",
                "status": "completed",
                "decision": decision,
                "score": match_result["score"],
                "question_count": len(questions),
            }
        )
        return ConstructionOutput(match_result, questions[:expected], followups[:5], claims, trace)

    @staticmethod
    def _initial_uncertainties(requirements: dict[str, Any], profile: dict[str, Any]) -> list[str]:
        items: list[str] = []
        must = set(requirements.get("must_have_skills") or [])
        have = set(profile.get("skills") or [])
        missing = sorted(must - have)
        if missing:
            items.append(f"missing_skills:{','.join(missing[:5])}")
        min_years = int(requirements.get("min_years") or 0)
        years = int(profile.get("years_experience") or 0)
        if min_years and years < min_years:
            items.append(f"years_gap:{years}<{min_years}")
        if not str(profile.get("raw_text") or "").strip():
            items.append("thin_resume_text")
        return items

    def _llm_judge(
        self,
        requirements: dict[str, Any],
        profile: dict[str, Any],
        det: dict[str, Any],
        job_context: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], Any]:
        import json as _json

        system = (
            "你是招聘匹配的 LLM Judge。只负责语境与可迁移能力判断；"
            "禁止把自由文本理由当作唯一事实来源；硬门槛由确定性工具决定，你不能推翻。"
            "每一项打分必须引用 JD 或简历中的原句证据。"
            "只输出 JSON："
            '{"score_llm":0-100,'
            '"dimensions":{"skills":0-100,"experience":0-100,"project_relevance":0-100,"risk":0-100},'
            '"evidence":[{"type":"skills|experience|project|risk","text":"引用原句","source":"jd|resume"}],'
            '"rationale":"一句话"}'
        )
        user = {
            "title": requirements.get("title"),
            "must_have_skills": requirements.get("must_have_skills"),
            "nice_to_have_skills": requirements.get("nice_to_have_skills"),
            "min_years": requirements.get("min_years"),
            "education": requirements.get("education"),
            "deterministic_anchor": {
                "score_deterministic": det["score_breakdown"]["score_deterministic"],
                "required_coverage": det["score_breakdown"]["required_coverage"],
                "missing_required": det.get("missing_required"),
                "hard_gate_pass": det.get("hard_gate_pass"),
                "risks": det.get("risks"),
            },
            "candidate": {
                "name": profile.get("name"),
                "years_experience": profile.get("years_experience"),
                "education": profile.get("education"),
                "skills": profile.get("skills"),
                "raw_text_excerpt": str(profile.get("raw_text") or "")[:2200],
            },
            "jd_excerpt": str(requirements.get("raw_text") or "")[:1600],
            "job_context_mode": (job_context or {}).get("mode"),
        }
        data, chat = self.client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": _json.dumps(user, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=1600,
        )
        score = max(0.0, min(100.0, float(data.get("score_llm"))))
        dims = data.get("dimensions") if isinstance(data.get("dimensions"), dict) else {}
        evidence = data.get("evidence") if isinstance(data.get("evidence"), list) else []
        return (
            {
                "score_llm": score,
                "dimensions": {
                    "skills": float(dims.get("skills") or score),
                    "experience": float(dims.get("experience") or score),
                    "project_relevance": float(dims.get("project_relevance") or score),
                    "risk": float(dims.get("risk") or score),
                },
                "evidence": evidence[:8],
                "rationale": str(data.get("rationale") or "")[:300],
                "model": chat.model,
            },
            chat,
        )

    def _llm_questions(
        self,
        requirements: dict[str, Any],
        profile: dict[str, Any],
        decision: str,
        expected: int,
        missing: list[str],
        risks: list[str],
        job_context: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], Any]:
        import json as _json

        system = (
            "你是招聘面试出题助手。根据 JD 与候选人画像生成结构化面试题。"
            "只输出 JSON。题干必须互不相同，优先考察岗位必备技能，"
            "不要把通用语言（如 Python）当作唯一考点。"
            "JSON schema: "
            '{"questions":[{"id":"Q01","question":"...","knowledge_point":"...","difficulty":"easy|medium|hard",'
            '"scoring_rubric":"..."}],'
            '"followups":[{"question":"...","target":"...","evidence_required":true}]}'
        )
        user = {
            "title": requirements.get("title"),
            "must_have_skills": requirements.get("must_have_skills"),
            "nice_to_have_skills": requirements.get("nice_to_have_skills"),
            "missing_required": missing,
            "decision": decision,
            "risks": risks,
            "candidate": {
                "name": profile.get("name"),
                "years_experience": profile.get("years_experience"),
                "education": profile.get("education"),
                "skills": profile.get("skills"),
                "raw_text_excerpt": str(profile.get("raw_text") or "")[:1800],
            },
            "question_count": expected,
            "followup_count": 3 if decision == "recommend" else 2,
            "job_context_mode": (job_context or {}).get("mode"),
        }
        return self.client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": _json.dumps(user, ensure_ascii=False)},
            ],
            temperature=0.5,
            max_tokens=2800,
        )
