import unittest

from app.web_research import JobResearchService


class _MockSettings:
    agent_mode = "mock"
    tavily_api_key = None


class JobResearchTests(unittest.TestCase):
    def test_mock_mode_never_calls_network_and_returns_safe_context(self) -> None:
        context = JobResearchService(_MockSettings()).research(
            {"title": "AI Agent 工程师", "must_have_skills": ["Python", "LangGraph"]}
        )

        self.assertEqual(context["mode"], "mock")
        self.assertEqual(context["sources"], [])
        self.assertIn("Python", context["query"])


if __name__ == "__main__":
    unittest.main()
