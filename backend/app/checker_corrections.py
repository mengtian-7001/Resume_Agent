"""Deterministic correction rules applied after Checker feedback.

Checker feedback cannot invent missing resume facts. This module therefore
re-evaluates the safe parts of a Construction result: it downgrades unsupported
recommendations, raises uncertainty, and marks affected claims as needing
verification before regenerating interview questions.
"""

from __future__ import annotations

from typing import Any

from .screening_config import decide, merge_screening_config

DECISION_RANK = {"reject": 0, "review": 1, "recommend": 2}


def _more_conservative(left: str, right: str) -> str:
    if DECISION_RANK.get(left, 1) <= DECISION_RANK.get(right, 1):
        return left
    return right


def _recompute_decision(match_result: dict[str, Any], *, blocking: bool, floor: str) -> str:
    """Re-band the patched score with the same decide() used by the matcher."""
    config = merge_screening_config(
        match_result.get("screening_config")
        or (match_result.get("score_breakdown") or {}).get("screening_config")
    )
    computed = decide(
        float(match_result.get("score") or 0),
        bool(match_result.get("hard_gate_pass")),
        config,
    )
    final = computed
    if blocking:
        final = _more_conservative(final, "review")
    return _more_conservative(final, floor)


EVIDENCE_BLOCKING_ISSUES = {
    "missing_evidence",
    "mastery_overclaim",
    "score_evidence_mismatch",
    "keyword_stuffing",
    "recommendation_skill_gap",
    "ungrounded_citation",
    "unsupported_score",
    "unsupported_claim",
}
SAFE_PATCH_ACTIONS = {
    "demote_decision",
    "set_decision",
    "set_uncertainty",
    "add_risk",
    "mark_claims_verification_required",
    "regenerate_questions",
    "cap",
    "remove",
    "add",
}
CAPABLE_SCORE_FIELDS = {
    "evidence",
    "evidence_quality",
    "skill",
    "experience",
    "education",
    "production",
    "score_deterministic",
    "score_llm",
}


def _patches_for_issue(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the structured patch shape without trusting arbitrary writes."""
    patches = issue.get("patches")
    if not isinstance(patches, list):
        patches = [issue.get("patch")] if isinstance(issue.get("patch"), dict) else []
    if not patches and issue.get("action"):
        patches = [{
            "action": issue.get("action"),
            "path": issue.get("path"),
            "value": issue.get("value"),
        }]
    if not patches and issue.get("recommended_action"):
        patches = [{
            "action": issue.get("recommended_action"),
            "path": issue.get("target") or issue.get("path"),
            "value": issue.get("recommended_value") or issue.get("value"),
            "topic": issue.get("topic"),
        }]
    return [dict(patch) for patch in patches if isinstance(patch, dict)]


def apply_checker_corrections(
    match_result: dict[str, Any],
    claims: list[dict[str, Any]],
    issues: list[dict[str, Any]] | None,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Correct conclusion/claims without manufacturing unsupported evidence."""
    issues = [item for item in (issues or []) if isinstance(item, dict)]
    issue_types = {str(item.get("issue_type") or "") for item in issues}
    original_decision = str(match_result.get("decision") or "reject")
    blocking = issue_types & EVIDENCE_BLOCKING_ISSUES
    applied_actions: list[str] = []
    rejected_actions: list[str] = []
    changed = bool(blocking)
    decision_floor = "recommend"

    # A Checker can reduce confidence, but can never reopen a deterministic
    # hard-gate failure. Score banding is applied after patches via decide().
    if not bool(match_result.get("hard_gate_pass")):
        decision_floor = "reject"
        if match_result.get("decision") != "reject":
            match_result["decision"] = "reject"
            applied_actions.append("enforce_hard_gate:reject")
            changed = True

    risks = list(match_result.get("risks") or [])
    for issue in issues:
        for patch in _patches_for_issue(issue):
            action = str(patch.get("action") or "")
            if action not in SAFE_PATCH_ACTIONS:
                if action:
                    rejected_actions.append(action)
                continue
            if action in {"demote_decision", "set_decision"}:
                target = str(patch.get("value") or "review")
                if target not in DECISION_RANK:
                    rejected_actions.append(f"{action}:{target}")
                    continue
                if not bool(match_result.get("hard_gate_pass")) and target != "reject":
                    rejected_actions.append(f"{action}:{target}")
                    continue
                if DECISION_RANK[target] > DECISION_RANK.get(str(match_result.get("decision") or "reject"), 0):
                    rejected_actions.append(f"{action}:{target}")
                    continue
                decision_floor = _more_conservative(decision_floor, target)
                applied_actions.append(f"{action}:{target}")
                changed = True
            elif action == "set_uncertainty":
                target = str(patch.get("value") or "high")
                if target in {"medium", "high"}:
                    match_result["uncertainty"] = target
                    applied_actions.append(f"set_uncertainty:{target}")
                    changed = True
            elif action == "mark_claims_verification_required":
                for claim in claims:
                    if claim.get("predicate") in {"has_skill", "covers_required_skill", "years_experience"}:
                        claim["verification_required"] = True
                        claim["confidence"] = "low"
                applied_actions.append("mark_claims_verification_required")
                changed = True
            elif action == "regenerate_questions":
                # Construction consumes the issue and regenerates its question pack.
                applied_actions.append("regenerate_questions")
            elif action == "add_risk":
                applied_actions.append("add_risk")
            elif action == "cap":
                path = str(patch.get("path") or "")
                field = path.split(".")[-1] if path else ""
                try:
                    cap_value = float(patch.get("value"))
                except (TypeError, ValueError):
                    rejected_actions.append(f"cap:{path}")
                    continue
                breakdown = dict(match_result.get("score_breakdown") or {})
                if field in CAPABLE_SCORE_FIELDS and field in breakdown:
                    current = float(breakdown.get(field) or 0)
                    breakdown[field] = min(current, cap_value)
                    match_result["score_breakdown"] = breakdown
                    if field in {"evidence", "evidence_quality", "production"}:
                        match_result["score"] = round(min(float(match_result.get("score") or 0), cap_value), 2)
                    applied_actions.append(f"cap:{field}:{cap_value}")
                    changed = True
                elif field in {"score", "proposed_score"}:
                    match_result["score"] = round(min(float(match_result.get("score") or 0), cap_value), 2)
                    applied_actions.append(f"cap:score:{cap_value}")
                    changed = True
                else:
                    rejected_actions.append(f"cap:{path}")
            elif action == "remove":
                target = str(patch.get("path") or patch.get("value") or "")
                claim_id = target.split(".")[-1]
                before = len(claims)
                keep = [claim for claim in claims if str(claim.get("id") or "") != claim_id]
                if len(keep) < before:
                    claims[:] = keep
                    applied_actions.append(f"remove:{claim_id}")
                    changed = True
                else:
                    rejected_actions.append(f"remove:{claim_id}")
            elif action == "add":
                topic = str(patch.get("topic") or patch.get("value") or "").strip()
                if topic and questions is not None:
                    prompt = f"请结合原文，说明与「{topic}」相关的生产职责、边界和可验证结果。"
                    if prompt not in {str(item.get("question") or "") for item in questions}:
                        questions.append(
                            {
                                "id": f"Q{len(questions) + 1:02d}",
                                "question": prompt,
                                "knowledge_point": topic[:40],
                                "difficulty": "medium",
                                "scoring_rubric": "原文职责 40%，生产证据 40%，边界与验证 20%",
                            }
                        )
                        applied_actions.append(f"add_question:{topic[:40]}")
                        changed = True
                else:
                    rejected_actions.append("add")
        note = str(issue.get("note") or issue.get("issue_type") or "")[:160]
        if note:
            item = f"Checker 复核：{note}"
            if item not in risks:
                risks.append(item)
    match_result["risks"] = risks

    if blocking:
        match_result["uncertainty"] = "high"
        for claim in claims:
            if claim.get("predicate") in {"has_skill", "covers_required_skill"}:
                claim["confidence"] = "low"
                claim["verification_required"] = True
            if claim.get("predicate") == "years_experience" and "missing_evidence" in blocking:
                claim["confidence"] = "low"
                claim["verification_required"] = True

    revised = _recompute_decision(match_result, blocking=bool(blocking), floor=decision_floor)
    if revised != match_result.get("decision"):
        applied_actions.append(f"decide:{match_result.get('decision')}->{revised}")
        changed = True
    match_result["decision"] = revised

    breakdown = dict(match_result.get("score_breakdown") or {})
    breakdown["checker_correction"] = {
        "applied": changed,
        "original_decision": original_decision,
        "revised_decision": match_result.get("decision"),
        "invalidated_issue_types": sorted(blocking),
        "claims_requiring_verification": sum(1 for claim in claims if claim.get("verification_required")),
        "applied_actions": applied_actions,
        "rejected_actions": rejected_actions,
    }
    match_result["score_breakdown"] = breakdown
    return {
        "applied": changed,
        "original_decision": original_decision,
        "revised_decision": str(match_result.get("decision") or "reject"),
        "issue_types": sorted(blocking),
        "applied_actions": applied_actions,
        "rejected_actions": rejected_actions,
    }
