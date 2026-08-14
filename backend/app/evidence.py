"""Evidence grounding / lightweight entailment checks for Judge citations."""

from __future__ import annotations

import re
from typing import Any


NEGATION_MARKERS = (
    "未", "没有", "无", "不曾", "并非", "不是", "缺乏", "不具备", "未参与", "未负责",
    "了解即可", "仅了解", "略懂", "不会",
)

STRENGTH_WEAK = ("了解", "熟悉基础", "做过 demo", "预研", "学习过", "接触过")
STRENGTH_STRONG = ("负责", "主导", "上线", "生产", "落地", "精通", "独立完成")


def normalize_cite(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _contains_window(hay: str, cite: str, window: int = 12) -> bool:
    if not cite:
        return False
    if cite in hay:
        return True
    if len(cite) < 8:
        return False
    step = max(4, window // 2)
    for i in range(0, max(1, len(cite) - window + 1), step):
        if cite[i : i + window] in hay:
            return True
    return False


def citation_in_source(cite: str, hay: str) -> bool:
    return _contains_window(hay, cite)


def negation_conflict(cite: str, hay: str) -> bool:
    """True when cite claims competence but nearby source text (or cite itself) negates it."""
    if not cite or not hay:
        return False
    if any(marker in cite for marker in NEGATION_MARKERS) and any(
        token in cite for token in ("负责", "主导", "熟练", "精通", "上线", "生产", *STRENGTH_STRONG)
    ):
        # e.g. “未负责生产级编排”
        return True
    # Find approximate location of cite in hay.
    idx = hay.find(cite[: min(12, len(cite))])
    if idx < 0:
        return False
    context = hay[max(0, idx - 18) : idx + min(len(cite), 18) + 18]
    if any(marker in context for marker in NEGATION_MARKERS):
        if any(token in cite for token in ("负责", "主导", "熟练", "精通", "上线", "生产")):
            return True
        if any(token in cite for token in STRENGTH_STRONG):
            return True
    return False


def strength_mismatch(cite: str, rationale: str) -> bool:
    """True when evidence is weak-skill language but rationale claims strong mastery."""
    weak = any(token in cite for token in STRENGTH_WEAK) or any(
        marker in cite for marker in NEGATION_MARKERS
    )
    strong_claim = any(token in (rationale or "") for token in ("精通", "资深", "生产级", "深度掌握"))
    return bool(weak and strong_claim)


def validate_judge_evidence(
    judge: dict[str, Any],
    requirements: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    jd_blob = normalize_cite(str(requirements.get("raw_text") or "") + str(requirements.get("title") or ""))
    resume_blob = normalize_cite(str(profile.get("raw_text") or ""))
    evidence = judge.get("evidence") if isinstance(judge.get("evidence"), list) else []
    rationale = str(judge.get("rationale") or "")
    grounded = 0
    grounded_citations: set[tuple[str, str]] = set()
    grounded_sources: set[str] = set()
    rejected_rows: list[str] = []
    flags: list[str] = []

    for row in evidence:
        if not isinstance(row, dict):
            continue
        cite_raw = str(row.get("text") or "")
        cite = normalize_cite(cite_raw)
        if len(cite) < 4:
            rejected_rows.append("too_short")
            continue
        source = str(row.get("source") or "").lower()
        if source not in {"jd", "resume"}:
            rejected_rows.append(f"invalid_source:{source or 'missing'}")
            flags.append("invalid_source")
            continue
        hay = jd_blob if source == "jd" else resume_blob
        if not citation_in_source(cite, hay):
            rejected_rows.append(cite_raw[:40] or "ungrounded")
            continue
        if source == "resume" and negation_conflict(cite, resume_blob):
            rejected_rows.append(f"negation:{cite_raw[:30]}")
            flags.append("negation_conflict")
            continue
        if strength_mismatch(cite_raw, rationale):
            rejected_rows.append(f"strength:{cite_raw[:30]}")
            flags.append("strength_mismatch")
            continue
        grounded += 1
        grounded_citations.add((source, cite))
        grounded_sources.add(source)

    # A single lexical hit is not enough to justify a model score. Require at
    # least two distinct grounded citations, including candidate-side evidence.
    ok = len(grounded_citations) >= 2 and "resume" in grounded_sources
    return {
        "ok": ok,
        "grounded": grounded,
        "total": len(evidence),
        "rejected": rejected_rows[:6],
        "flags": sorted(set(flags))[:6],
        "mode": "entailment_lite",
    }


def validate_claim_evidence(claim: dict[str, Any], source_text: str) -> dict[str, Any]:
    """Check a Construction claim's evidence cites against a source blob."""
    hay = normalize_cite(source_text)
    rows = claim.get("evidence") if isinstance(claim.get("evidence"), list) else []
    grounded = 0
    for row in rows:
        text = row.get("text") if isinstance(row, dict) else str(row or "")
        cite = normalize_cite(str(text))
        if len(cite) >= 4 and citation_in_source(cite, hay):
            if not negation_conflict(cite, hay):
                grounded += 1
    return {"ok": grounded >= 1 or not rows, "grounded": grounded, "total": len(rows)}
