x#!/usr/bin/env python3
"""Run the matching fixture set against the Agent contract.

Usage:
    PYTHONPATH=backend backend/.venv/bin/python backend/scripts/run_matching_eval.py

The script intentionally keeps its expected-decision mapping explicit:
fixture `gray` means the product route `review`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.agents import MockConstructionAgent


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "testdata" / "matching_eval"
EXPECTED_ROUTE = {"recommend": "recommend", "gray": "review", "reject": "reject"}


def run() -> dict[str, Any]:
    agent = MockConstructionAgent()
    evaluated = 0
    correct = 0
    errors: list[dict[str, Any]] = []
    files = sorted(FIXTURE_DIR.glob("*.json"))

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for case in payload.get("cases", []):
            job = case["job"]
            requirements = job["structured"]
            requirements["title"] = job["title"]
            for resume in case["resumes"]:
                expected = EXPECTED_ROUTE[resume["ground_truth"]["expected_decision"]]
                output = agent.analyze(requirements, resume["structured"])
                actual = output.match_result["decision"]
                evaluated += 1
                correct += actual == expected
                if actual != expected:
                    errors.append(
                        {
                            "case_id": case["case_id"],
                            "resume_id": resume["resume_id"],
                            "expected": expected,
                            "actual": actual,
                            "score": output.match_result["score"],
                            "risks": output.match_result["risks"],
                        }
                    )

    return {
        "fixture_files": [path.name for path in files],
        "evaluated": evaluated,
        "correct": correct,
        "accuracy": round(correct / evaluated, 4) if evaluated else 0,
        "mismatches": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-on-mismatch", action="store_true")
    args = parser.parse_args()
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_mismatch and result["mismatches"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
