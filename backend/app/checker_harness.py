"""Bounded Construction → Checker correction loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .checker_contract import CheckerInputBuilder
from .checker_corrections import apply_checker_corrections

MAX_CHECKER_ROUNDS = 2


@dataclass(frozen=True)
class CheckerHarnessResult:
    output: Any
    review: dict[str, Any]
    rounds: int
    corrections: list[dict[str, Any]]


def review_requires_revision(review: dict[str, Any]) -> bool:
    """A review with actionable issues must return to Construction once."""
    if not list(review.get("issues") or []):
        return False
    return bool(
        review.get("status") in {"review", "fail", "degraded"}
        or review.get("degraded")
        or review.get("hard_degrade")
    )


def run_checker_harness(
    *,
    initial_output: Any,
    requirements: dict[str, Any],
    raw_candidate_profile: dict[str, Any],
    review: Callable[[Any], dict[str, Any]],
    revise: Callable[[list[dict[str, Any]]], Any],
    max_rounds: int = MAX_CHECKER_ROUNDS,
) -> CheckerHarnessResult:
    """Review complete context and allow at most one corrective reconstruction.

    ``max_rounds`` is deliberately capped at two: the initial Construction /
    Checker pass and one correction pass. Safe, local patch actions are applied
    immediately after *every* review, including the initial pass.
    """
    rounds_limit = max(1, min(int(max_rounds), MAX_CHECKER_ROUNDS))
    output = initial_output
    corrections: list[dict[str, Any]] = []
    final_review: dict[str, Any] = {}

    for round_number in range(1, rounds_limit + 1):
        checker_input = CheckerInputBuilder.build(output, requirements, raw_candidate_profile)
        final_review = dict(review(checker_input) or {})
        correction = apply_checker_corrections(
            output.match_result,
            output.claims,
            list(final_review.get("issues") or []),
            output.questions,
        )
        corrections.append(correction)
        final_review["correction"] = correction
        final_review["round"] = round_number

        if review_requires_revision(final_review) and round_number < rounds_limit:
            output = revise(list(final_review.get("issues") or []))
            continue
        break

    final_review["correction_rounds"] = len(corrections)
    final_review["correction_cap_reached"] = (
        len(corrections) == rounds_limit and review_requires_revision(final_review)
    )
    return CheckerHarnessResult(output, final_review, len(corrections), corrections)
