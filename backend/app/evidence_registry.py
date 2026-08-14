"""Locate recruiter-facing evidence as quotes that exist in source text.

Scoring and Checker may only treat an evidence_id as grounded after the quote
is found in the JD or resume. Inferred summaries are allowed for display, but
they never receive an evidence_id and cannot raise a recommendation.
"""

from __future__ import annotations

from typing import Any


def quote_in_source(quote: str, source_text: str) -> bool:
    return bool(quote) and bool(source_text) and quote in source_text


def locate_span(source_text: str, needle: str) -> tuple[int, int, str] | None:
    if not source_text or not needle:
        return None
    idx = source_text.find(needle)
    if idx < 0:
        idx = source_text.lower().find(needle.lower())
    if idx < 0:
        return None
    end = idx + len(needle)
    return idx, end, source_text[idx:end]


def expand_window(source_text: str, start: int, end: int, radius: int = 18) -> tuple[int, int, str]:
    left = max(0, start - radius)
    right = min(len(source_text), end + radius)
    while left > 0 and source_text[left] not in "。；;\n":
        left -= 1
        if start - left > 48:
            break
    if left > 0 and source_text[left] in "。；;\n":
        left += 1
    while right < len(source_text) and source_text[right] not in "。；;\n":
        right += 1
        if right - end > 48:
            break
    quote = source_text[left:right].strip()
    return left, left + len(quote), quote


def grounded_row(
    *,
    index: int,
    source: str,
    source_text: str,
    needle: str,
    supports: list[str],
    strength: str,
    document_id: str | None = None,
    expand: bool = False,
) -> dict[str, Any] | None:
    span = locate_span(source_text, needle)
    if not span:
        return None
    start, end, quote = span
    if expand:
        start, end, quote = expand_window(source_text, start, end)
    if not quote_in_source(quote, source_text):
        return None
    return {
        "evidence_id": f"EV-{index:03d}",
        "source": source,
        "document_id": document_id,
        "page": None,
        "block_index": None,
        "quote": quote,
        "char_start": start,
        "char_end": end,
        "supports": supports,
        "strength": strength,
        "text": quote,
        "type": supports[0] if supports else "quote",
    }


def inferred_row(*, source: str, text: str, supports: list[str], row_type: str) -> dict[str, Any]:
    return {
        "source": source,
        "quote": "",
        "text": text,
        "supports": supports,
        "strength": "inferred",
        "type": row_type,
    }


def is_grounded(item: dict[str, Any] | None, source_text: str) -> bool:
    if not isinstance(item, dict):
        return False
    quote = str(item.get("quote") or "")
    return bool(item.get("evidence_id")) and quote_in_source(quote, source_text)


def build_match_evidence(
    *,
    requirements: dict[str, Any],
    profile: dict[str, Any],
    matched_required: list[str],
    matched_preferred: list[str],
    missing_required: list[str],
    years: int,
    min_years: int,
    production_cues: list[str],
    job_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    resume_text = str(profile.get("raw_text") or "")
    jd_text = str(requirements.get("raw_text") or requirements.get("title") or "")
    resume_id = str((profile.get("document_id") or (job_context or {}).get("resume_document_id") or "") or "") or None
    rows: list[dict[str, Any]] = []
    index = 1

    for skill in matched_required:
        row = grounded_row(
            index=index,
            source="resume",
            source_text=resume_text,
            needle=skill,
            supports=["covers_required_skill", "has_skill"],
            strength="high",
            document_id=resume_id,
        )
        if row:
            rows.append(row)
            index += 1

    for skill in matched_preferred[:3]:
        row = grounded_row(
            index=index,
            source="resume",
            source_text=resume_text,
            needle=skill,
            supports=["preferred_skill"],
            strength="medium",
            document_id=resume_id,
        )
        if row:
            rows.append(row)
            index += 1

    year_needles = [f"{years}年", f"{years} 年", f"{years}年以上"]
    year_row = None
    for needle in year_needles:
        year_row = grounded_row(
            index=index,
            source="resume",
            source_text=resume_text,
            needle=needle,
            supports=["years_experience"],
            strength="medium",
            document_id=resume_id,
            expand=True,
        )
        if year_row:
            break
    if year_row:
        rows.append(year_row)
        index += 1

    education = str(profile.get("education") or "")
    if education:
        edu_row = grounded_row(
            index=index,
            source="resume",
            source_text=resume_text,
            needle=education,
            supports=["education"],
            strength="medium",
            document_id=resume_id,
        )
        if edu_row:
            rows.append(edu_row)
            index += 1

    for cue in production_cues[:4]:
        row = grounded_row(
            index=index,
            source="resume",
            source_text=resume_text,
            needle=cue,
            supports=["production_experience"],
            strength="high",
            document_id=resume_id,
            expand=True,
        )
        if row:
            rows.append(row)
            index += 1

    if not any(item.get("evidence_id") for item in rows):
        if matched_required:
            rows.append(
                inferred_row(
                    source="resume",
                    text=f"结构化技能覆盖：{', '.join(matched_required)}",
                    supports=["has_skill"],
                    row_type="skills",
                )
            )
        rows.append(
            inferred_row(
                source="resume",
                text=f"候选人 {years} 年经验，岗位要求 {min_years} 年",
                supports=["years_experience"],
                row_type="experience",
            )
        )
        if missing_required:
            rows.append(
                inferred_row(
                    source="resume",
                    text=f"简历未直接体现：{', '.join(missing_required)}",
                    supports=["skills_gap"],
                    row_type="skills_gap",
                )
            )
    elif missing_required:
        gap = grounded_row(
            index=index,
            source="jd",
            source_text=jd_text,
            needle=missing_required[0],
            supports=["skills_gap"],
            strength="low",
            document_id=str(requirements.get("document_id") or "") or None,
        )
        if gap:
            rows.append(gap)
        else:
            rows.append(
                inferred_row(
                    source="resume",
                    text=f"简历未直接体现：{', '.join(missing_required)}",
                    supports=["skills_gap"],
                    row_type="skills_gap",
                )
            )
    return rows
