"""Offline-first Construction and Checker agents.

The production model adapter can replace these deterministic mock agents without
changing the worker contract. Every output is grounded in an evidence record so
the same schema works for mock and real LLM runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .matching import match_profile
from .skill_ontology import canonicalize_skills


def _evidence(kind: str, text: str, source: str) -> dict[str, str]:
    return {"type": kind, "text": text, "source": source}


@dataclass(frozen=True)
class ConstructionOutput:
    match_result: dict[str, Any]
    questions: list[dict[str, Any]]
    followups: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    trace: list[dict[str, Any]]


class MockConstructionAgent:
    """Deterministic stand-in for the Construction ReAct agent.

    Scoring follows a HireLens/ResumeIQ-style hybrid matcher:
    skill ontology + lexical overlap + evidence quality, still offline-first.
    """

    model_name = "mock-construction-v1"

    def analyze(
        self,
        requirements: dict[str, Any],
        profile: dict[str, Any],
        *,
        job_context: dict[str, Any] | None = None,
        screening_config: dict[str, Any] | None = None,
    ) -> ConstructionOutput:
        scored = match_profile(
            requirements,
            profile,
            screening_config=screening_config,
            job_context=job_context,
        )
        missing_required = list(scored.get("missing_required") or [])
        matched_required = list(scored.get("matched_required") or [])
        skills = canonicalize_skills(profile.get("skills") or [])
        years = int(profile.get("years_experience") or 0)
        evidence = list(scored.get("evidence") or [])
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
            "job_context": scored.get("job_context") or job_context or {"mode": "hybrid-matcher", "sources": []},
            "missing_required": missing_required,
            "matched_required": matched_required,
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
                "evidence": [evidence[1]] if len(evidence) > 1 else [_evidence("experience", f"{years} 年经验", "resume")],
            }
        )

        questions, followups = self._generate_questions(
            requirements, profile, decision=decision, missing_required=missing_required, risks=risks
        )
        trace = [
            {
                "action": "react_plan",
                "status": "completed",
                "uncertainties": ([f"missing_skills:{','.join(missing_required)}"] if missing_required else [])
                + (["years_gap"] if years < int(requirements.get("min_years") or 0) else []),
                "mode": "mock",
            },
            {"action": "act_observe", "tool": "score_deterministic", "status": "completed", "model": self.model_name},
            {
                "action": "reflect",
                "status": "completed",
                "enough_evidence": True,
                "next": "generate_questions",
                "ensemble": {
                    "score_total": scored["score"],
                    "score_llm": scored["score_breakdown"].get("score_llm"),
                    "score_deterministic": scored["score_breakdown"].get("score_deterministic"),
                    "score_llm_source": scored["score_breakdown"].get("score_llm_source"),
                },
            },
            {"action": "act_observe", "tool": "generate_questions", "status": "completed"},
            {"action": "decision_generate", "status": "completed", "decision": decision, "score": scored["score"]},
        ]
        return ConstructionOutput(match_result, questions, followups, claims, trace)

    @staticmethod
    def _generate_questions(
        requirements: dict[str, Any],
        profile: dict[str, Any],
        *,
        decision: str,
        missing_required: list[str],
        risks: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        must_skills = list(requirements.get("must_have_skills", []) or [])
        nice_skills = list(requirements.get("nice_to_have_skills", []) or [])
        title = str(requirements.get("title") or "本岗位")
        candidate_name = profile.get("name") or "候选人"
        years = int(profile.get("years_experience") or 0)
        min_years = int(requirements.get("min_years") or 0)

        # Prefer domain / missing skills for probing; keep general languages secondary.
        focus_skills: list[str] = []
        for skill in list(missing_required) + must_skills + nice_skills:
            if skill and skill not in focus_skills:
                focus_skills.append(skill)
        if not focus_skills:
            focus_skills = ["核心岗位能力"]

        followups: list[dict[str, Any]] = []
        for skill in focus_skills[:3]:
            followups.append(
                {
                    "question": f"请结合简历中的一个项目说明你如何应用 {skill}，以及结果如何衡量？",
                    "target": skill,
                    "evidence_required": True,
                }
            )
        if missing_required:
            followups.append(
                {
                    "question": f"简历未体现 {missing_required[0]}。请说明是否有相关可迁移经验或学习计划？",
                    "target": missing_required[0],
                    "evidence_required": True,
                }
            )
        if years < min_years and min_years:
            followups.append(
                {
                    "question": f"岗位要求约 {min_years} 年经验，你目前简历显示约 {years} 年。请用 1–2 个项目证明你能独立扛起同级工作量。",
                    "target": "experience_gap",
                    "evidence_required": True,
                }
            )
        if risks:
            followups.append(
                {
                    "question": f"针对“{risks[0]}”，请补充可以验证该项能力的具体经历。",
                    "target": "risk_clarification",
                    "evidence_required": True,
                }
            )
        # Deduplicate followups by question text.
        seen_fu: set[str] = set()
        unique_followups: list[dict[str, Any]] = []
        for item in followups:
            text = str(item.get("question") or "")
            if text and text not in seen_fu:
                seen_fu.add(text)
                unique_followups.append(item)
        followups = unique_followups[:5]

        question_count = 10 if decision == "recommend" else 5 if decision == "review" else 3
        stems_by_decision = {
            "recommend": [
                "{name}，请复盘一个与「{skill}」相关的项目：背景、你的动作、可量化结果分别是什么？",
                "如果让你为本岗位设计一版「{skill}」落地方案，你会如何拆阶段、设验收标准？",
                "围绕「{skill}」，请讲一次你处理过的故障/返工：根因、止损与长期改进。",
                "请对比你用过的两种「{skill}」相关方案，说明取舍理由与适用边界。",
                "假设业务方临时加需求，你会如何在保证「{skill}」质量的前提下做优先级裁剪？",
                "请说明你在「{skill}」上的监控/校验手段：如何发现坏数据或性能回退？",
                "结合 {title}，你认为「{skill}」最容易踩的三个坑是什么？你会怎么预防？",
                "请用一个具体例子说明你如何把「{skill}」成果交付给下游使用方。",
                "若线上「{skill}」链路延迟翻倍，你的排查顺序与止损策略是什么？",
                "请描述一次你推动「{skill}」规范/复用的经历，以及它带来的协作收益。",
            ],
            "review": [
                "简历对「{skill}」着墨不多。请用一个具体项目说明你是否做过同类能力（做什么、怎么验证）。",
                "请针对「{skill}」补充一段可验证经历：你做了什么、如何衡量结果、边界在哪里？",
                "如果试用期要证明你具备「{skill}」，你会选哪两个里程碑交付物？",
                "围绕 {title}，请说明「{skill}」在你过往工作中的真实参与深度（负责/协助/了解）。",
                "请讲一次与「{skill}」相关的协作冲突或需求变更，你如何对齐目标并收口。",
            ],
            "reject": [
                "当前匹配偏弱。若补齐「{skill}」，你认为 30/60/90 天最短可验证路径是什么？",
                "请诚实说明你与 {title} 在「{skill}」上的真实差距，以及能否用项目证据缩小它。",
                "假设你仍想争取该方向，请设计一个最小实验来证明你具备「{skill}」相关潜力。",
                "针对年限/能力差距，请用一个高难度任务说明你曾如何快速追上团队节奏。",
                "请选择一个与「{skill}」相邻的可迁移经验，解释它为何能支撑本岗位核心工作。",
            ],
        }
        stems = stems_by_decision.get(decision) or stems_by_decision["review"]

        # Topic rotation: experience gap, missing skills, must-haves — avoid one skill monopolizing.
        topics: list[tuple[str, str]] = []
        if years < min_years and min_years:
            topics.append(("experience_gap", f"工作年限（简历 {years} 年 / 要求 {min_years} 年）"))
        for skill in missing_required:
            topics.append((skill, skill))
        for skill in must_skills:
            topics.append((skill, skill))
        for skill in nice_skills:
            topics.append((skill, skill))
        if not topics:
            topics = [("核心岗位能力", "核心岗位能力")]

        questions: list[dict[str, Any]] = []
        seen_questions: set[str] = set()
        topic_index = 0
        stem_index = 0
        guard = 0
        while len(questions) < question_count and guard < question_count * 4:
            guard += 1
            knowledge, label = topics[topic_index % len(topics)]
            topic_index += 1
            stem = stems[stem_index % len(stems)]
            stem_index += 1
            prompt = stem.format(name=candidate_name, skill=label, title=title)
            if prompt in seen_questions:
                continue
            seen_questions.add(prompt)
            questions.append(
                {
                    "id": f"Q{len(questions) + 1:02d}",
                    "question": prompt,
                    "knowledge_point": knowledge,
                    "difficulty": ["easy", "medium", "hard"][len(questions) % 3],
                    "scoring_rubric": "问题拆解 30%，技术方案 40%，边界与验证 30%",
                }
            )
        return questions, followups


class MockCheckerAgent:
    """Quality gate for mock and future model-backed Construction outputs."""

    model_name = "mock-checker-v1"

    def review(self, output: ConstructionOutput) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        result = output.match_result
        if not result.get("evidence"):
            issues.append({"issue_type": "missing_evidence", "target_skill": "match", "severity": "high"})
        if result["decision"] == "recommend" and len(output.questions) < 10:
            issues.append({"issue_type": "insufficient_questions", "target_skill": "question_react", "severity": "high"})
        if result["decision"] == "recommend" and len(output.followups) < 3:
            issues.append({"issue_type": "insufficient_followups", "target_skill": "question_react", "severity": "medium"})
        for question in output.questions:
            if not question.get("scoring_rubric") or not question.get("knowledge_point"):
                issues.append({"issue_type": "invalid_question_schema", "target_skill": "question_react", "severity": "high"})
                break
        return {
            "status": "pass" if not issues else "fail",
            "issues": issues,
            "model": self.model_name,
            "reviewed_at": "mock",
        }


def _normalize_questions(raw: list[Any], *, expected: int) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("question") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        questions.append(
            {
                "id": str(item.get("id") or f"Q{len(questions) + 1:02d}"),
                "question": text,
                "knowledge_point": str(item.get("knowledge_point") or item.get("topic") or "核心岗位能力"),
                "difficulty": str(item.get("difficulty") or ["easy", "medium", "hard"][len(questions) % 3]),
                "scoring_rubric": str(
                    item.get("scoring_rubric") or "问题拆解 30%，技术方案 40%，边界与验证 30%"
                ),
            }
        )
        if len(questions) >= expected:
            break
    return questions


def _normalize_followups(raw: list[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    followups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("question") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        followups.append(
            {
                "question": text,
                "target": str(item.get("target") or item.get("knowledge_point") or "followup"),
                "evidence_required": bool(item.get("evidence_required", True)),
            }
        )
        if len(followups) >= limit:
            break
    return followups


class OpenAICheckerAgent:
    """LLM quality gate with deterministic mock fallback."""

    def __init__(self, settings: Any) -> None:
        from .llm_client import client_from_llm_config

        self.settings = settings
        self.fallback = MockCheckerAgent()
        self.client = client_from_llm_config(settings.checker_llm())
        self.model_name = (settings.checker_llm().get("model") or "gpt-4o-mini")

    def review(self, output: ConstructionOutput) -> dict[str, Any]:
        base = self.fallback.review(output)
        if not self.client:
            return {**base, "fallback": "mock", "reason": "missing_credentials"}

        system = (
            "你是面试题质检员。检查题目是否重复、是否偏离岗位必备技能、rubric 是否完整。"
            "只输出 JSON："
            '{"status":"pass"|"fail","issues":[{"issue_type":"...","target_skill":"...","severity":"high|medium|low","note":"..."}]}'
        )
        import json as _json

        payload = {
            "decision": output.match_result.get("decision"),
            "title_hint": (output.match_result.get("job_context") or {}),
            "questions": output.questions,
            "followups": output.followups,
            "risks": output.match_result.get("risks"),
        }
        try:
            data, chat = self.client.chat_json(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.1,
                max_tokens=1200,
            )
            status = str(data.get("status") or "fail")
            if status not in {"pass", "fail"}:
                status = "fail"
            issues = []
            for item in data.get("issues") or []:
                if isinstance(item, dict):
                    issues.append(
                        {
                            "issue_type": str(item.get("issue_type") or "quality"),
                            "target_skill": str(item.get("target_skill") or "question_react"),
                            "severity": str(item.get("severity") or "medium"),
                            "note": str(item.get("note") or ""),
                        }
                    )
            # Merge hard schema failures from mock.
            if base["status"] == "fail":
                for issue in base["issues"]:
                    if issue not in issues:
                        issues.append(issue)
                status = "fail"
            return {
                "status": status,
                "issues": issues,
                "model": chat.model,
                "provider": "evolink",
                "duration_ms": chat.duration_ms,
                "reviewed_at": "openai",
            }
        except Exception as exc:  # noqa: BLE001
            return {**base, "fallback": "mock", "error": str(exc)[:300], "model": self.model_name}


from .react_construction import OpenAIConstructionAgent  # noqa: E402,F401
