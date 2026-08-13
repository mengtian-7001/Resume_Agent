import unittest
from unittest.mock import MagicMock

from app.agents import MockConstructionAgent, OpenAICheckerAgent, OpenAIConstructionAgent
from app.llm_client import LLMClientError, extract_json_object
from app.matching import hybrid_text_score, match_profile


class FakeSettings:
    agent_mode = "openai"
    agent_max_react_steps = 3

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


class MatchingHybridTests(unittest.TestCase):
    def test_hybrid_text_score_has_semantic_and_tfidf(self) -> None:
        parts = hybrid_text_score("ETL 数仓 SQL Spark", "负责 ETL 与 SQL 数仓建模，Spark 批处理")
        self.assertIn("semantic", parts)
        self.assertIn("tfidf", parts)
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
        self.assertTrue(any(t.get("tool") == "llm_judge" and t.get("fallback") == "heuristic_proxy" for t in output.trace))
        self.assertEqual(output.match_result["score_breakdown"]["score_llm_source"], "heuristic_proxy")

    def test_construction_react_uses_llm_judge_and_questions(self) -> None:
        agent = OpenAIConstructionAgent(FakeSettings())
        agent.client = MagicMock()
        chat = MagicMock(model="gpt-test", duration_ms=12)
        judge = {
            "score_llm": 71,
            "dimensions": {"skills": 60, "experience": 50, "project_relevance": 70, "risk": 40},
            "evidence": [{"type": "skills", "text": "熟悉 SQL", "source": "resume"}],
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
            },
            {
                "name": "测试",
                "years_experience": 0,
                "education": "本科",
                "skills": ["Python"],
                "raw_text": "熟悉 SQL，做过一次上线",
            },
        )
        self.assertEqual(output.match_result["score_breakdown"]["score_llm_source"], "llm_judge")
        self.assertEqual(output.match_result["score_breakdown"]["score_llm"], 71)
        self.assertEqual(len(output.questions), 3)
        self.assertTrue(any(t.get("action") == "react_plan" for t in output.trace))
        self.assertTrue(any(t.get("tool") == "llm_judge" and t.get("status") == "completed" for t in output.trace))
        self.assertTrue(any(t.get("action") == "decision_generate" for t in output.trace))

    def test_checker_falls_back_on_error(self) -> None:
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
        self.assertIn(review["status"], {"pass", "fail"})
        self.assertEqual(review.get("fallback"), "mock")


if __name__ == "__main__":
    unittest.main()
