from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.agents import MockCheckerAgent, MockConstructionAgent
from app.checker_contract import CheckerInputBuilder
from app.checker_corrections import apply_checker_corrections
from app.checker_harness import run_checker_harness
from app.evidence_registry import build_match_evidence, quote_in_source
from app.memory_recall import (
    fetch_scoped_feedback_memories,
    filter_memory_hits,
    is_trusted_for_scoring,
    memory_from_feedback,
    memory_is_usable,
    scoped_feedback_memories,
)
from app.worker import ScreeningWorker


class _FakeExecute:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeFeedbackQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)
        self._eq: list[tuple[str, object]] = []
        self._ilike: tuple[str, str] | None = None
        self._limit: int | None = None

    def eq(self, column: str, value: object) -> "_FakeFeedbackQuery":
        self._eq.append((column, value))
        return self

    def ilike(self, column: str, pattern: str) -> "_FakeFeedbackQuery":
        self._ilike = (column, pattern)
        return self

    def order(self, *_args: object, **_kwargs: object) -> "_FakeFeedbackQuery":
        return self

    def limit(self, count: int) -> "_FakeFeedbackQuery":
        self._limit = count
        return self

    def execute(self) -> _FakeExecute:
        rows = self._rows
        for column, value in self._eq:
            rows = [row for row in rows if str(row.get(column)) == str(value)]
        if self._ilike:
            column, pattern = self._ilike
            needle = pattern.strip("%").lower()
            rows = [row for row in rows if needle in str(row.get(column) or "").lower()]
        rows = sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _FakeExecute(rows)


class _FakeFeedbackTable:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def select(self, *_args: object, **_kwargs: object) -> _FakeFeedbackQuery:
        return _FakeFeedbackQuery(self._rows)


class _FakeFeedbackClient:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def table(self, name: str) -> _FakeFeedbackTable:
        assert name == "recruiter_feedback"
        return _FakeFeedbackTable(self._rows)


class GroundedEvidenceTests(unittest.TestCase):
    def test_match_evidence_uses_locatable_quotes(self) -> None:
        profile = {
            "raw_text": "2021.07—至今，负责智能客服平台后端开发。使用 FastAPI 上线生产服务。",
            "years_experience": 4,
            "education": "本科",
            "skills": ["FastAPI"],
        }
        rows = build_match_evidence(
            requirements={"raw_text": "需要 FastAPI", "title": "后端"},
            profile=profile,
            matched_required=["FastAPI"],
            matched_preferred=[],
            missing_required=[],
            years=4,
            min_years=3,
            production_cues=["上线", "生产"],
        )
        grounded = [row for row in rows if row.get("evidence_id")]
        self.assertGreaterEqual(len(grounded), 2)
        for row in grounded:
            self.assertTrue(quote_in_source(row["quote"], profile["raw_text"]))

    def test_ungrounded_citation_is_not_pass(self) -> None:
        requirements = {
            "title": "AI Agent 工程师",
            "must_have_skills": ["Python", "LangGraph", "FastAPI"],
            "nice_to_have_skills": ["MCP"],
            "min_years": 3,
            "education": "本科",
            "raw_text": "需要 Python LangGraph FastAPI",
        }
        profile = {
            "name": "虚假引用",
            "years_experience": 4,
            "education": "本科",
            "skills": ["Python", "LangGraph", "FastAPI"],
            "raw_text": "负责生产环境 Python 服务两年。",
        }
        output = MockConstructionAgent().analyze(requirements, profile)
        output.match_result["decision"] = "recommend"
        output.match_result["evidence"] = [
            {
                "evidence_id": "EV-999",
                "source": "resume",
                "quote": "这段话简历里根本没有",
                "text": "这段话简历里根本没有",
            }
        ]
        review = MockCheckerAgent().review(CheckerInputBuilder.build(output, requirements, profile))
        self.assertNotEqual(review["status"], "pass")
        self.assertTrue(any(item["issue_type"] == "ungrounded_citation" for item in review["issues"]))


class StructuredPatchTests(unittest.TestCase):
    def test_cap_and_remove_claim_patches(self) -> None:
        match_result = {
            "score": 88,
            "decision": "recommend",
            "hard_gate_pass": True,
            "uncertainty": "low",
            "risks": [],
            "score_breakdown": {"evidence": 88, "production": 88},
        }
        claims = [{"id": "CL-004", "predicate": "has_skill", "value": "K8s", "confidence": "high"}]
        questions: list[dict] = []
        correction = apply_checker_corrections(
            match_result,
            claims,
            [
                {
                    "issue_type": "unsupported_score",
                    "recommended_action": "cap",
                    "target": "score_breakdown.evidence",
                    "recommended_value": 55,
                    "note": "原文只有预研",
                },
                {
                    "issue_type": "unsupported_claim",
                    "recommended_action": "remove",
                    "target": "claims.CL-004",
                },
                {
                    "issue_type": "missing_question",
                    "recommended_action": "add",
                    "target": "questions",
                    "topic": "生产部署与故障恢复",
                },
            ],
            questions,
        )
        self.assertLess(match_result["score"], 88)
        self.assertEqual(match_result["score_breakdown"]["evidence"], 55)
        self.assertEqual(match_result["decision"], "reject")
        self.assertFalse(any(claim.get("id") == "CL-004" for claim in claims))
        self.assertTrue(any("生产部署" in str(item.get("question") or "") for item in questions))
        self.assertTrue(correction["applied"])

    def test_capped_score_is_rebanded_by_decide(self) -> None:
        match_result = {
            "score": 82,
            "decision": "recommend",
            "hard_gate_pass": True,
            "uncertainty": "low",
            "risks": [],
            "score_breakdown": {"evidence": 82},
            "screening_config": {"score_thresholds": {"recommend_min": 75, "review_min": 60}},
        }
        apply_checker_corrections(
            match_result,
            [],
            [{"issue_type": "unsupported_score", "recommended_action": "cap", "target": "score", "recommended_value": 55}],
        )
        self.assertEqual(match_result["score"], 55)
        self.assertEqual(match_result["decision"], "reject")


class MemoryRecallTests(unittest.TestCase):
    def test_revoked_expired_and_untrusted_are_dropped(self) -> None:
        now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        rows = [
            {"content": "ok", "trust_level": "human_verified"},
            {"content": "soft", "trust_level": "model_checked"},
            {"content": "revoked", "trust_level": "revoked"},
            {"content": "expired-flag", "trust_level": "expired"},
            {
                "content": "stale",
                "trust_level": "human_verified",
                "expires_at": (now - timedelta(days=1)).isoformat(),
            },
            {"content": "legacy-trusted", "trusted": True, "trust_level": "untrusted"},
        ]
        usable = filter_memory_hits(rows, now=now)
        self.assertEqual({row["content"] for row in usable}, {"ok", "soft"})
        self.assertTrue(is_trusted_for_scoring(usable[0]))
        self.assertFalse(is_trusted_for_scoring({"trust_level": "model_checked"}))
        self.assertFalse(memory_is_usable({"trust_level": "revoked"}))

    def test_same_job_decision_does_not_pollute_other_candidates(self) -> None:
        rows = [
            {
                "screening_job_id": "job-a",
                "candidate_profile_id": "cand-1",
                "job_title": "AI Agent 工程师",
                "skills": ["Python", "LangGraph"],
                "feedback_type": "decision",
                "value": "too_high",
                "comment": "这个人分数偏高",
            },
            {
                "screening_job_id": "job-a",
                "candidate_profile_id": "cand-1",
                "job_title": "AI Agent 工程师",
                "skills": ["Python"],
                "feedback_type": "evidence",
                "value": "confirmed",
            },
            {
                "screening_job_id": "job-a",
                "candidate_profile_id": "cand-1",
                "job_title": "AI Agent 工程师",
                "skills": ["Python"],
                "feedback_type": "candidate_status",
                "value": "entered_interview",
            },
        ]
        leaked = scoped_feedback_memories(
            rows,
            job_id="job-a",
            candidate_id="cand-2",
            job_title="AI Agent 工程师",
            skills=["Python", "FastAPI"],
        )
        self.assertEqual(leaked, [])
        own = scoped_feedback_memories(
            rows,
            job_id="job-a",
            candidate_id="cand-1",
            job_title="AI Agent 工程师",
            skills=["Python"],
        )
        self.assertEqual(len(own), 3)
        types = {item["memory_type"]: item for item in own}
        self.assertIn("evidence_confirmation", types)
        self.assertTrue(is_trusted_for_scoring(types["evidence_confirmation"]))
        self.assertFalse(is_trusted_for_scoring(types["recruiter_calibration"]))
        self.assertFalse(is_trusted_for_scoring(types["recruiter_outcome"]))

    def test_question_templates_can_reuse_the_same_job(self) -> None:
        rows = [
            {
                "screening_job_id": "job-a",
                "candidate_profile_id": "cand-1",
                "job_title": "AI Agent 工程师",
                "skills": ["Python"],
                "feedback_type": "question",
                "value": "effective",
                "comment": "追问工具调用边界",
            }
        ]
        memories = scoped_feedback_memories(
            rows,
            job_id="job-a",
            candidate_id="cand-2",
            job_title="AI Agent 工程师",
            skills=["Python", "FastAPI"],
        )
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["memory_type"], "question_pattern")
        self.assertEqual(memories[0]["trust_level"], "model_checked")
        self.assertFalse(is_trusted_for_scoring(memories[0]))

    def test_feedback_does_not_leak_across_unrelated_jobs(self) -> None:
        rows = [
            {
                "screening_job_id": "job-a",
                "candidate_profile_id": "cand-1",
                "job_title": "AI Agent 工程师",
                "skills": ["Python", "LangGraph"],
                "feedback_type": "decision",
                "value": "too_high",
                "comment": "这个 Agent 岗分数偏高",
            },
            {
                "screening_job_id": "job-b",
                "candidate_profile_id": "cand-9",
                "job_title": "财务会计",
                "skills": ["Excel"],
                "feedback_type": "decision",
                "value": "accurate",
                "comment": "会计岗结论准确",
            },
            {
                "screening_job_id": "job-c",
                "candidate_profile_id": "cand-8",
                "job_title": "AI Agent 工程师",
                "skills": ["Python"],
                "feedback_type": "question",
                "value": "effective",
                "comment": "类似岗位可复用追问",
            },
        ]
        memories = scoped_feedback_memories(
            rows,
            job_id="job-z",
            candidate_id="cand-2",
            job_title="AI Agent 工程师",
            skills=["Python", "FastAPI"],
        )
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["memory_type"], "question_pattern")
        self.assertFalse(is_trusted_for_scoring(memories[0]))

        leaked = scoped_feedback_memories(
            rows,
            job_id="job-z",
            candidate_id="cand-z",
            job_title="财务会计",
            skills=["Excel"],
        )
        self.assertEqual(leaked, [])

    def test_only_confirmed_evidence_is_human_verified(self) -> None:
        confirmed = memory_from_feedback(
            {"feedback_type": "evidence", "value": "confirmed", "evidence_id": "EV-003", "job_title": "后端"}
        )
        self.assertEqual(confirmed["trust_level"], "human_verified")
        self.assertEqual(confirmed["memory_type"], "evidence_confirmation")
        self.assertTrue(is_trusted_for_scoring(confirmed))

        for row, memory_type in (
            ({"feedback_type": "decision", "value": "accurate"}, "recruiter_calibration"),
            ({"feedback_type": "question", "value": "effective"}, "question_pattern"),
            ({"feedback_type": "candidate_status", "value": "entered_interview"}, "recruiter_outcome"),
            ({"feedback_type": "decision", "value": "unknown_label"}, "recruiter_calibration"),
            ({"feedback_type": "evidence", "value": "insufficient"}, "recruiter_calibration"),
        ):
            memory = memory_from_feedback(row)
            self.assertEqual(memory["memory_type"], memory_type)
            self.assertEqual(memory["trust_level"], "model_checked")
            self.assertFalse(is_trusted_for_scoring(memory))

    def test_fetch_does_not_drop_older_candidate_feedback(self) -> None:
        rows = [
            {
                "id": f"noise-{index}",
                "workspace_id": "ws",
                "screening_job_id": "other-job",
                "candidate_profile_id": f"other-{index}",
                "job_title": "无关岗位",
                "skills": ["Excel"],
                "feedback_type": "decision",
                "value": "accurate",
                "created_at": f"2026-08-14T12:{index:02d}:00Z",
            }
            for index in range(30)
        ]
        rows.append(
            {
                "id": "keep-me",
                "workspace_id": "ws",
                "screening_job_id": "job-a",
                "candidate_profile_id": "cand-1",
                "job_title": "AI Agent 工程师",
                "skills": ["Python"],
                "feedback_type": "evidence",
                "value": "confirmed",
                "created_at": "2026-08-01T00:00:00Z",
            }
        )
        memories = fetch_scoped_feedback_memories(
            _FakeFeedbackClient(rows),
            "ws",
            job_id="job-a",
            candidate_id="cand-1",
            job_title="AI Agent 工程师",
            skills=["Python"],
        )
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["memory_type"], "evidence_confirmation")
        self.assertTrue(is_trusted_for_scoring(memories[0]))


class DualAgentPipelineTests(unittest.TestCase):
    def test_end_to_end_emits_match_questions_and_checker_review(self) -> None:
        jd = (
            "岗位名称：AI Agent 工程师\n本科及以上学历，3年及以上开发经验。\n"
            "要求 Python、LangGraph、FastAPI。"
        )
        requirements = ScreeningWorker._extract_requirements(jd)
        profile = {
            "name": "端到端候选人",
            "years_experience": 4,
            "education": "本科",
            "skills": ["Python", "LangGraph", "FastAPI"],
            "raw_text": (
                "负责生产环境 Multi-Agent 编排与 FastAPI 服务化。"
                "日均调用 12000 次，工具误调用率从 9% 降至 2.8%，并完成 tracing 与压测。"
            ),
        }
        construction = MockConstructionAgent()
        checker = MockCheckerAgent()
        output = construction.analyze(requirements, profile)
        result = run_checker_harness(
            initial_output=output,
            requirements=requirements,
            raw_candidate_profile=profile,
            review=checker.review,
            revise=lambda issues: construction.analyze(requirements, profile, revision_feedback=issues),
        )
        self.assertTrue(result.output.match_result.get("evidence"))
        self.assertTrue(result.output.questions)
        self.assertIn(result.review.get("status"), {"pass", "review", "fail", "degraded"})
        self.assertGreaterEqual(result.rounds, 1)
        grounded = [
            item
            for item in result.output.match_result["evidence"]
            if item.get("quote") and quote_in_source(item["quote"], profile["raw_text"])
        ]
        self.assertTrue(grounded)

    def test_pipeline_persists_match_questions_and_checker_review(self) -> None:
        from unittest.mock import MagicMock

        from app.persist import persist_candidate_core

        jd = "岗位名称：AI Agent 工程师\n本科及以上，3年经验。\n要求 Python、FastAPI。"
        requirements = ScreeningWorker._extract_requirements(jd)
        profile = {
            "name": "持久化候选人",
            "years_experience": 4,
            "education": "本科",
            "skills": ["Python", "FastAPI"],
            "raw_text": "负责生产环境 FastAPI 服务上线，Python 日均调用稳定。",
        }
        construction = MockConstructionAgent()
        checker = MockCheckerAgent()
        output = construction.analyze(requirements, profile)
        result = run_checker_harness(
            initial_output=output,
            requirements=requirements,
            raw_candidate_profile=profile,
            review=checker.review,
            revise=lambda issues: construction.analyze(requirements, profile, revision_feedback=issues),
        )
        client = MagicMock()
        client.rpc.side_effect = RuntimeError(
            "Could not find the function public.persist_screening_candidate_core in the schema cache"
        )
        table = MagicMock()
        client.table.return_value = table
        table.upsert.return_value.execute.return_value = MagicMock()
        persisted = persist_candidate_core(
            client,
            workspace_id="ws",
            screening_job_id="job",
            candidate_profile_id="cand",
            match_payload=result.output.match_result,
            questions=result.output.questions,
            followups=result.output.followups,
            review=result.review,
        )
        self.assertEqual(persisted["mode"], "fallback")
        written = {call.args[0] for call in client.table.call_args_list}
        self.assertGreaterEqual(written, {"match_results", "question_packs", "checker_reviews"})
        self.assertEqual(table.upsert.call_count, 3)

    def test_revision_caps_score_and_drops_unsupported_claim(self) -> None:
        requirements = {
            "title": "AI Agent 工程师",
            "must_have_skills": ["Python"],
            "nice_to_have_skills": [],
            "min_years": 1,
            "education": "本科",
            "raw_text": "需要 Python",
        }
        profile = {
            "name": "待修正",
            "years_experience": 4,
            "education": "本科",
            "skills": ["Python"],
            "raw_text": "负责生产环境 Python 服务上线两年，日均调用稳定。",
        }
        agent = MockConstructionAgent()
        initial = agent.analyze(requirements, profile)
        original_score = float(initial.match_result["score"])
        unsupported = next(claim for claim in initial.claims if claim.get("predicate") == "has_skill")
        issue = {
            "issue_type": "unsupported_score",
            "recommended_action": "cap",
            "target": "score_breakdown.evidence",
            "recommended_value": 40,
            "note": "只有预研",
            "patches": [
                {"action": "cap", "path": "score_breakdown.evidence", "value": 40},
                {"action": "remove", "path": f"claims.{unsupported['id']}"},
                {"action": "demote_decision", "path": "/decision", "value": "review"},
            ],
        }

        def review(_checker_input):
            return {"status": "review", "issues": [issue]}

        result = run_checker_harness(
            initial_output=initial,
            requirements=requirements,
            raw_candidate_profile=profile,
            review=review,
            revise=lambda issues: agent.analyze(requirements, profile, revision_feedback=issues),
        )
        self.assertLess(result.output.match_result["score"], original_score)
        self.assertFalse(any(claim.get("id") == unsupported["id"] for claim in result.output.claims))
