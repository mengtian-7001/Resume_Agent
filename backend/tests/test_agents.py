import unittest

from app.agents import MockCheckerAgent, MockConstructionAgent


class MockAgentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = MockConstructionAgent()
        self.checker = MockCheckerAgent()
        self.requirements = {
            "title": "AI Agent 工程师",
            "must_have_skills": ["Python", "LangGraph", "FastAPI"],
            "nice_to_have_skills": ["MCP"],
            "min_years": 3,
            "education": "本科",
        }

    def test_recommendation_generates_grounded_question_pack(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "测试候选人",
                "years_experience": 4,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI", "MCP"],
            },
        )

        self.assertEqual(output.match_result["decision"], "recommend")
        self.assertGreaterEqual(len(output.questions), 10)
        self.assertGreaterEqual(len(output.followups), 3)
        self.assertTrue(all(question["scoring_rubric"] for question in output.questions))
        self.assertEqual(self.checker.review(output)["status"], "pass")

    def test_gate_failure_does_not_generate_full_exam(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "初级候选人",
                "years_experience": 1,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI"],
            },
        )

        self.assertEqual(output.match_result["decision"], "reject")
        self.assertFalse(output.match_result["hard_gate_pass"])
        self.assertEqual(output.questions, [])
        self.assertGreaterEqual(len(output.followups), 1)


if __name__ == "__main__":
    unittest.main()
