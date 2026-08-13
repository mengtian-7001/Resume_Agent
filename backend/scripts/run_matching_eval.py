#!/usr/bin/env python3
"""Run the matching fixture set against the Agent contract (no frontend).

Every sample MUST go through both agents:
  1) MockConstructionAgent.analyze
  2) MockCheckerAgent.review

Usage:
    PYTHONPATH=backend backend/.venv/bin/python backend/scripts/run_matching_eval.py
    PYTHONPATH=backend backend/.venv/bin/python backend/scripts/run_matching_eval.py --rounds 100

Fixture labels:
  recommend → recommend
  gray      → review
  reject    → reject
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.agents import MockCheckerAgent, MockConstructionAgent


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "testdata" / "matching_eval"
EXPECTED_ROUTE = {"recommend": "recommend", "gray": "review", "reject": "reject"}


def load_pairs() -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    files = sorted(p for p in FIXTURE_DIR.glob("*.json") if not p.name.startswith("_"))
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for case in payload.get("cases", []):
            job = case["job"]
            requirements = dict(job["structured"])
            requirements["title"] = job["title"]
            requirements["raw_text"] = job.get("raw_text") or ""
            for resume in case["resumes"]:
                profile = dict(resume["structured"])
                profile["raw_text"] = resume.get("raw_text") or ""
                pairs.append(
                    {
                        "source": path.name,
                        "case_id": case["case_id"],
                        "resume_id": resume["resume_id"],
                        "label": resume["ground_truth"].get("label"),
                        "expected": EXPECTED_ROUTE[resume["ground_truth"]["expected_decision"]],
                        "requirements": requirements,
                        "profile": profile,
                    }
                )
    return pairs


def run(*, rounds: int = 1, max_mismatches: int = 30) -> dict[str, Any]:
    construction = MockConstructionAgent()
    checker = MockCheckerAgent()
    pairs = load_pairs()
    if not pairs:
        raise SystemExit(f"No fixtures found under {FIXTURE_DIR}")

    evaluated = 0
    correct = 0
    checker_calls = 0
    checker_pass = 0
    checker_fail = 0
    confusion: Counter[tuple[str, str]] = Counter()
    label_stats: Counter[str] = Counter()
    issue_stats: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []
    checker_fail_examples: list[dict[str, Any]] = []
    started = time.perf_counter()

    for round_idx in range(rounds):
        for pair in pairs:
            output = construction.analyze(pair["requirements"], pair["profile"])
            review = checker.review(output)
            checker_calls += 1
            if review.get("status") == "pass":
                checker_pass += 1
            else:
                checker_fail += 1
                for issue in review.get("issues") or []:
                    issue_stats[str(issue.get("issue_type") or "unknown")] += 1
                if len(checker_fail_examples) < max_mismatches:
                    checker_fail_examples.append(
                        {
                            "round": round_idx + 1,
                            "case_id": pair["case_id"],
                            "resume_id": pair["resume_id"],
                            "decision": output.match_result["decision"],
                            "issues": review.get("issues") or [],
                        }
                    )

            actual = output.match_result["decision"]
            expected = pair["expected"]
            evaluated += 1
            label_stats[pair["label"] or expected] += 1
            confusion[(expected, actual)] += 1
            if actual == expected:
                correct += 1
            elif len(errors) < max_mismatches:
                errors.append(
                    {
                        "round": round_idx + 1,
                        "case_id": pair["case_id"],
                        "resume_id": pair["resume_id"],
                        "label": pair["label"],
                        "expected": expected,
                        "actual": actual,
                        "score": output.match_result["score"],
                        "risks": output.match_result["risks"],
                        "checker_status": review.get("status"),
                        "checker_issues": review.get("issues") or [],
                    }
                )

    if checker_calls != evaluated:
        raise RuntimeError(
            f"Checker must run on every sample: calls={checker_calls} evaluated={evaluated}"
        )

    elapsed = time.perf_counter() - started
    matrix = {
        f"{expected}->{actual}": count
        for (expected, actual), count in sorted(confusion.items())
    }
    return {
        "fixture_dir": str(FIXTURE_DIR),
        "agents": ["MockConstructionAgent.analyze", "MockCheckerAgent.review"],
        "unique_samples": len(pairs),
        "rounds": rounds,
        "evaluated": evaluated,
        "correct": correct,
        "accuracy": round(correct / evaluated, 4) if evaluated else 0,
        "checker_calls": checker_calls,
        "checker_pass": checker_pass,
        "checker_fail": checker_fail,
        "checker_pass_rate": round(checker_pass / checker_calls, 4) if checker_calls else 0,
        "checker_issue_counts": dict(issue_stats),
        "elapsed_sec": round(elapsed, 3),
        "samples_per_sec": round(evaluated / elapsed, 1) if elapsed else 0,
        "label_counts": dict(label_stats),
        "confusion": matrix,
        "mismatch_examples": errors,
        "checker_fail_examples": checker_fail_examples,
        "mismatch_examples_capped": len(errors) >= max_mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline matching eval (no frontend)")
    parser.add_argument("--rounds", type=int, default=1, help="Repeat the full fixture set N times")
    parser.add_argument("--fail-on-mismatch", action="store_true")
    parser.add_argument(
        "--fail-on-checker",
        action="store_true",
        help="Exit non-zero if any Checker review fails",
    )
    parser.add_argument("--max-mismatches", type=int, default=30, help="Cap printed mismatch examples")
    args = parser.parse_args()
    if args.rounds < 1:
        raise SystemExit("--rounds must be >= 1")

    result = run(rounds=args.rounds, max_mismatches=args.max_mismatches)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["checker_calls"] != result["evaluated"]:
        raise SystemExit("Checker was not invoked for every sample")
    if args.fail_on_mismatch and result["correct"] < result["evaluated"]:
        raise SystemExit(1)
    if args.fail_on_checker and result["checker_fail"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
