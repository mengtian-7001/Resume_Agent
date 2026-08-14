import unittest
import json
from unittest.mock import MagicMock

from app.agents import MockConstructionAgent, OpenAICheckerAgent, OpenAIConstructionAgent
from app.checker_contract import CheckerInputBuilder
from app.embeddings import cosine, local_embed
from app.llm_client import LLMClientError, extract_json_object
from app.matching import hybrid_text_score, match_profile


class FakeSettings:
    agent_mode = "openai"
    agent_max_react_steps = 8
    agent_max_llm_calls = 6
    agent_llm_reflect = False
    checker_max_retries = 0
    checker_fail_closed = True
    tavily_api_key = None
    embedding_model = None

    def construction_llm(self):
        return {
            "base_url": "https://direct.evolink.ai/v1",
            "api_key": "sk-test",
            "model": "gpt-test",
        }

    def checker_llm(self):
        return self.construction_llm()


class LlmClientHelpersTests(unittest.TestCase):
    def test_extract_json_object_from_fence(self) -> None:
        data = extract_json_object('```json\n{"questions":[{"question":"q1","knowledge_point":"SQL"}]}\n```')
        self.assertEqual(data["questions"][0]["knowledge_point"], "SQL")

    def test_extract_json_object_rejects_empty(self) -> None:
        with self.assertRaises(LLMClientError):
            extract_json_object("")


class EmbeddingTests(unittest.TestCase):
    def test_local_embed_is_normalized_and_stable(self) -> None:
        a = local_embed("ETL 数仓 SQL")
        b = local_embed("ETL 数仓 SQL")
        self.assertEqual(len(a), 256)
        self.assertAlmostEqual(sum(x * x for x in a), 1.0, places=5)
        self.assertEqual(a, b)
        self.assertGreater(cosine(a, local_embed("ETL SQL Spark")), 0.1)

    def test_embedder_ignores_chat_model_name(self) -> None:
        from app.embeddings import embedder_from_settings

        class S:
            embedding_model = "gpt-4o-mini"

            def construction_llm(self):
                return {"base_url": "https://example.com/v1", "api_key": "x", "model": "gpt-4o-mini"}

        service = embedder_from_settings(S())
        self.assertEqual(service.model, "text-embedding-3-small")


class MatchingHybridTests(unittest.TestCase):
    def test_hybrid_text_score_has_semantic_and_tfidf(self) -> None:
        parts = hybrid_text_score("ETL 数仓 SQL Spark", "负责 ETL 与 SQL 数仓建模，Spark 批处理")
        self.assertIn("semantic", parts)
        self.assertIn("tfidf", parts)
        self.assertIn("semantic_source", parts)
        self.assertGreater(parts["score"], 0)

    def test_match_profile_marks_llm_source(self) -> None:
        scored = match_profile(
            {"must_have_skills": ["SQL"], "min_years": 1, "education": "本科"},
            {"name": "A", "years_experience": 2, "education": "本科", "skills": ["SQL"], "raw_text": "SQL 上线"},
            score_llm=88.0,
        )
        self.assertEqual(scored["score_breakdown"]["score_llm_source"], "llm_judge")
        self.assertEqual(scored["score_breakdown"]["score_llm"], 88.0)


class OpenAIAgentFallbackTests(unittest.TestCase):
    def test_construction_falls_back_when_llm_fails(self) -> None:
        agent = OpenAIConstructionAgent(FakeSettings())
        agent.client = MagicMock()
        agent.client.chat_json.side_effect = LLMClientError("boom")
        output = agent.analyze(
            {
                "title": "数据工程师",
                "must_have_skills": ["ETL", "SQL"],
                "nice_to_have_skills": ["Python"],
                "min_years": 3,
                "education": "本科",
            },
            {
                "name": "测试",
                "years_experience": 0,
                "education": "本科",
                "skills": ["Python"],
                "raw_text": "上线",
            },
        )
        self.assertGreaterEqual(len(output.questions), 1)
        self.assertTrue(any(t.get("action") == "react_plan" for t in output.trace))
        self.assertTrue(any(t.get("action") == "reflect" for t in output.trace))
        self.assertTrue(any(t.get("tool") == "llm_judge" and t.get("status") == "failed" for t in output.trace))
        self.assertEqual(output.match_result["score_breakdown"]["score_llm_source"], "heuristic_proxy")

    def test_construction_react_uses_llm_judge_and_questions(self) -> None:
        agent = OpenAIConstructionAgent(FakeSettings())
        agent.client = MagicMock()
        chat = MagicMock(model="gpt-test", duration_ms=12)
        judge = {
            "score_llm": 71,
            "dimensions": {"skills": 60, "experience": 50, "project_relevance": 70, "risk": 40},
            "evidence": [
                {"type": "skills", "text": "熟悉 SQL", "source": "resume"},
                {"type": "experience", "text": "做过一次上线", "source": "resume"},
            ],
            "rationale": "有 SQL 基础但 ETL 证据不足",
        }
        questions = {
            "questions": [
                {
                    "id": "Q01",
                    "question": "请设计一条从 ODS 到 DWD 的 ETL 校验链路。",
                    "knowledge_point": "ETL",
                    "difficulty": "medium",
                    "scoring_rubric": "拆解 30% 方案 40% 验证 30%",
                },
                {
                    "id": "Q02",
                    "question": "如何用 SQL 排查数仓重复计量？",
                    "knowledge_point": "SQL",
                    "difficulty": "hard",
                    "scoring_rubric": "拆解 30% 方案 40% 验证 30%",
                },
                {
                    "id": "Q03",
                    "question": "请说明 SCD2 维度建模的适用场景。",
                    "knowledge_point": "数仓",
                    "difficulty": "easy",
                    "scoring_rubric": "拆解 30% 方案 40% 验证 30%",
                },
            ],
            "followups": [
                {"question": "补一个可量化的上线验收指标。", "target": "ETL", "evidence_required": True},
                {"question": "年限差距如何用项目证明？", "target": "experience_gap", "evidence_required": True},
            ],
        }
        agent.client.chat_json.side_effect = [(judge, chat), (questions, chat)]
        output = agent.analyze(
            {
                "title": "数据工程师",
                "must_have_skills": ["ETL", "SQL", "数仓"],
                "nice_to_have_skills": ["Python"],
                "min_years": 3,
                "education": "本科",
                "raw_text": "岗位要求熟悉 ETL、SQL 与数仓建模",
            },
            {
                "name": "测试",
                "years_experience": 0,
                "education": "本科",
                "skills": ["Python"],
                "raw_text": "熟悉 SQL，做过一次上线",
            },
        )
        self.assertIn(
            output.match_result["score_breakdown"]["score_llm_source"],
            {"llm_judge", "llm_judge_clamped"},
        )
        det = float(output.match_result["score_breakdown"]["score_deterministic"])
        llm = float(output.match_result["score_breakdown"]["score_llm"])
        self.assertLessEqual(abs(llm - det), 18.0 + 1e-6)
        self.assertEqual(len(output.questions), 3)
        self.assertTrue(any(t.get("action") == "react_plan" for t in output.trace))
        self.assertTrue(any(t.get("tool") == "llm_judge" and t.get("status") == "completed" for t in output.trace))
        self.assertTrue(any(t.get("action") == "reflect" for t in output.trace))
        self.assertTrue(any(t.get("action") == "decision_generate" for t in output.trace))
        self.assertIn("score_deterministic", (output.match_result.get("react") or {}).get("tools_used") or [])

    def test_judge_rejects_ungrounded_evidence(self) -> None:
        agent = OpenAIConstructionAgent(FakeSettings())
        agent.client = MagicMock()
        chat = MagicMock(model="gpt-test", duration_ms=8)
        bad_judge = {
            "score_llm": 90,
            "dimensions": {"skills": 90, "experience": 90, "project_relevance": 90, "risk": 10},
            "evidence": [{"type": "skills", "text": "这段话简历里根本没有", "source": "resume"}],
            "rationale": "瞎编",
        }
        questions = {
            "questions": [
                {
                    "id": "Q01",
                    "question": "请说明 ETL 校验？",
                    "knowledge_point": "ETL",
                    "difficulty": "medium",
                    "scoring_rubric": "方案",
                },
                {
                    "id": "Q02",
                    "question": "SQL 如何去重？",
                    "knowledge_point": "SQL",
                    "difficulty": "easy",
                    "scoring_rubric": "方案",
                },
                {
                    "id": "Q03",
                    "question": "数仓分层怎么设计？",
                    "knowledge_point": "数仓",
                    "difficulty": "hard",
                    "scoring_rubric": "方案",
                },
            ],
            "followups": [{"question": "追问", "target": "ETL", "evidence_required": True}],
        }
        # First judge rejected, retry also rejected, then questions.
        agent.client.chat_json.side_effect = [(bad_judge, chat), (bad_judge, chat), (questions, chat)]
        output = agent.analyze(
            {
                "title": "数据工程师",
                "must_have_skills": ["ETL", "SQL"],
                "min_years": 1,
                "education": "本科",
                "raw_text": "需要 ETL 与 SQL",
            },
            {
                "name": "测试",
                "years_experience": 2,
                "education": "本科",
                "skills": ["SQL"],
                "raw_text": "只会写简单 SQL",
            },
        )
        self.assertEqual(output.match_result["score_breakdown"]["score_llm_source"], "heuristic_proxy")
        self.assertTrue(
            any(
                t.get("tool") == "llm_judge"
                and t.get("status") == "completed"
                and (t.get("observation") or {}).get("rejected")
                for t in output.trace
            )
        )

    def test_checker_fails_closed_on_error(self) -> None:
        construction = MockConstructionAgent().analyze(
            {
                "title": "AI Agent 工程师",
                "must_have_skills": ["Python", "LangGraph", "FastAPI"],
                "min_years": 3,
                "education": "本科",
            },
            {
                "name": "测试",
                "years_experience": 4,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI"],
                "raw_text": "生产环境 Multi-Agent 编排，日均 12000 次。",
            },
        )
        checker = OpenAICheckerAgent(FakeSettings())
        checker.client = MagicMock()
        checker.client.chat_json.side_effect = LLMClientError("down")
        review = checker.review(construction)
        self.assertEqual(review["status"], "fail")
        self.assertEqual(review.get("fallback"), "mock_degraded")
        self.assertTrue(review.get("hard_degrade"))

    def test_openai_checker_receives_complete_checker_input(self) -> None:
        requirements = {
            "title": "AI Agent 工程师",
            "must_have_skills": ["Python", "LangGraph", "FastAPI"],
            "min_years": 3,
            "education": "本科",
            "raw_text": "需要 Python、LangGraph 与 FastAPI 的生产经验。",
        }
        profile = {
            "name": "测试",
            "years_experience": 4,
            "education": "本科",
            "skills": ["Python", "LangGraph", "FastAPI"],
            "raw_text": "负责生产环境 Multi-Agent 编排并上线 FastAPI 服务。",
        }
        construction = MockConstructionAgent().analyze(requirements, profile)
        checker = OpenAICheckerAgent(FakeSettings())
        checker.client = MagicMock()
        chat = MagicMock(model="gpt-test", duration_ms=6)
        checker.client.chat_json.return_value = (
            {"status": "pass", "summary": "ok", "reasoning_path": [], "assumptions": [], "issues": []},
            chat,
        )

        checker.review(CheckerInputBuilder.build(construction, requirements, profile))

        payload = json.loads(checker.client.chat_json.call_args.args[0][1]["content"])
        self.assertEqual(
            set(payload),
            {
                "requirements",
                "candidate_profile",
                "raw_candidate_profile",
                "source_evidence",
                "proposed_score",
                "score_breakdown",
                "hard_gate_pass",
                "hard_gate",
                "proposed_decision",
                "decision",
                "claims",
                "questions",
                "followups",
                "risks",
            },
        )
        self.assertEqual(payload["requirements"]["raw_text"], requirements["raw_text"])
        self.assertEqual(payload["candidate_profile"]["raw_text"], profile["raw_text"])
        self.assertEqual(payload["raw_candidate_profile"]["raw_text"], profile["raw_text"])
        self.assertTrue(payload["source_evidence"])
        self.assertIn("pass", payload["hard_gate"])
        self.assertIn("proposed_score", payload)

    def test_memory_context_is_injected_into_judge_and_questions(self) -> None:
        settings = FakeSettings()
        agent = OpenAIConstructionAgent(
            settings,
            memory_retriever=lambda *_args, **_kwargs: [
                {
                    "content": "已审核题目模式；考点：ETL,SQL",
                    "similarity": 0.91,
                    "trust_level": "human_or_source_verified",
                    "trusted": True,
                },
                {
                    "content": "已审核题目模式；考点：数仓",
                    "similarity": 0.72,
                    "trust_level": "model_checked",
                    "trusted": False,
                },
            ],
        )
        agent.client = MagicMock()
        chat = MagicMock(model="gpt-test", duration_ms=9)
        judge = {
            "score_llm": 66,
            "dimensions": {"skills": 60, "experience": 50, "project_relevance": 55, "risk": 40},
            "evidence": [
                {"type": "skills", "text": "熟悉 SQL", "source": "resume"},
                {"type": "experience", "text": "做过一次上线", "source": "resume"},
            ],
            "rationale": "记忆提示 ETL 考点需深挖",
        }
        questions = {
            "questions": [
                {
                    "id": "Q01",
                    "question": "请设计 ETL 校验？",
                    "knowledge_point": "ETL",
                    "difficulty": "medium",
                    "scoring_rubric": "方案",
                },
                {
                    "id": "Q02",
                    "question": "SQL 如何去重？",
                    "knowledge_point": "SQL",
                    "difficulty": "easy",
                    "scoring_rubric": "方案",
                },
                {
                    "id": "Q03",
                    "question": "数仓分层？",
                    "knowledge_point": "数仓",
                    "difficulty": "hard",
                    "scoring_rubric": "方案",
                },
            ],
            "followups": [{"question": "追问", "target": "ETL", "evidence_required": True}],
        }
        agent.client.chat_json.side_effect = [(judge, chat), (questions, chat)]
        output = agent.analyze(
            {
                "title": "数据工程师",
                "must_have_skills": ["ETL", "SQL"],
                "min_years": 1,
                "education": "本科",
                "raw_text": "需要 ETL 与 SQL",
            },
            {
                "name": "测试",
                "years_experience": 2,
                "education": "本科",
                "skills": ["SQL"],
                "raw_text": "熟悉 SQL，做过一次上线",
            },
            workspace_id="ws-demo",
            job_id="job-demo",
        )
        react = output.match_result.get("react") or {}
        self.assertTrue(react.get("memory_applied"))
        self.assertGreaterEqual(react.get("memory_trusted") or 0, 1)
        self.assertTrue(any("记忆先验（trusted）" in r for r in (output.match_result.get("risks") or [])))
        # Judge user payload must include memory_context with trusted prior.
        judge_user = agent.client.chat_json.call_args_list[0].args[0][1]["content"]
        self.assertIn("memory_context", judge_user)
        self.assertIn("已审核题目模式；考点：ETL,SQL", judge_user)
        question_user = agent.client.chat_json.call_args_list[1].args[0][1]["content"]
        self.assertIn("memory_context", question_user)

    def test_llm_budget_caps_chat_calls(self) -> None:
        settings = FakeSettings()
        settings.agent_max_llm_calls = 1
        settings.agent_max_react_steps = 8
        agent = OpenAIConstructionAgent(settings)
        agent.client = MagicMock()
        chat = MagicMock(model="gpt-test", duration_ms=3)
        judge = {
            "score_llm": 70,
            "dimensions": {"skills": 70, "experience": 70, "project_relevance": 70, "risk": 30},
            "evidence": [
                {"type": "skills", "text": "熟悉 SQL", "source": "resume"},
                {"type": "experience", "text": "做过一次上线", "source": "resume"},
            ],
            "rationale": "ok",
        }
        agent.client.chat_json.side_effect = [(judge, chat)]
        output = agent.analyze(
            {
                "title": "数据工程师",
                "must_have_skills": ["SQL"],
                "min_years": 1,
                "education": "本科",
                "raw_text": "需要 SQL",
            },
            {
                "name": "测试",
                "years_experience": 2,
                "education": "本科",
                "skills": ["SQL"],
                "raw_text": "熟悉 SQL，做过一次上线",
            },
        )
        self.assertEqual(agent.client.chat_json.call_count, 1)
        self.assertEqual((output.match_result.get("react") or {}).get("llm_calls"), 1)
        self.assertGreaterEqual(len(output.questions), 1)


if __name__ == "__main__":
    unittest.main()
