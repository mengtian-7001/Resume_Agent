#!/usr/bin/env python3
"""Evaluate matching on real sample DOCX files + human labels.

Usage:
  PYTHONPATH=backend ./backend/.venv/bin/python backend/scripts/run_sample_doc_eval.py
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

from app.agents import MockCheckerAgent, MockConstructionAgent
from app.checker_policy import apply_checker_review
from app.document_text import extract_document_text
from app.worker import ScreeningWorker

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "samples" / "manifest.json"
LABELS = ROOT / "testdata" / "sample_doc_labels.json"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def load_text(rel_path: str) -> str:
    raw = (ROOT / rel_path).read_bytes()
    return extract_document_text(raw, DOCX_MIME)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    jd_by_id = {item["id"]: item for item in manifest["jd"]}
    resume_by_id = {item["id"]: item for item in manifest["resumes"]}

    # 1) Parse smoke: every labeled DOCX must yield extractable text.
    parse_fail = 0
    for item in list(jd_by_id.values()) + list(resume_by_id.values()):
        text = load_text(item["path"])
        if len(text.strip()) < 40:
            parse_fail += 1
            print(f"  PARSE_FAIL {item['path']}")
    if parse_fail:
        print(f"FAIL: parse_fail={parse_fail}")
        return 1

    construction = MockConstructionAgent()
    checker = MockCheckerAgent()
    total = 0
    correct = 0
    errors: list[str] = []
    confusion: Counter[tuple[str, str]] = Counter()

    for pair in labels["pairs"]:
        jd = jd_by_id[pair["jd_id"]]
        resume = resume_by_id[pair["resume_id"]]
        jd_text = load_text(jd["path"])
        resume_text = load_text(resume["path"])
        requirements = ScreeningWorker._extract_requirements(jd_text)
        profile = ScreeningWorker._extract_profile(resume_text)
        profile["name"] = resume["title"]
        if resume.get("years") is not None:
            # Manifest years act as human-verified overlay when date parsing is ambiguous.
            profile["years_experience"] = max(int(profile.get("years_experience") or 0), int(resume["years"]))
        output = construction.analyze(requirements, profile)
        review = checker.review(output)
        decision = apply_checker_review(output.match_result["decision"], review)["decision"]
        total += 1
        expected = pair["expected"]
        ok = decision == expected
        correct += int(ok)
        confusion[(str(expected), str(decision))] += 1
        if not ok:
            errors.append(
                f"{pair['jd_id']} × {pair['resume_id']}: got={decision} expected={expected} "
                f"score={output.match_result.get('score')} years={profile.get('years_experience')} "
                f"title={requirements.get('title')} notes={pair.get('notes')}"
            )

    print(f"sample_doc_eval parse_ok decisions={correct}/{total}")
    for err in errors:
        print(f"  MISS {err}")
    labels_seen = ("recommend", "review", "reject")
    f1s: list[float] = []
    for label in labels_seen:
        tp = confusion[(label, label)]
        fp = sum(confusion[(expected, label)] for expected in labels_seen if expected != label)
        fn = sum(confusion[(label, actual)] for actual in labels_seen if actual != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    recommend_fp = sum(
        count for (expected, actual), count in confusion.items() if actual == "recommend" and expected != "recommend"
    )
    reject_fn = sum(
        count for (expected, actual), count in confusion.items() if actual == "reject" and expected != "reject"
    )
    print(
        f"metrics macro_f1={sum(f1s) / len(f1s):.4f} "
        f"recommend_false_positive={recommend_fp} reject_false_negative={reject_fn}"
    )
    if total == 0:
        return 1
    # Real-doc floor: ≥75% agreement with human labels under mock agents (raise bar vs simple majority).
    threshold = max(1, int(math.ceil(total * 0.75)))
    if correct < threshold:
        print(f"FAIL: correct={correct} < floor={threshold} ({correct / total:.0%})")
        return 1
    print(f"PASS floor={threshold} accuracy={correct / total:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
