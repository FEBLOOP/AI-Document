from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .scoring import calculate_scoring


NAVY = "17324D"
BLUE = "2878B5"
LIGHT_BLUE = "EAF3F9"
LIGHT_GRAY = "F4F6F8"


def _text(value: Any) -> str:
    if value is None:
        return "-"
    rendered = str(value).strip()
    return rendered or "-"


def _set_cell_shading(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = properties.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        properties.append(shading)
    shading.set(qn("w:fill"), fill)


def _set_repeat_table_header(row) -> None:
    properties = row._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    properties.append(repeat)


def _set_font(run, name: str = "TH Sarabun New", size: float = 14, bold: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)


def _configure_styles(document: Document) -> None:
    styles = document.styles
    definitions = {
        "Normal": ("TH Sarabun New", 14, False, NAVY),
        "Title": ("TH Sarabun New", 26, True, NAVY),
        "Heading 1": ("TH Sarabun New", 20, True, NAVY),
        "Heading 2": ("TH Sarabun New", 17, True, BLUE),
        "Heading 3": ("TH Sarabun New", 15, True, NAVY),
    }
    for style_name, (font_name, size, bold, color) in definitions.items():
        style = styles[style_name]
        style.font.name = font_name
        style.font.size = Pt(size)
        style.font.bold = bold
        style.font.color.rgb = RGBColor.from_string(color)
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), font_name)
    if "IOP Bullet" not in styles:
        bullet = styles.add_style("IOP Bullet", WD_STYLE_TYPE.PARAGRAPH)
        bullet.base_style = styles["Normal"]
        bullet.paragraph_format.left_indent = Cm(0.6)
        bullet.paragraph_format.first_line_indent = Cm(-0.4)


def _add_page_number(paragraph) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, end])
    _set_font(run, size=10)


def _add_bullets(document: Document, values: list[Any]) -> None:
    for value in values or ["-"]:
        paragraph = document.add_paragraph(style="IOP Bullet")
        paragraph.add_run(f"• {_text(value)}")


def _fill_table_header(row, labels: list[str]) -> None:
    for cell, label in zip(row.cells, labels):
        _set_cell_shading(cell, LIGHT_BLUE)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        paragraph = cell.paragraphs[0]
        run = paragraph.add_run(label)
        _set_font(run, size=12, bold=True)
    _set_repeat_table_header(row)


def _add_criterion(document: Document, criterion: dict[str, Any]) -> None:
    document.add_heading(
        f"{_text(criterion.get('criterion_id'))}  {_text(criterion.get('criterion'))}",
        level=3,
    )
    score = criterion.get("score")
    score_text = f"{score}/5" if isinstance(score, (int, float)) else "ยังไม่มีคะแนน"
    confidence = criterion.get("confidence")
    confidence_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else "-"
    status = document.add_table(rows=1, cols=6)
    status.style = "Table Grid"
    values = ["คะแนน", score_text, "ความเชื่อมั่น", confidence_text, "สถานะ", _text(criterion.get("status"))]
    for index, (cell, value) in enumerate(zip(status.rows[0].cells, values)):
        _set_cell_shading(cell, LIGHT_GRAY)
        run = cell.paragraphs[0].add_run(value)
        _set_font(run, size=12, bold=index % 2 == 0)

    document.add_paragraph(_text(criterion.get("summary")))
    strengths = criterion.get("strengths") or []
    improvements = criterion.get("areas_for_improvement") or []
    if strengths:
        document.add_heading("จุดแข็ง", level=3)
        _add_bullets(document, strengths)
    if improvements:
        document.add_heading("ประเด็นที่ควรปรับปรุง", level=3)
        _add_bullets(document, improvements)

    citations = criterion.get("citations") or []
    if citations:
        document.add_heading("หลักฐานอ้างอิง", level=3)
        table = document.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        _fill_table_header(table.rows[0], ["Chunk", "หน้า", "ข้อความหลักฐาน"])
        for citation in citations:
            start = citation.get("page_start") or citation.get("page")
            end = citation.get("page_end") or start
            page = start if start == end else f"{start}-{end}"
            excerpt = citation.get("text") or citation.get("excerpt") or "-"
            if len(str(excerpt)) > 600:
                excerpt = str(excerpt)[:600].rstrip() + "…"
            row = table.add_row()
            for cell, value in zip(
                row.cells,
                [citation.get("chunk_id"), page, excerpt],
            ):
                run = cell.paragraphs[0].add_run(_text(value))
                _set_font(run, size=11)
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP


def build_docx_report(
    phase06: dict[str, Any],
    output_path: str | Path,
    rubric: dict[str, Any] | None = None,
) -> Path:
    """Render a validated Phase 06 package as an editable Thai Word report."""
    if not isinstance(phase06.get("dimension_summaries"), list):
        raise ValueError("Input is not a Phase 06 summary: dimension_summaries is required.")

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document_id = _text(phase06.get("document_id"))
    scoring = calculate_scoring(phase06, rubric)
    document = Document()
    _configure_styles(document)

    section = document.sections[0]
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(1.8)
    section.right_margin = Cm(1.8)
    footer = section.footer.paragraphs[0]
    footer.text = f"IOP Assessment Report - {document_id}                                      หน้า "
    _add_page_number(footer)

    properties = document.core_properties
    properties.title = f"IOP Assessment Report - {document_id}"
    properties.author = "AI-Document"
    properties.subject = "IOP assessment summary"

    title = document.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run("รายงานผลการประเมิน IOP")
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run(f"รหัสเอกสาร: {document_id}\n")
    subtitle.add_run(
        f"แหล่งสรุป: {_text(phase06.get('summary_provider'))}"
        + (f" / {_text(phase06.get('summary_model'))}" if phase06.get("summary_model") else "")
    )

    document.add_heading("ผลคะแนนองค์กร", level=1)
    if scoring["status"] == "complete":
        score_table = document.add_table(rows=1, cols=4)
        score_table.style = "Table Grid"
        values = [
            "คะแนนรวมองค์กร",
            f"{scoring['overall_score_display']:.2f}/5",
            "Maturity Level",
            scoring["maturity_level"],
        ]
        for index, (cell, value) in enumerate(zip(score_table.rows[0].cells, values)):
            _set_cell_shading(cell, LIGHT_BLUE)
            run = cell.paragraphs[0].add_run(_text(value))
            _set_font(run, size=14, bold=index % 2 == 0 or index == 3)
        document.add_paragraph(_text(scoring["maturity_description"]))
    else:
        document.add_paragraph(
            f"สถานะ: incomplete — มีคะแนนถูกต้อง {scoring['scored_criteria']} "
            f"จาก {scoring['total_criteria']} เกณฑ์ จึงยังไม่แสดงคะแนนรวมและ Maturity Level"
        )
        missing = ", ".join(scoring["missing_criterion_ids"][:12]) or "-"
        if len(scoring["missing_criterion_ids"]) > 12:
            missing += " …"
        document.add_paragraph(f"เกณฑ์ที่ขาด: {missing}")

    document.add_heading("สรุปภาพรวม", level=1)
    document.add_paragraph(_text(phase06.get("overall_summary")))
    dimensions = phase06.get("dimension_summaries", [])
    overview = document.add_table(rows=1, cols=4)
    overview.style = "Table Grid"
    _fill_table_header(overview.rows[0], ["มิติ", "ชื่อมิติ", "จำนวนเกณฑ์", "คะแนนเฉลี่ย"])
    for dimension in dimensions:
        average = scoring["dimension_scores"].get(str(dimension.get("dimension_id")))
        values = [
            dimension.get("dimension_id"),
            dimension.get("dimension_name"),
            len(dimension.get("criterion_summaries", [])),
            f"{average:.2f}/5" if average is not None else "-",
        ]
        row = overview.add_row()
        for cell, value in zip(row.cells, values):
            run = cell.paragraphs[0].add_run(_text(value))
            _set_font(run, size=12)

    for dimension in dimensions:
        document.add_page_break()
        dimension_id = _text(dimension.get("dimension_id"))
        dimension_name = _text(dimension.get("dimension_name"))
        document.add_heading(f"{dimension_id}  {dimension_name}", level=1)
        average = scoring["dimension_scores"].get(dimension_id)
        if average is not None:
            banner = document.add_table(rows=1, cols=1)
            banner.style = "Table Grid"
            _set_cell_shading(banner.cell(0, 0), LIGHT_BLUE)
            run = banner.cell(0, 0).paragraphs[0].add_run(f"คะแนนเฉลี่ยมิติ {average:.2f}/5")
            _set_font(run, size=16, bold=True)
        document.add_paragraph(_text(dimension.get("summary")))

        strengths = dimension.get("strengths") or []
        improvements = dimension.get("areas_for_improvement") or []
        if strengths:
            document.add_heading("จุดแข็งของมิติ", level=2)
            _add_bullets(document, strengths)
        if improvements:
            document.add_heading("ประเด็นที่ควรปรับปรุงของมิติ", level=2)
            _add_bullets(document, improvements)
        document.add_heading("รายละเอียดรายเกณฑ์", level=2)
        for criterion in dimension.get("criterion_summaries", []):
            _add_criterion(document, criterion)

    document.save(target)
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError("Generated DOCX is empty.")
    return target
