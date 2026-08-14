"""Atomic (preferred) and fallback persistence for screening candidate core rows."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("worker.persist")


def map_review_status(status: str | None) -> str:
    value = str(status or "fail").lower()
    return "pass" if value == "pass" else "fail"


def normalize_match_decision(value: Any) -> str:
    text = str(value or "review").strip().lower()
    if text in {"recommend", "review", "reject"}:
        return text
    return "review"


def persist_candidate_core(
    client: Any,
    *,
    workspace_id: str,
    screening_job_id: str,
    candidate_profile_id: str,
    match_payload: dict[str, Any],
    questions: list[dict[str, Any]],
    followups: list[dict[str, Any]],
    review: dict[str, Any],
    claims: list[dict[str, Any]] | None = None,
    db_lock: Any | None = None,
) -> dict[str, Any]:
    """Persist match + questions + checker (+ optional claims) atomically via RPC.

    Falls back to three PostgREST upserts if the RPC is missing.
    """
    def _run(fn: Callable[[], Any]) -> Any:
        if db_lock is None:
            return fn()
        with db_lock:
            return fn()

    match_payload = {
        **match_payload,
        "decision": normalize_match_decision(match_payload.get("decision")),
    }
    review_body = {
        "status": review.get("status"),
        "issues": review.get("issues") or [],
        "feedback": review.get("issues") or review.get("feedback") or [],
        "model": review.get("model") or "unknown",
    }
    claims_payload = None
    if claims is not None:
        claims_payload = [
            {
                "subject_type": c.get("subject_type") or "candidate",
                "predicate": c.get("predicate"),
                "value": c.get("value"),
                "normalized_value": str(c.get("value") or "").lower(),
                "confidence": c.get("confidence") or "medium",
                "status": c.get("status") or "proposed",
                "evidence": c.get("evidence") or [],
                "producer": c.get("producer") or "construction",
                "producer_version": c.get("producer_version"),
            }
            for c in claims
        ]

    def _rpc() -> dict[str, Any]:
        response = client.rpc(
            "persist_screening_candidate_core",
            {
                "p_workspace_id": workspace_id,
                "p_screening_job_id": screening_job_id,
                "p_candidate_profile_id": candidate_profile_id,
                "p_match": match_payload,
                "p_questions": questions,
                "p_followups": followups,
                "p_review": review_body,
                "p_claims": claims_payload,
            },
        ).execute()
        data = response.data
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return {"ok": True, "mode": "rpc"}
        return {**data, "mode": "rpc"}

    try:
        return _run(_rpc)
    except Exception as exc:
        message = str(exc)
        missing = "PGRST202" in message or "schema cache" in message or "persist_screening_candidate_core" in message
        type_mismatch = "42804" in message or "match_decision" in message
        if not missing and not type_mismatch:
            raise
        logger.warning("persist RPC unavailable or type-mismatched, falling back to multi-upsert: %s", message[:160])
        return _run(
            lambda: _fallback_upserts(
                client,
                workspace_id=workspace_id,
                screening_job_id=screening_job_id,
                candidate_profile_id=candidate_profile_id,
                match_payload=match_payload,
                questions=questions,
                followups=followups,
                review=review,
            )
        )


def _fallback_upserts(
    client: Any,
    *,
    workspace_id: str,
    screening_job_id: str,
    candidate_profile_id: str,
    match_payload: dict[str, Any],
    questions: list[dict[str, Any]],
    followups: list[dict[str, Any]],
    review: dict[str, Any],
) -> dict[str, Any]:
    client.table("match_results").upsert(
        {
            "screening_job_id": screening_job_id,
            "candidate_profile_id": candidate_profile_id,
            "score": match_payload["score"],
            "decision": match_payload["decision"],
            "hard_gate_pass": match_payload["hard_gate_pass"],
            "score_breakdown": match_payload.get("score_breakdown") or {},
            "evidence": match_payload.get("evidence") or [],
            "risks": match_payload.get("risks") or [],
            "interview_question": match_payload.get("interview_question"),
        },
        on_conflict="candidate_profile_id",
    ).execute()
    client.table("question_packs").upsert(
        {
            "workspace_id": workspace_id,
            "screening_job_id": screening_job_id,
            "candidate_profile_id": candidate_profile_id,
            "questions": questions,
            "followups": followups,
            "quality": {
                "question_count": len(questions),
                "followup_count": len(followups),
                "checker_status": review.get("status"),
            },
        },
        on_conflict="candidate_profile_id",
    ).execute()
    client.table("checker_reviews").upsert(
        {
            "workspace_id": workspace_id,
            "screening_job_id": screening_job_id,
            "candidate_profile_id": candidate_profile_id,
            "status": map_review_status(review.get("status")),
            "feedback": review.get("issues") or [],
            "model": review.get("model") or "unknown",
        },
        on_conflict="candidate_profile_id",
    ).execute()
    return {"ok": True, "mode": "fallback", "claims_written": 0}
