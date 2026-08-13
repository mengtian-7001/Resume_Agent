"""Offline-first Construction and Checker agents.

The production model adapter can replace these deterministic mock agents without
changing the worker contract. Every output is grounded in an evidence record so
the same schema works for mock and real LLM runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _education_rank(value: str | None) -> int:
    return {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}.get(value or "", 0)


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

    It deliberately follows the same contract expected from an LLM-backed
    implementation: proposed claims, grounded scores, Question ReAct output,
    and a trace of bounded actions.
    """

    model_name = "mock-construction-v1"

    def analyze(
        self,
        requirements: dict[str, Any],
        profile: dict[str, Any],
        *,
        job_context: dict[str, Any] | None = None,
    ) -> ConstructionOutput:
        required = set(requirements.get("must_have_skills", []))
        preferred = set(requirements.get("nice_to_have_skills", []))
        skills = set(profile.get("skills", []))
        matched_required = sorted(required & skills)
        missing_required = sorted(required - skills)
        matched_preferred = sorted(preferred & skills)

        required_score = len(matched_required) / len(required) if required else 1.0
        preferred_score = len(matched_preferred) / len(preferred) if preferred else 1.0
        skill_score = 100 * (0.70 * required_score + 0.30 * preferred_score)

        min_years = int(requirements.get("min_years") or 0)
        years = int(profile.get("years_experience") or 0)
        experience_score = 100 if years >= min_years else round(100 * years / max(min_years, 1), 2)
        education_score = (
            100
            if _education_rank(profile.get("education")) >= _education_rank(requirements.get("education"))
            else 0
        )
        # Mock semantic/keyword score is intentionally deterministic. A real
        # adapter replaces it with embeddings + TF-IDF while preserving fields.
        text_score = round(100 * (0.65 * required_score + 0.35 * preferred_score), 2)
        deterministic = round(
            0.40 * skill_score + 0.20 * experience_score + 0.10 * education_score + 0.30 * text_score,
            2,
        )
        llm_score = round(
            0.45 * skill_score + 0.30 * experience_score + 0.15 * education_score + 0.10 * text_score,
            2,
        )
        total = round(0.60 * llm_score + 0.40 * deterministic, 2)

        years_ok = years >= min_years
        education_ok = _education_rank(profile.get("education")) >= _education_rank(requirements.get("education"))
        hard_gate_pass = years_ok and education_ok
        uncertainty = "low" if required_score >= 0.75 and years_ok else "medium" if hard_gate_pass else "high"
        decision = "recommend" if hard_gate_pass and total >= 75 else "review" if hard_gate_pass else "reject"

        evidence = [
            _evidence("skills", f"匹配必备技能：{', '.join(matched_required) or '无'}", "resume"),
            _evidence("experience", f"候选人 {years} 年经验，岗位要求 {min_years} 年", "resume"),
            _evidence("education", f"候选人学历：{profile.get('education') or '未提及'}", "resume"),
        ]
        risks: list[str] = []
        if not years_ok:
            risks.append("未满足最低工作年限")
        if not education_ok:
            risks.append("未满足最低学历要求")
        if missing_required:
            risks.append(f"缺少必备技能：{', '.join(missing_required)}")

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
        claims.append(
            {
                "subject_type": "candidate",
                "predicate": "years_experience",
                "value": years,
                "confidence": "medium",
                "status": "proposed",
                "evidence": [evidence[1]],
            }
        )

        match_result = {
            "score": total,
            "decision": decision,
            "hard_gate_pass": hard_gate_pass,
            "score_breakdown": {
                "score_llm": llm_score,
                "score_deterministic": deterministic,
                "skill": round(skill_score, 2),
                "experience": experience_score,
                "education": education_score,
                "text": text_score,
                "required_coverage": round(required_score * 100, 2),
                "preferred_coverage": round(preferred_score * 100, 2),
            },
            "evidence": evidence,
            "risks": risks,
            "uncertainty": uncertainty,
            "job_context": job_context or {"mode": "mock", "sources": []},
        }
        questions, followups = self._generate_questions(
            requirements, profile, decision=decision, missing_required=missing_required, risks=risks
        )
        trace = [
            {"action": "inspect_jd", "status": "completed"},
            {"action": "inspect_profile", "status": "completed"},
            {"action": "score_deterministic", "status": "completed"},
            {"action": "judge_match", "status": "completed", "model": self.model_name},
            {"action": "question_react", "status": "completed" if decision == "recommend" else "skipped"},
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
        skills = requirements.get("must_have_skills", []) or ["核心岗位能力"]
        candidate_name = profile.get("name") or "候选人"
        followups = [
            {
                "question": f"请结合简历中的一个项目说明你如何应用 {skill}，以及结果如何衡量？",
                "target": skill,
                "evidence_required": True,
            }
            for skill in skills[:3]
        ]
        if missing_required:
            followups.append(
                {
                    "question": f"简历未体现 {missing_required[0]}。请说明是否有相关可迁移经验或学习计划？",
                    "target": missing_required[0],
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
        followups = followups[:5]
        if decision != "recommend":
            return [], followups[:5]

        questions: list[dict[str, Any]] = []
        for index in range(10):
            skill = skills[index % len(skills)]
            questions.append(
                {
                    "id": f"Q{index + 1:02d}",
                    "question": f"{candidate_name}，请设计或复盘一个使用 {skill} 解决岗位相关问题的方案。",
                    "knowledge_point": skill,
                    "difficulty": ["easy", "medium", "hard"][index % 3],
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
