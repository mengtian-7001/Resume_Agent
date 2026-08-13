import unittest

from app.worker import ScreeningWorker, _education_rank


class DeterministicMatchingTests(unittest.TestCase):
    def test_extract_requirements_detects_hard_gates(self) -> None:
        requirements = ScreeningWorker._extract_requirements(
            "岗位名称：AI Agent 工程师\n本科及以上学历，3年及以上开发经验。\n"
            "要求 Python、LangChain、Function Calling、Multi-Agent、Prompt Engineering。"
        )
        self.assertEqual(requirements["title"], "AI Agent 工程师")
        self.assertEqual(requirements["min_years"], 3)
        self.assertEqual(requirements["education"], "本科")
        self.assertIn("LangChain", requirements["must_have_skills"])

    def test_score_rejects_failed_hard_gate(self) -> None:
        result = ScreeningWorker._score(
            {"must_have_skills": ["Python", "LangChain"], "min_years": 3, "education": "本科"},
            {"years_experience": 1, "education": "本科", "skills": ["Python", "LangChain"]},
        )
        self.assertFalse(result["hard_gate_pass"])
        self.assertEqual(result["decision"], "reject")

    def test_education_order(self) -> None:
        self.assertGreater(_education_rank("硕士"), _education_rank("本科"))


if __name__ == "__main__":
    unittest.main()
