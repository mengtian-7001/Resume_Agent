"""Graded-trust helpers for agent memory recall."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

USABLE_TRUST = {"model_checked", "source_verified", "human_verified", "human_or_source_verified"}
UNUSABLE_TRUST = {"revoked", "expired", "untrusted"}
TRUSTED_FOR_SCORING = {"human_verified", "source_verified", "human_or_source_verified"}
# Process outcomes and question-quality notes must never raise a later score.
NON_SCORING_MEMORY_TYPES = {"recruiter_calibration", "recruiter_outcome", "question_pattern"}

CANDIDATE_SCOPED_FEEDBACK = {"decision", "evidence", "candidate_status"}
JOB_REUSABLE_FEEDBACK = {"question"}
NEGATIVE_FEEDBACK = {"too_high", "too_low", "insufficient", "ineffective", "not_entered"}
FEEDBACK_TITLE_MIN_SCORE = 40.0
FEEDBACK_COLUMNS = (
    "id,feedback_type,value,comment,created_at,screening_job_id,"
    "candidate_profile_id,job_title,skills,evidence_id,target_skill,polarity"
)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalized_trust_level(row: dict[str, Any]) -> str:
    trust = str(row.get("trust_level") or "").strip().lower()
    if trust in USABLE_TRUST or trust in UNUSABLE_TRUST:
        return trust
    return "untrusted"


def memory_is_usable(row: dict[str, Any] | None, now: datetime | None = None) -> bool:
    if not isinstance(row, dict):
        return False
    trust = normalized_trust_level(row)
    if trust in UNUSABLE_TRUST or trust not in USABLE_TRUST:
        return False
    expires = _parse_time(row.get("expires_at"))
    if expires and expires <= (now or datetime.now(timezone.utc)):
        return False
    return True


def is_trusted_for_scoring(row: dict[str, Any] | None) -> bool:
    if not memory_is_usable(row):
        return False
    memory_type = str((row or {}).get("memory_type") or "")
    if memory_type in NON_SCORING_MEMORY_TYPES:
        return False
    return normalized_trust_level(row or {}) in TRUSTED_FOR_SCORING


def filter_memory_hits(hits: list[dict[str, Any]] | None, now: datetime | None = None) -> list[dict[str, Any]]:
    usable: list[dict[str, Any]] = []
    for row in hits or []:
        if not memory_is_usable(row, now=now):
            continue
        usable.append({**row, "trust_level": normalized_trust_level(row)})
    return usable


def _feedback_type(row: dict[str, Any]) -> str:
    return str(row.get("feedback_type") or "").strip().lower()


def _feedback_value(row: dict[str, Any]) -> str:
    return str(row.get("value") or "").strip().lower()


def feedback_in_scope(
    row: dict[str, Any],
    *,
    job_id: str | None = None,
    candidate_id: str | None = None,
    job_title: str | None = None,
    skills: list[str] | None = None,
) -> bool:
    """Candidate-bound types stay on that person; only question templates may reuse a job."""
    ftype = _feedback_type(row)
    row_candidate = str(row.get("candidate_profile_id") or "")
    row_job = str(row.get("screening_job_id") or "")
    want_candidate = str(candidate_id or "")
    want_job = str(job_id or "")

    if ftype not in JOB_REUSABLE_FEEDBACK:
        return bool(want_candidate) and row_candidate == want_candidate

    if want_job and row_job == want_job:
        return True
    title = str(row.get("job_title") or "")
    current_title = str(job_title or "")
    if not title or not current_title:
        return False
    from .matching import lexical_overlap

    if lexical_overlap(current_title, title) < FEEDBACK_TITLE_MIN_SCORE:
        return False
    row_skills = {str(item) for item in (row.get("skills") or []) if item}
    current_skills = {str(item) for item in (skills or []) if item}
    if row_skills and current_skills:
        return bool(row_skills & current_skills)
    return True


def memory_from_feedback(row: dict[str, Any]) -> dict[str, Any]:
    value = _feedback_value(row)
    ftype = _feedback_type(row)
    comment = str(row.get("comment") or "")
    title = str(row.get("job_title") or "")
    evidence_id = str(row.get("evidence_id") or "")
    suffix = (
        f"{' · ' + title if title else ''}"
        f"{' · ' + evidence_id if evidence_id else ''}"
        f"{' · ' + comment if comment else ''}"
    )
    negative = value in NEGATIVE_FEEDBACK or str(row.get("polarity") or "") == "negative_calibration"
    if negative:
        return {
            "content": f"负向校准规则：{ftype or 'decision'}={value}{suffix}"[:240],
            "trust_level": "model_checked",
            "similarity": None,
            "memory_type": "recruiter_calibration",
            "trusted": False,
        }
    if ftype == "evidence" and value == "confirmed":
        return {
            "content": f"证据已确认：evidence={value}{suffix}"[:240],
            "trust_level": "human_verified",
            "similarity": None,
            "memory_type": "evidence_confirmation",
            "trusted": True,
        }
    if ftype == "question":
        return {
            "content": f"题目模式：question={value}{suffix}"[:240],
            "trust_level": "model_checked",
            "similarity": None,
            "memory_type": "question_pattern",
            "trusted": False,
        }
    if ftype == "candidate_status":
        return {
            "content": f"流程结果：candidate_status={value}{suffix}"[:240],
            "trust_level": "model_checked",
            "similarity": None,
            "memory_type": "recruiter_outcome",
            "trusted": False,
        }
    return {
        "content": f"招聘校准：{ftype or 'decision'}={value}{suffix}"[:240],
        "trust_level": "model_checked",
        "similarity": None,
        "memory_type": "recruiter_calibration",
        "trusted": False,
    }


def scoped_feedback_memories(
    rows: list[dict[str, Any]] | None,
    *,
    job_id: str | None = None,
    candidate_id: str | None = None,
    job_title: str | None = None,
    skills: list[str] | None = None,
) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if not feedback_in_scope(
            row,
            job_id=job_id,
            candidate_id=candidate_id,
            job_title=job_title,
            skills=skills,
        ):
            continue
        memories.append(memory_from_feedback(row))
    return memories


def _ilike_fragment(title: str) -> str:
    token = str(title or "").strip()[:16]
    for char in ("%", "_", ",", "*", '"', "'"):
        token = token.replace(char, "")
    return token.strip()


def _feedback_select(client: Any, workspace_id: str) -> Any:
    return (
        client.table("recruiter_feedback")
        .select(FEEDBACK_COLUMNS)
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
    )


def _execute_rows(builder: Any) -> list[dict[str, Any]]:
    try:
        data = builder.execute().data
    except Exception:
        return []
    return [row for row in (data or []) if isinstance(row, dict)]


def fetch_scoped_feedback_rows(
    client: Any,
    workspace_id: str,
    *,
    job_id: str | None = None,
    candidate_id: str | None = None,
    job_title: str | None = None,
    skills: list[str] | None = None,
    limit_each: int = 16,
) -> list[dict[str, Any]]:
    """Load candidate rows and question templates by index, not a workspace-wide recency window."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _extend(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            rid = str(row.get("id") or "")
            key = rid or f"{row.get('candidate_profile_id')}:{row.get('feedback_type')}:{row.get('value')}:{row.get('created_at')}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)

    if candidate_id:
        _extend(
            _execute_rows(
                _feedback_select(client, workspace_id)
                .eq("candidate_profile_id", candidate_id)
                .limit(limit_each)
            )
        )
    if job_id:
        _extend(
            _execute_rows(
                _feedback_select(client, workspace_id)
                .eq("screening_job_id", job_id)
                .eq("feedback_type", "question")
                .limit(limit_each)
            )
        )
    fragment = _ilike_fragment(job_title or "")
    if fragment:
        _extend(
            _execute_rows(
                _feedback_select(client, workspace_id)
                .eq("feedback_type", "question")
                .ilike("job_title", f"%{fragment}%")
                .limit(limit_each)
            )
        )
    return [
        row
        for row in merged
        if feedback_in_scope(
            row,
            job_id=job_id,
            candidate_id=candidate_id,
            job_title=job_title,
            skills=skills,
        )
    ]


def fetch_scoped_feedback_memories(
    client: Any,
    workspace_id: str,
    *,
    job_id: str | None = None,
    candidate_id: str | None = None,
    job_title: str | None = None,
    skills: list[str] | None = None,
    limit_each: int = 16,
) -> list[dict[str, Any]]:
    rows = fetch_scoped_feedback_rows(
        client,
        workspace_id,
        job_id=job_id,
        candidate_id=candidate_id,
        job_title=job_title,
        skills=skills,
        limit_each=limit_each,
    )
    return scoped_feedback_memories(
        rows,
        job_id=job_id,
        candidate_id=candidate_id,
        job_title=job_title,
        skills=skills,
    )
