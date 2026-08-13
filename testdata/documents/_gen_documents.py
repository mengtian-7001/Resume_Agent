#!/usr/bin/env python3
"""Generate DOCX/PDF test documents from matching_eval fixtures and random templates."""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from io import BytesIO
from pathlib import Path

import fitz
from docx import Document
from docx.shared import Pt

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "testdata" / "matching_eval"
OUT_DIR = Path(__file__).resolve().parent

SUPPORTED_SKILLS = [
    "Python",
    "LangChain",
    "Function Calling",
    "Multi-Agent",
    "Prompt Engineering",
    "LangGraph",
    "FastAPI",
    "MCP",
]

SURNAMES = ["林", "周", "陈", "王", "李", "赵", "刘", "孙", "吴", "郑"]
GIVEN = ["知远", "启明", "雨桐", "浩然", "思琪", "子涵", "文博", "嘉怡", "俊杰", "晓晨"]
COMPANIES = ["澄空智能", "拾光纪科技", "云栈信息", "星链数据", "北辰软件", "青禾互联"]
CITIES = ["上海", "北京", "杭州", "深圳", "成都"]

# macOS / Linux common CJK font paths for PDF text rendering
CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def normalize_jd_text(raw: str, title: str, min_years: int, education: str) -> str:
    text = raw.replace("【岗位名称】", "岗位名称：").replace("【职位名称】", "职位名称：")
    if not re.search(r"岗位名称[：:]", text) and not re.search(r"职位名称[：:]", text):
        text = f"岗位名称：{title}\n" + text
    if not re.search(r"\d+\s*年(?:及以上|以上).{0,12}(?:经验|开发)", text):
        text += f"\n1. {education}及以上学历，{min_years}年及以上 Python 开发经验。"
    return text.strip()


def normalize_resume_text(raw: str, years: int) -> str:
    if not re.search(r"\d+\s*年(?:相关|开发|工作)?经验", raw):
        raw = raw.rstrip() + f"\n合计 {years} 年开发经验。"
    return raw.strip()


def write_docx(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "PingFang SC"
    style.font.size = Pt(11)
    for line in text.splitlines():
        doc.add_paragraph(line)
    doc.save(path)


def find_cjk_font() -> str | None:
    for candidate in CJK_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def write_pdf(text: str, path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    fontfile = find_cjk_font()
    if not fontfile:
        return False

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    margin_x, margin_y = 50, 50
    line_height = 16
    y = margin_y
    max_width = 495

    for line in text.splitlines():
        if y > 780:
            page = doc.new_page(width=595, height=842)
            y = margin_y
        page.insert_text(
            (margin_x, y),
            line or " ",
            fontfile=fontfile,
            fontsize=11,
        )
        y += line_height

    doc.save(path)
    doc.close()
    return True


def load_fixture_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(EVAL_DIR.glob("part*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases.extend(payload["cases"])
    return cases


def pick_fixture_samples(cases: list[dict], rng: random.Random) -> tuple[list[dict], list[dict]]:
    jds: list[dict] = []
    resumes: list[dict] = []
    for case in cases:
        job = case["job"]
        jds.append(
            {
                "id": job["job_id"],
                "case_id": case["case_id"],
                "title": job["title"],
                "text": normalize_jd_text(
                    job["raw_text"],
                    job["title"],
                    job["structured"]["min_years"],
                    job["structured"]["education"],
                ),
                "source": "fixture",
            }
        )
        by_label: dict[str, list] = {"good": [], "partial": [], "poor": []}
        for resume in case["resumes"]:
            label = resume["ground_truth"]["label"]
            by_label.setdefault(label, []).append(resume)
        for label in ("good", "partial", "poor"):
            pool = by_label.get(label, [])
            if pool:
                chosen = rng.choice(pool)
                resumes.append(
                    {
                        "id": chosen["resume_id"],
                        "case_id": case["case_id"],
                        "jd_id": job["job_id"],
                        "label": label,
                        "expected_decision": chosen["ground_truth"]["expected_decision"],
                        "text": normalize_resume_text(
                            chosen["raw_text"],
                            chosen["structured"]["years_experience"],
                        ),
                        "source": "fixture",
                    }
                )
    return jds, resumes


def random_name(rng: random.Random) -> str:
    return rng.choice(SURNAMES) + rng.choice(GIVEN)


def build_random_jd(rng: random.Random, index: int) -> dict:
    city = rng.choice(CITIES)
    min_years = rng.randint(2, 5)
    education = rng.choice(["本科", "硕士"])
    core = rng.sample(SUPPORTED_SKILLS[:5], k=4)
    nice = rng.sample(SUPPORTED_SKILLS[4:], k=min(2, len(SUPPORTED_SKILLS) - 4))
    title = rng.choice(
        [
            "AI Agent / LLM 应用工程师",
            "智能体平台开发工程师",
            "LLM 应用后端工程师",
        ]
    )
    lines = [
        f"岗位名称：{title}",
        f"工作地点：{city}",
        "岗位职责：",
        "1. 负责企业 Copilot 与 Agent 编排，将业务 API 以 Function Calling 接入大模型。",
        "2. 基于 LangChain 设计 Multi-Agent 工作流，覆盖记忆、重试与人工接管。",
        "3. 编写 Prompt Engineering 方案并搭建评测回归，降低幻觉与工具误调用。",
        "任职要求：",
        f"1. {education}及以上学历，{min_years}年及以上 Python 开发经验。",
        f"2. 熟悉 {', '.join(core[:3])}，能独立交付 Agent 功能。",
        f"3. 具备 {core[3]} 实战经验，理解鉴权、幂等与失败重试。",
        f"4. 掌握 Prompt Engineering，能编写系统提示词与少样本示例。",
    ]
    if nice:
        lines.append(f"加分项：{', '.join(nice)}。")
    return {
        "id": f"RJD_{index:03d}",
        "case_id": f"RANDOM_{index:03d}",
        "title": title,
        "text": "\n".join(lines),
        "source": "random",
        "must_have_skills": core,
        "min_years": min_years,
        "education": education,
    }


def build_random_resume(rng: random.Random, jd: dict, index: int, scenario: str) -> dict:
    name = random_name(rng)
    min_years = jd["min_years"]
    education = jd["education"]
    must = jd["must_have_skills"]

    if scenario == "good":
        years = min_years + rng.randint(1, 3)
        edu = education
        skills = must + rng.sample(SUPPORTED_SKILLS, k=rng.randint(1, 2))
        expected = "recommend"
        label = "good"
        extra = "主导过生产级 Agent 编排，Function Calling 协议统一鉴权与幂等，Multi-Agent 日均调用稳定。"
    elif scenario == "partial":
        years = min_years
        edu = education
        skills = must[: max(1, len(must) - 1)]
        expected = "gray"
        label = "partial"
        extra = "参与过 LangChain 集成与 Prompt 调优，但 Multi-Agent 编排经验偏少，工具调用场景较浅。"
    elif scenario == "years_fail":
        years = max(1, min_years - 1)
        edu = education
        skills = must
        expected = "reject"
        label = "poor"
        extra = "技能栈与岗位相近，但工作年限不足，尚未独立负责过完整 Agent 上线。"
    else:  # education_fail
        years = min_years + 1
        edu = "大专"
        skills = must
        expected = "reject"
        label = "poor"
        extra = "项目经验丰富，但最高学历为大专，不满足岗位学历硬门槛。"

    company = rng.choice(COMPANIES)
    lines = [
        f"姓名：{name}",
        f"求职意向：{jd['title']}",
        f"教育经历：东湖理工大学 计算机科学与技术 {edu} 2016-2020",
        "工作经历：",
        f"{company} | Agent 平台工程师 | 2021.03-至今",
        extra,
        f"技能：{', '.join(dict.fromkeys(skills))}",
        f"合计 {years} 年开发经验。",
    ]
    return {
        "id": f"RCV_{index:03d}",
        "case_id": jd["case_id"],
        "jd_id": jd["id"],
        "label": label,
        "expected_decision": expected,
        "text": "\n".join(lines),
        "source": "random",
        "scenario": scenario,
    }


def emit_documents(
    jds: list[dict],
    resumes: list[dict],
    *,
    include_pdf: bool,
) -> dict:
    manifest: dict = {
        "version": "1.0",
        "locale": "zh-CN",
        "description": "E2E 文档解析测试集：JD 与简历 DOCX/PDF",
        "jobs": [],
        "resumes": [],
        "pairs": [],
    }

    for jd in jds:
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", jd["title"]).strip("_")[:40]
        base = OUT_DIR / "jd" / f"{jd['id']}_{slug}"
        docx_path = base.with_suffix(".docx")
        write_docx(jd["text"], docx_path)
        entry = {
            "job_id": jd["id"],
            "case_id": jd["case_id"],
            "title": jd["title"],
            "source": jd["source"],
            "docx": str(docx_path.relative_to(ROOT)),
        }
        if include_pdf:
            pdf_path = base.with_suffix(".pdf")
            if write_pdf(jd["text"], pdf_path):
                entry["pdf"] = str(pdf_path.relative_to(ROOT))
        manifest["jobs"].append(entry)

    for resume in resumes:
        base = OUT_DIR / "resumes" / resume["case_id"] / f"{resume['id']}_{resume['label']}"
        docx_path = base.with_suffix(".docx")
        write_docx(resume["text"], docx_path)
        entry = {
            "resume_id": resume["id"],
            "case_id": resume["case_id"],
            "jd_id": resume["jd_id"],
            "label": resume["label"],
            "expected_decision": resume["expected_decision"],
            "source": resume["source"],
            "docx": str(docx_path.relative_to(ROOT)),
        }
        if resume.get("scenario"):
            entry["scenario"] = resume["scenario"]
        if include_pdf:
            pdf_path = base.with_suffix(".pdf")
            if write_pdf(resume["text"], pdf_path):
                entry["pdf"] = str(pdf_path.relative_to(ROOT))
        manifest["resumes"].append(entry)
        manifest["pairs"].append({"jd_id": resume["jd_id"], "resume_id": resume["id"]})

    manifest["stats"] = {
        "jobs": len(manifest["jobs"]),
        "resumes": len(manifest["resumes"]),
        "pairs": len(manifest["pairs"]),
    }
    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def verify_parse(manifest: dict) -> list[str]:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.worker import extract_document_text, ScreeningWorker  # noqa: WPS433

    issues: list[str] = []
    for job in manifest["jobs"]:
        raw = (ROOT / job["docx"]).read_bytes()
        text = extract_document_text(
            raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        req = ScreeningWorker._extract_requirements(text)
        if req["title"] == "未命名岗位":
            issues.append(f"{job['job_id']}: title not parsed")
        if req["min_years"] == 0:
            issues.append(f"{job['job_id']}: min_years not parsed")

    for resume in manifest["resumes"]:
        raw = (ROOT / resume["docx"]).read_bytes()
        text = extract_document_text(
            raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        profile = ScreeningWorker._extract_profile(text)
        if profile["name"] == "未命名候选人":
            issues.append(f"{resume['resume_id']}: name not parsed")
        if profile["years_experience"] == 0:
            issues.append(f"{resume['resume_id']}: years not parsed")

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate JD/resume test documents.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--random-jds", type=int, default=2, help="Extra random JD count")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF generation")
    parser.add_argument("--verify", action="store_true", help="Run worker parse smoke check")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    cases = load_fixture_cases()
    jds, resumes = pick_fixture_samples(cases, rng)

    for i in range(1, args.random_jds + 1):
        jd = build_random_jd(rng, i)
        jds.append(jd)
        for scenario in ("good", "partial", "years_fail", "education_fail"):
            resumes.append(build_random_resume(rng, jd, i * 10 + len(resumes), scenario))

    manifest = emit_documents(jds, resumes, include_pdf=not args.no_pdf)

    print(f"Generated {manifest['stats']['jobs']} JDs, {manifest['stats']['resumes']} resumes")
    print(f"Manifest: {OUT_DIR / 'manifest.json'}")

    if args.verify:
        issues = verify_parse(manifest)
        if issues:
            print("Parse warnings:")
            for item in issues:
                print(f"  - {item}")
        else:
            print("Parse smoke check passed.")


if __name__ == "__main__":
    main()
