#!/usr/bin/env python3
"""Held-out document evaluation (credibility-oriented).

Differences vs matching_eval 60/60 fixtures:
  - Reads real DOCX/PDF bytes and extracts text (parse path under test)
  - Labels live in testdata/heldout_labels.json only (not embedded in resume prose)
  - Strips known label-leak phrases before scoring
  - Does NOT use generator structured profiles

Usage:
  PYTHONPATH=backend ./backend/.venv/bin/python backend/scripts/run_heldout_doc_eval.py
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from collections import Counter

from app.agents import MockCheckerAgent, MockConstructionAgent
from app.checker_policy import apply_checker_review
from app.document_text import extract_document_text
from app.worker import ScreeningWorker

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "testdata" / "documents" / "manifest.json"
LABELS = ROOT / "testdata" / "heldout_labels.json"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"


def _mime(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return PDF
    return DOCX


def _load_text(rel: str) -> str:
    path = ROOT / rel
    return extract_document_text(path.read_bytes(), _mime(path))


def _strip_leaks(text: str, patterns: list[str]) -> str:
    out = text
    for pat in patterns:
        out = out.replace(pat, "")
    # Also drop sentences that explicitly announce the expected decision.
    out = re.sub(r"[^。\n]{0,40}应\s*reject[^。\n]{0,40}。?", "。", out, flags=re.IGNORECASE)
    return out


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    decision_map = labels.get("decision_map") or {}
    leaks = list(labels.get("leak_patterns") or [])

    jd_by_id = {row["job_id"]: row for row in manifest.get("jobs") or []}
    resume_by_id = {row["resume_id"]: row for row in manifest.get("resumes") or []}

    construction = MockConstructionAgent()
    checker = MockCheckerAgent()

    total = 0
    correct = 0
    parse_fail = 0
    misses: list[str] = []
    confusion: Counter[tuple[str, str]] = Counter()

    for pair in labels.get("pairs") or []:
        jd = jd_by_id.get(pair["jd_id"])
        resume = resume_by_id.get(pair["resume_id"])
        if not jd or not resume:
            misses.append(f"MISSING_DOC {pair}")
            continue
        jd_path = jd.get("docx") or jd.get("pdf")
        resume_path = resume.get("docx") or resume.get("pdf")
        try:
            jd_text = _strip_leaks(_load_text(jd_path), leaks)
            resume_text = _strip_leaks(_load_text(resume_path), leaks)
        except Exception as exc:  # noqa: BLE001
            parse_fail += 1
            misses.append(f"PARSE_FAIL {pair['resume_id']}: {exc}")
            continue
        if len(jd_text.strip()) < 40 or len(resume_text.strip()) < 40:
            parse_fail += 1
            misses.append(f"THIN_TEXT {pair['jd_id']}×{pair['resume_id']}")
            continue

        requirements = ScreeningWorker._extract_requirements(jd_text)
        profile = ScreeningWorker._extract_profile(resume_text)
        output = construction.analyze(requirements, profile)
        review = checker.review(output)
        decision = apply_checker_review(str(output.match_result.get("decision") or "reject"), review)["decision"]

        expected = decision_map.get(pair["expected"], pair["expected"])
        # Also accept generator label synonyms via resume metadata only for reporting,
        # never as model input.
        total += 1
        ok = decision == expected
        correct += int(ok)
        confusion[(str(expected), str(decision))] += 1
        if not ok:
            misses.append(
                f"{pair['jd_id']}×{pair['resume_id']}: got={decision} expected={expected} "
                f"score={output.match_result.get('score')} years={profile.get('years_experience')}"
            )

    print(
        f"heldout_doc_eval correct={correct}/{total} "
        f"parse_fail={parse_fail} accuracy={round(correct / total, 4) if total else 0}"
    )
    for line in misses[:40]:
        print(f"  MISS {line}")

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

    if parse_fail:
        print("FAIL: document parse failures")
        return 1
    if total < 10:
        print("FAIL: too few held-out pairs")
        return 1
    # Held-out bar: 70% agreement and no rounding down.
    floor = math.ceil(total * 0.70)
    if correct < floor:
        print(f"FAIL: correct={correct} < floor={floor}")
        return 1
    print(f"PASS (floor={floor}; not a 100% claim)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
