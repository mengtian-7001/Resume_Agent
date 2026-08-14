"""Single decision policy for Checker outcomes in workers and evaluations."""

from __future__ import annotations

from typing import Any


def checker_is_degraded(review: dict[str, Any] | None) -> bool:
    review = review or {}
    return bool(
        review.get("status") in {"fail", "degraded", "review"}
        or review.get("hard_degrade")
        or review.get("degraded")
    )


def apply_checker_review(decision: str, review: dict[str, Any] | None) -> dict[str, Any]:
    """Demote unsafe recommendations while preserving the original decision audit trail."""
    degraded = checker_is_degraded(review)
    final_decision = "review" if degraded and decision == "recommend" else decision
    return {
        "decision": final_decision,
        "checker_degraded": degraded,
        "checker_demotion": final_decision != decision,
        "checker_status": (review or {}).get("status") or "unknown",
    }
