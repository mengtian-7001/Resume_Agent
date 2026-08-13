#!/usr/bin/env python3
"""Generate sample JD/resume DOCX files for the upload picker."""

from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "testdata/matching_eval/part1_case001_003.json"
OUT = ROOT / "samples"

RESUME_PICKS = ["林知远", "韩沐辰", "周启明", "陈思齐", "吴晓岚", "孙博文", "赵予安", "郑小禾"]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "sample"


def write_docx(text: str, path: Path) -> None:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    manifest: dict[str, list[dict[str, str]]] = {"jd": [], "resumes": []}

    for index, case in enumerate(data["cases"][:3]):
        job = case["job"]
        file_id = f"jd-{slugify(job['title'])}"
        filename = f"{job['title']}.docx"
        path = OUT / f"{file_id}.docx"
        write_docx(job["raw_text"], path)
        manifest["jd"].append(
            {
                "id": file_id,
                "title": job["title"],
                "filename": filename,
                "path": f"samples/{file_id}.docx",
                "tag": ["Agent", "后端", "数据"][index],
            }
        )

    case1 = data["cases"][0]
    for resume in case1["resumes"]:
        name = resume["structured"]["name"]
        if name not in RESUME_PICKS:
            continue
        file_id = f"resume-{resume['resume_id'].lower()}"
        filename = f"{name} · 简历.docx"
        path = OUT / f"{file_id}.docx"
        write_docx(resume["raw_text"], path)
        label = resume["ground_truth"]["label"]
        manifest["resumes"].append(
            {
                "id": file_id,
                "title": name,
                "filename": filename,
                "path": f"samples/{file_id}.docx",
                "tag": {"good": "推荐", "partial": "复核", "poor": "不匹配"}[label],
            }
        )

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(manifest['jd'])} JD and {len(manifest['resumes'])} resume samples to {OUT}")


if __name__ == "__main__":
    main()
