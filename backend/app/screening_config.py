"""Workspace-level screening thresholds and hard-gate toggles."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_SCREENING_CONFIG: dict[str, Any] = {
    "hard_gates": {
        "min_years": {"enabled": True},
        "education": {"enabled": True},
        # 50% coverage is enough to pass the gate; scoring still penalizes gaps.
        "must_have_skills": {"enabled": True, "min_coverage": 0.5},
    },
    "score_thresholds": {
        "recommend_min": 75,
        "review_min": 60,
    },
}


def merge_screening_config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_SCREENING_CONFIG)
    if not config:
        return merged
    for section in ("hard_gates", "score_thresholds"):
        if section in config and isinstance(config[section], dict):
            merged[section].update(config[section])
            if section == "hard_gates":
                for gate in ("min_years", "education", "must_have_skills"):
                    if gate in config["hard_gates"] and isinstance(config["hard_gates"][gate], dict):
                        merged["hard_gates"][gate].update(config["hard_gates"][gate])
    return merged


def evaluate_hard_gates(
    *,
    years_ok: bool,
    education_ok: bool,
    required_coverage: float,
    config: dict[str, Any],
) -> bool:
    gates = config["hard_gates"]
    if gates.get("min_years", {}).get("enabled", True) and not years_ok:
        return False
    if gates.get("education", {}).get("enabled", True) and not education_ok:
        return False
    if gates.get("must_have_skills", {}).get("enabled", True):
        min_coverage = float(gates.get("must_have_skills", {}).get("min_coverage", 0.5))
        if required_coverage < min_coverage:
            return False
    return True


def decide(total_score: float, hard_gate_pass: bool, config: dict[str, Any]) -> str:
    if not hard_gate_pass:
        return "reject"
    thresholds = config["score_thresholds"]
    recommend_min = float(thresholds.get("recommend_min", 75))
    review_min = float(thresholds.get("review_min", 60))
    if total_score >= recommend_min:
        return "recommend"
    if total_score >= review_min:
        return "review"
    return "reject"
