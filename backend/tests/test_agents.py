import unittest

from app.agents import MockCheckerAgent, MockConstructionAgent
from app.checker_contract import CheckerInputBuilder
from app.checker_corrections import apply_checker_corrections
from app.checker_harness import run_checker_harness


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
                "raw_text": (
                    "负责生产环境 Multi-Agent 编排与 FastAPI 服务化。"
                    "日均调用 12000 次，工具误调用率从 9% 降至 2.8%，"
                    "并完成 tracing 与压测。"
                ),
                "experiences": [
                    {
                        "company": "示例科技",
                        "title": "Agent 工程师",
                        "years": 3,
                        "bullets": ["生产级 Multi-Agent", "FastAPI 网关 QPS 提升 40%"],
                    }
                ],
                "projects": [{"name": "内部 Copilot", "bullets": ["日活 3000+", "灰度上线"]}],
            },
        )

        self.assertEqual(output.match_result["decision"], "recommend")
        self.assertGreaterEqual(len(output.questions), 10)
        self.assertEqual(len({q["question"] for q in output.questions}), len(output.questions))
        self.assertGreaterEqual(len(output.followups), 3)
        self.assertTrue(all(question["scoring_rubric"] for question in output.questions))
        self.assertEqual(self.checker.review(output)["status"], "pass")

    def test_gate_failure_generates_gap_validation_exam(self) -> None:
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
        self.assertGreaterEqual(len(output.questions), 10)
        self.assertEqual(len({q["question"] for q in output.questions}), len(output.questions))
        self.assertGreaterEqual(len(output.followups), 3)

    def test_missing_one_skill_goes_to_review_not_hard_reject(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "接近匹配候选人",
                "years_experience": 4,
                "education": "硕士",
                "skills": ["Python", "LangGraph"],  # missing FastAPI
            },
        )

        self.assertTrue(output.match_result["hard_gate_pass"])
        self.assertEqual(output.match_result["decision"], "review")
        self.assertGreaterEqual(len(output.questions), 10)
        self.assertGreaterEqual(len(output.followups), 3)
        self.assertTrue(any("FastAPI" in (q["question"] + q["knowledge_point"]) for q in output.questions))

    def test_checker_exposes_auditable_overclaim_and_repair(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "仅预研候选人",
                "years_experience": 4,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI"],
                "raw_text": "参与过 LangGraph 预研 Demo，未上线。",
            },
        )
        output.match_result["decision"] = "recommend"
        output.match_result["score_breakdown"]["evidence_quality"] = 20
        output.match_result["evidence"] = [
            {"source": "resume", "text": "参与过 LangGraph 预研 Demo，未上线。"}
        ]
        review = self.checker.review(output)
        self.assertEqual(review["status"], "review")
        self.assertTrue(any(item["issue_type"] == "mastery_overclaim" for item in review["issues"]))
        self.assertTrue(all(item.get("recommendation") for item in review["issues"]))
        self.assertTrue(review["reasoning_path"])

    def test_information_missing_is_not_promoted(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "信息缺失候选人",
                "years_experience": 0,
                "education": "",
                "skills": ["Python"],
                "raw_text": "有一些 AI 项目经历。",
            },
        )
        self.assertEqual(output.match_result["decision"], "reject")
        self.assertFalse(output.match_result["hard_gate_pass"])
        self.assertTrue(any("年限" in risk or "学历" in risk for risk in output.match_result["risks"]))

    def test_keyword_stuffing_is_a_risk_not_production_proof(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "关键词堆砌候选人",
                "years_experience": 4,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI"],
                "raw_text": "Python LangGraph FastAPI 关键词堆砌，熟悉全部技术栈。",
            },
        )
        self.assertIn("疑似关键词堆砌", output.match_result["risks"])
        review = self.checker.review(output)
        self.assertEqual(review["status"], "review")
        self.assertTrue(any(item["issue_type"] == "keyword_stuffing" for item in review["issues"]))

    def test_checker_revision_revalidates_claims_and_decision(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "待复核候选人",
                "years_experience": 4,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI"],
                "raw_text": "负责生产环境的 Python、LangGraph 与 FastAPI 服务，持续运行两年。",
            },
            revision_feedback=[
                {
                    "issue_type": "mastery_overclaim",
                    "note": "项目经历没有支撑熟练程度。",
                }
            ],
        )

        self.assertEqual(output.match_result["decision"], "review")
        correction = output.match_result["score_breakdown"]["checker_correction"]
        self.assertTrue(correction["applied"])
        self.assertIn("mastery_overclaim", correction["invalidated_issue_types"])
        self.assertTrue(any(claim.get("verification_required") for claim in output.claims))

    def test_checker_input_contains_full_review_context(self) -> None:
        profile = {
            "name": "完整上下文候选人",
            "years_experience": 4,
            "education": "本科",
            "skills": ["Python", "LangGraph", "FastAPI"],
            "raw_text": "负责生产环境服务并完成上线。",
        }
        output = self.agent.analyze(self.requirements, profile)

        checker_input = CheckerInputBuilder.build(output, self.requirements, profile)

        self.assertEqual(checker_input.requirements, self.requirements)
        self.assertEqual(checker_input.candidate_profile, profile)
        self.assertEqual(checker_input.raw_candidate_profile, profile)
        self.assertEqual(checker_input.proposed_decision, output.match_result["decision"])
        self.assertEqual(checker_input.decision, output.match_result["decision"])
        self.assertEqual(checker_input.hard_gate_pass, output.match_result["hard_gate_pass"])
        self.assertEqual(checker_input.hard_gate["pass"], output.match_result["hard_gate_pass"])
        self.assertEqual(checker_input.proposed_score, output.match_result["score"])
        self.assertTrue(checker_input.source_evidence)
        self.assertTrue(checker_input.score_breakdown)
        self.assertTrue(checker_input.candidate_profile["raw_text"])
        self.assertEqual(checker_input.source_evidence, output.match_result["evidence"])
        self.assertEqual(checker_input.score_breakdown, output.match_result["score_breakdown"])
        self.assertEqual(checker_input.claims, output.claims)
        self.assertEqual(checker_input.questions, output.questions)
        self.assertEqual(checker_input.followups, output.followups)
        self.assertEqual(checker_input.risks, output.match_result["risks"])

    def test_structured_patch_cannot_override_hard_gate(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "硬门槛失败",
                "years_experience": 1,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI"],
            },
        )
        output.match_result["decision"] = "recommend"  # Simulate a faulty Checker proposal.
        correction = apply_checker_corrections(
            output.match_result,
            output.claims,
            [
                {
                    "issue_type": "bad_patch",
                    "patches": [{"action": "set_decision", "path": "/decision", "value": "recommend"}],
                }
            ],
        )

        self.assertFalse(output.match_result["hard_gate_pass"])
        self.assertEqual(output.match_result["decision"], "reject")
        self.assertIn("set_decision:recommend", correction["rejected_actions"])

    def test_checker_harness_applies_first_pass_correction_and_caps_at_two_rounds(self) -> None:
        profile = {
            "name": "待修正候选人",
            "years_experience": 4,
            "education": "本科",
            "skills": ["Python", "LangGraph", "FastAPI"],
            "raw_text": "负责生产环境服务并完成上线。",
        }
        initial = self.agent.analyze(self.requirements, profile)
        initial.match_result["decision"] = "recommend"
        calls: list[object] = []
        revisions: list[list[dict[str, object]]] = []
        issue = {
            "issue_type": "mastery_overclaim",
            "severity": "medium",
            "note": "生产深度证据不足",
            "patches": [
                {"action": "demote_decision", "path": "/decision", "value": "review"},
                {"action": "mark_claims_verification_required", "path": "/claims"},
            ],
        }

        def review(checker_input):
            calls.append(checker_input)
            return {"status": "review", "issues": [issue]}

        def revise(feedback):
            revisions.append(feedback)
            return self.agent.analyze(self.requirements, profile, revision_feedback=feedback)

        result = run_checker_harness(
            initial_output=initial,
            requirements=self.requirements,
            raw_candidate_profile=profile,
            review=review,
            revise=revise,
            max_rounds=99,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(result.rounds, 2)
        self.assertEqual(initial.match_result["decision"], "review")
        self.assertTrue(any(claim.get("verification_required") for claim in initial.claims))
        self.assertIn("demote_decision:review", result.corrections[0]["applied_actions"])
        self.assertTrue(result.review["correction_cap_reached"])

    def test_custom_thresholds_can_promote_review_candidate(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "边界候选人",
                "years_experience": 4,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI"],
            },
            screening_config={
                "score_thresholds": {"recommend_min": 90, "review_min": 50},
            },
        )

        self.assertEqual(output.match_result["decision"], "review")

    def test_disabled_year_gate_allows_younger_candidate_if_score_is_high(self) -> None:
        output = self.agent.analyze(
            self.requirements,
            {
                "name": "初级高分候选人",
                "years_experience": 1,
                "education": "本科",
                "skills": ["Python", "LangGraph", "FastAPI"],
            },
            screening_config={
                "hard_gates": {
                    "min_years": {"enabled": False},
                    "education": {"enabled": True},
                    "must_have_skills": {"enabled": True, "min_coverage": 1.0},
                },
                "score_thresholds": {"recommend_min": 75, "review_min": 60},
            },
        )

        self.assertTrue(output.match_result["hard_gate_pass"])
        self.assertIn(output.match_result["decision"], {"recommend", "review"})


if __name__ == "__main__":
    unittest.main()
