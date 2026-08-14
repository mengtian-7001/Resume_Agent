"""Document text extraction helpers (PDF / DOCX) with magic-byte validation."""

from __future__ import annotations

import zipfile
from io import BytesIO

import fitz
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Reject pathological DOCX (zip bombs / oversized members) before python-docx parses.
_MAX_DOCX_UNCOMPRESSED = 40 * 1024 * 1024
_MAX_DOCX_MEMBER = 12 * 1024 * 1024
_MAX_DOCX_FILES = 4000


def detect_document_mime(raw: bytes) -> str | None:
    """Return canonical MIME from file signatures, or None if unrecognized."""
    if len(raw) >= 5 and raw[:5] == b"%PDF-":
        return PDF_MIME
    if len(raw) >= 4 and raw[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                names = set(zf.namelist())
                if "[Content_Types].xml" in names and any(
                    name.startswith("word/") for name in names
                ):
                    return DOCX_MIME
        except zipfile.BadZipFile:
            return None
    return None


def assert_safe_document(raw: bytes, declared_mime: str | None = None) -> str:
    """
    Validate bytes against magic / ZIP structure and optional declared MIME.
    Returns the detected MIME. Raises ValueError on mismatch or unsafe archives.
    """
    detected = detect_document_mime(raw)
    if detected is None:
        raise ValueError("无法识别文件类型，请上传真实的 PDF 或 DOCX。")

    declared = (declared_mime or "").split(";")[0].strip().lower()
    if declared and declared not in {detected, "application/octet-stream"}:
        # Allow empty/octet-stream from storage; reject clear mismatches.
        aliases = {
            PDF_MIME: {"application/pdf", "application/x-pdf"},
            DOCX_MIME: {
                DOCX_MIME,
                "application/msword",
                "application/zip",
            },
        }
        if declared not in aliases.get(detected, {detected}):
            raise ValueError(
                f"文件内容与声明类型不符（声明={declared or '空'}，实际={detected}）。"
            )

    if detected == DOCX_MIME:
        _assert_docx_zip_bounds(raw)
    return detected


def extract_document_text(raw: bytes, mime_type: str) -> str:
    detected = assert_safe_document(raw, mime_type)
    if detected == PDF_MIME:
        pdf = fitz.open(stream=raw, filetype="pdf")
        try:
            return "\n".join(page.get_text() for page in pdf)
        finally:
            pdf.close()
    if detected == DOCX_MIME:
        return _extract_docx_text(raw)
    raise ValueError("不支持的文件格式")


def _assert_docx_zip_bounds(raw: bytes) -> None:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_DOCX_FILES:
                raise ValueError("DOCX 内文件数量过多，已拒绝解析。")
            total = 0
            for info in infos:
                if info.file_size > _MAX_DOCX_MEMBER:
                    raise ValueError("DOCX 内含过大条目，已拒绝解析。")
                total += max(0, int(info.file_size))
                if total > _MAX_DOCX_UNCOMPRESSED:
                    raise ValueError("DOCX 解压体积过大，已拒绝解析。")
                # Compression ratio sanity for zip bombs.
                if info.compress_size > 0 and info.file_size / max(1, info.compress_size) > 200:
                    raise ValueError("DOCX 压缩比异常，已拒绝解析。")
    except zipfile.BadZipFile as exc:
        raise ValueError("DOCX 不是有效的 ZIP 包。") from exc


def _extract_docx_text(raw: bytes) -> str:
    """Read body paragraphs + tables (incl. nested), plus header/footer text."""
    document = DocxDocument(BytesIO(raw))
    chunks: list[str] = []

    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            text = (block.text or "").strip()
            if text:
                chunks.append(text)
        elif isinstance(block, Table):
            table_text = _table_text(block)
            if table_text:
                chunks.append(table_text)

    for section in document.sections:
        for part_name in ("header", "footer"):
            part = getattr(section, part_name, None)
            if part is None:
                continue
            for paragraph in part.paragraphs:
                text = (paragraph.text or "").strip()
                if text:
                    chunks.append(text)
            for table in part.tables:
                table_text = _table_text(table)
                if table_text:
                    chunks.append(table_text)

    return "\n".join(chunks)


def _iter_block_items(parent):
    """Yield paragraphs and tables in document order (python-docx recipe)."""
    body = parent.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            nested = []
            for nested_table in cell.tables:
                nested_text = _table_text(nested_table)
                if nested_text:
                    nested.append(nested_text)
            cell_text = " ".join(
                (p.text or "").strip() for p in cell.paragraphs if (p.text or "").strip()
            )
            if nested:
                cell_text = " ".join(filter(None, [cell_text, *nested]))
            if cell_text:
                cells.append(cell_text)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)
