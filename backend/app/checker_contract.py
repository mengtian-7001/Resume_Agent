"""Typed input contract shared by Checker implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CheckerInput:
    """Complete, auditable context required for a Checker review."""

    requirements: dict[str, Any]
    candidate_profile: dict[str, Any]
    source_evidence: list[dict[str, Any]]
    proposed_score: float
    score_breakdown: dict[str, Any]
    hard_gate_pass: bool
    proposed_decision: str
    claims: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    followups: list[dict[str, Any]]
    risks: list[Any]

    @property
    def raw_candidate_profile(self) -> dict[str, Any]:
        return self.candidate_profile

    @property
    def decision(self) -> str:
        return self.proposed_decision

    @property
    def hard_gate(self) -> dict[str, Any]:
        return {
            "pass": self.hard_gate_pass,
            "immutable": True,
            "missing_required": list(self.score_breakdown.get("missing_required") or []),
        }

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-safe representation for LLM Checker adapters."""
        return {
            "requirements": self.requirements,
            "candidate_profile": self.candidate_profile,
            "raw_candidate_profile": self.candidate_profile,
            "source_evidence": self.source_evidence,
            "proposed_score": self.proposed_score,
            "score_breakdown": self.score_breakdown,
            "hard_gate_pass": self.hard_gate_pass,
            "hard_gate": self.hard_gate,
            "proposed_decision": self.proposed_decision,
            "decision": self.proposed_decision,
            "claims": self.claims,
            "questions": self.questions,
            "followups": self.followups,
            "risks": self.risks,
        }


class CheckerInputBuilder:
    """Build a complete CheckerInput from Construction output and its sources."""

    @staticmethod
    def build(
        output: Any,
        requirements: dict[str, Any] | None,
        raw_candidate_profile: dict[str, Any] | None,
    ) -> CheckerInput:
        result = dict(getattr(output, "match_result", {}) or {})
        profile = dict(raw_candidate_profile or result.get("candidate_profile") or {})
        if not profile.get("raw_text") and result.get("source_profile_text"):
            profile["raw_text"] = str(result.get("source_profile_text") or "")
        provided_requirements = dict(requirements or {})
        if not provided_requirements.get("raw_text") and requirements is None and result.get("source_jd_text"):
            provided_requirements["raw_text"] = str(result.get("source_jd_text") or "")
        breakdown = dict(result.get("score_breakdown") or {})
        if "missing_required" not in breakdown:
            breakdown["missing_required"] = list(result.get("missing_required") or [])
        return CheckerInput(
            requirements=provided_requirements,
            candidate_profile=profile,
            source_evidence=[
                dict(item)
                for item in (result.get("evidence") or [])
                if isinstance(item, dict)
            ],
            proposed_score=float(result.get("score") or 0),
            score_breakdown=breakdown,
            hard_gate_pass=bool(result.get("hard_gate_pass")),
            proposed_decision=str(result.get("decision") or "reject"),
            claims=[
                dict(item)
                for item in (getattr(output, "claims", []) or [])
                if isinstance(item, dict)
            ],
            questions=[
                dict(item)
                for item in (getattr(output, "questions", []) or [])
                if isinstance(item, dict)
            ],
            followups=[
                dict(item)
                for item in (getattr(output, "followups", []) or [])
                if isinstance(item, dict)
            ],
            risks=list(result.get("risks") or []),
        )


def coerce_checker_input(
    value: CheckerInput | Any,
    *,
    requirements: dict[str, Any] | None = None,
    raw_candidate_profile: dict[str, Any] | None = None,
) -> CheckerInput:
    """Keep the legacy ``review(ConstructionOutput)`` call shape working."""
    if isinstance(value, CheckerInput):
        return value
    return CheckerInputBuilder.build(value, requirements, raw_candidate_profile)
