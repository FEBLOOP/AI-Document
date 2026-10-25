from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#2878B5")
LIGHT_BLUE = colors.HexColor("#EAF3F9")
LIGHT_GRAY = colors.HexColor("#F4F6F8")
MID_GRAY = colors.HexColor("#6B7785")
GREEN = colors.HexColor("#247A52")
AMBER = colors.HexColor("#A66500")
RED = colors.HexColor("#B33A3A")


def _font_paths() -> tuple[Path, Path]:
    configured = os.getenv("IOP_PDF_FONT")
    if configured:
        regular = Path(configured)
        bold = Path(os.getenv("IOP_PDF_FONT_BOLD", configured))
        if regular.is_file() and bold.is_file():
            return regular, bold
        raise RuntimeError("IOP_PDF_FONT or IOP_PDF_FONT_BOLD does not point to a font file.")

    candidates = [
        (Path("C:/Windows/Fonts/tahoma.ttf"), Path("C:/Windows/Fonts/tahomabd.ttf")),
        (
            Path("/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/tlwg/Garuda.ttf"),
            Path("/usr/share/fonts/truetype/tlwg/Garuda-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            return regular, bold
    raise RuntimeError(
        "No Thai TrueType font was found. Set IOP_PDF_FONT and "
        "IOP_PDF_FONT_BOLD to Thai-capable .ttf files."
    )


def _register_fonts() -> tuple[str, str]:
    regular_path, bold_path = _font_paths()
    regular_name = "IOPThai"
    bold_name = "IOPThaiBold"
    if regular_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
    if bold_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
    pdfmetrics.registerFontFamily(
        "IOPThai",
        normal=regular_name,
        bold=bold_name,
        italic=regular_name,
        boldItalic=bold_name,
    )
    return regular_name, bold_name


def _text(value: Any) -> str:
    if value is None:
        return "-"
    rendered = str(value).strip()
    return rendered or "-"


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    safe = escape(_text(value)).replace("\n", "<br/>")
    return Paragraph(safe, style)


def _score_color(score: Any) -> colors.Color:
    if not isinstance(score, (int, float)):
        return MID_GRAY
    if score >= 4:
        return GREEN
    if score >= 3:
        return AMBER
    return RED


def _dimension_score(dimension: dict[str, Any]) -> float | None:
    scores = [
        item.get("score")
        for item in dimension.get("criterion_summaries", [])
        if isinstance(item.get("score"), (int, float))
    ]
    return round(sum(scores) / len(scores), 2) if scores else None


def _styles(regular_font: str, bold_font: str) -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "IOPTitle",
            parent=sample["Title"],
            fontName=bold_font,
            fontSize=24,
            leading=32,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=10 * mm,
            wordWrap="CJK",
        ),
        "subtitle": ParagraphStyle(
            "IOPSubtitle",
            parent=sample["Normal"],
            fontName=regular_font,
            fontSize=11,
            leading=18,
            textColor=MID_GRAY,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "h1": ParagraphStyle(
            "IOPH1",
            parent=sample["Heading1"],
            fontName=bold_font,
            fontSize=18,
            leading=25,
            textColor=NAVY,
            spaceBefore=4 * mm,
            spaceAfter=4 * mm,
            wordWrap="CJK",
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "IOPH2",
            parent=sample["Heading2"],
            fontName=bold_font,
            fontSize=14,
            leading=20,
            textColor=BLUE,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
            wordWrap="CJK",
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "IOPH3",
            parent=sample["Heading3"],
            fontName=bold_font,
            fontSize=11,
            leading=17,
            textColor=NAVY,
            spaceBefore=3 * mm,
            spaceAfter=2 * mm,
            wordWrap="CJK",
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "IOPBody",
            parent=sample["BodyText"],
            fontName=regular_font,
            fontSize=9.5,
            leading=15,
            textColor=colors.HexColor("#28323C"),
            alignment=TA_LEFT,
            spaceAfter=2 * mm,
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "IOPSmall",
            parent=sample["BodyText"],
            fontName=regular_font,
            fontSize=8,
            leading=12,
            textColor=MID_GRAY,
            wordWrap="CJK",
        ),
        "bullet": ParagraphStyle(
            "IOPBullet",
            parent=sample["BodyText"],
            fontName=regular_font,
            fontSize=9,
            leading=14,
            leftIndent=5 * mm,
            firstLineIndent=-3 * mm,
            textColor=colors.HexColor("#28323C"),
            wordWrap="CJK",
        ),
    }


def _page_decorator(document_id: str, regular_font: str):
    def decorate(canvas, doc) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#D8E0E7"))
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
        canvas.setFont(regular_font, 7.5)
        canvas.setFillColor(MID_GRAY)
        canvas.drawString(18 * mm, 9 * mm, f"IOP Assessment Report - {document_id}")
        canvas.drawRightString(width - 18 * mm, 9 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    return decorate


def _bullet_items(values: list[Any], styles: dict[str, ParagraphStyle]) -> list[Paragraph]:
    if not values:
        return [_paragraph("-", styles["body"])]
    return [
        Paragraph(f"• {escape(_text(value))}", styles["bullet"])
        for value in values
    ]


def _criterion_story(
    criterion: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    criterion_id = _text(criterion.get("criterion_id"))
    title = f"{criterion_id}  {_text(criterion.get('criterion'))}"
    score = criterion.get("score")
    score_text = f"{score}/5" if isinstance(score, (int, float)) else "ยังไม่มีคะแนน"
    confidence = criterion.get("confidence")
    confidence_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else "-"
    status_table = Table(
        [
            [
                _paragraph("คะแนน", styles["small"]),
                _paragraph(score_text, styles["body"]),
                _paragraph("ความเชื่อมั่น", styles["small"]),
                _paragraph(confidence_text, styles["body"]),
                _paragraph("สถานะ", styles["small"]),
                _paragraph(criterion.get("status"), styles["body"]),
            ]
        ],
        colWidths=[18 * mm, 20 * mm, 24 * mm, 18 * mm, 15 * mm, 55 * mm],
    )
    status_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D8E0E7")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8E0E7")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TEXTCOLOR", (1, 0), (1, 0), _score_color(score)),
            ]
        )
    )
    story: list[Any] = [
        _paragraph(title, styles["h3"]),
        status_table,
        Spacer(1, 2 * mm),
        _paragraph(criterion.get("summary"), styles["body"]),
    ]
    strengths = criterion.get("strengths") or []
    improvements = criterion.get("areas_for_improvement") or []
    if strengths:
        story.append(_paragraph("จุดแข็ง", styles["h3"]))
        story.extend(_bullet_items(strengths, styles))
    if improvements:
        story.append(_paragraph("ประเด็นที่ควรปรับปรุง", styles["h3"]))
        story.extend(_bullet_items(improvements, styles))

    citations = criterion.get("citations") or []
    if citations:
        story.append(_paragraph("หลักฐานอ้างอิง", styles["h3"]))
        citation_rows = [[
            _paragraph("Chunk", styles["small"]),
            _paragraph("หน้า", styles["small"]),
            _paragraph("ข้อความหลักฐาน", styles["small"]),
        ]]
        for citation in citations:
            start = citation.get("page_start") or citation.get("page")
            end = citation.get("page_end") or start
            page = start if start == end else f"{start}-{end}"
            excerpt = citation.get("text") or citation.get("excerpt") or "-"
            if len(str(excerpt)) > 320:
                excerpt = str(excerpt)[:320].rstrip() + "…"
            citation_rows.append(
                [
                    _paragraph(citation.get("chunk_id"), styles["small"]),
                    _paragraph(page, styles["small"]),
                    _paragraph(excerpt, styles["small"]),
                ]
            )
        citation_table = Table(
            citation_rows,
            colWidths=[30 * mm, 18 * mm, 112 * mm],
            repeatRows=1,
        )
        citation_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BLUE),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C8D5DF")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8E0E7")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(citation_table)
    story.append(Spacer(1, 5 * mm))
    return story


def build_pdf_report(phase06: dict[str, Any], output_path: str | Path) -> Path:
    """Render a validated Phase 06 package as a readable Thai PDF report."""
    if not isinstance(phase06.get("dimension_summaries"), list):
        raise ValueError("Input is not a Phase 06 summary: dimension_summaries is required.")

    regular_font, bold_font = _register_fonts()
    styles = _styles(regular_font, bold_font)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document_id = _text(phase06.get("document_id"))
    doc = SimpleDocTemplate(
        str(target),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"IOP Assessment Report - {document_id}",
        author="AI-Document",
        subject="IOP assessment summary",
    )

    story: list[Any] = [
        Spacer(1, 20 * mm),
        _paragraph("รายงานผลการประเมิน IOP", styles["title"]),
        _paragraph(f"รหัสเอกสาร: {document_id}", styles["subtitle"]),
        _paragraph(
            f"แหล่งสรุป: {_text(phase06.get('summary_provider'))}"
            + (f" / {_text(phase06.get('summary_model'))}" if phase06.get("summary_model") else ""),
            styles["subtitle"],
        ),
        Spacer(1, 20 * mm),
        _paragraph("สรุปภาพรวม", styles["h1"]),
        _paragraph(phase06.get("overall_summary"), styles["body"]),
        Spacer(1, 5 * mm),
    ]

    dimensions = phase06.get("dimension_summaries", [])
    overview_rows = [[
        _paragraph("มิติ", styles["small"]),
        _paragraph("ชื่อมิติ", styles["small"]),
        _paragraph("จำนวนเกณฑ์", styles["small"]),
        _paragraph("คะแนนเฉลี่ย", styles["small"]),
    ]]
    for dimension in dimensions:
        average = _dimension_score(dimension)
        overview_rows.append(
            [
                _paragraph(dimension.get("dimension_id"), styles["body"]),
                _paragraph(dimension.get("dimension_name"), styles["body"]),
                _paragraph(len(dimension.get("criterion_summaries", [])), styles["body"]),
                _paragraph(f"{average:.2f}/5" if average is not None else "-", styles["body"]),
            ]
        )
    overview = Table(overview_rows, colWidths=[20 * mm, 92 * mm, 24 * mm, 28 * mm], repeatRows=1)
    overview.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFCBD5")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8E0E7")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([overview, PageBreak()])

    for dimension_index, dimension in enumerate(dimensions):
        if dimension_index:
            story.append(CondPageBreak(70 * mm))
        dimension_id = _text(dimension.get("dimension_id"))
        dimension_name = _text(dimension.get("dimension_name"))
        story.append(_paragraph(f"{dimension_id}  {dimension_name}", styles["h1"]))
        average = _dimension_score(dimension)
        if average is not None:
            score_banner = Table(
                [[_paragraph(f"คะแนนเฉลี่ยมิติ {average:.2f}/5", styles["h2"])]],
                colWidths=[164 * mm],
            )
            score_banner.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE),
                        ("BOX", (0, 0), (-1, -1), 0.75, _score_color(average)),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            story.extend([score_banner, Spacer(1, 3 * mm)])
        story.append(_paragraph(dimension.get("summary"), styles["body"]))

        strengths = dimension.get("strengths") or []
        improvements = dimension.get("areas_for_improvement") or []
        if strengths:
            story.append(_paragraph("จุดแข็งของมิติ", styles["h2"]))
            story.extend(_bullet_items(strengths, styles))
        if improvements:
            story.append(_paragraph("ประเด็นที่ควรปรับปรุงของมิติ", styles["h2"]))
            story.extend(_bullet_items(improvements, styles))

        story.append(CondPageBreak(55 * mm))
        story.append(_paragraph("รายละเอียดรายเกณฑ์", styles["h2"]))
        for criterion in dimension.get("criterion_summaries", []):
            criterion_story = _criterion_story(criterion, styles)
            story.append(KeepTogether(criterion_story[:4]))
            story.extend(criterion_story[4:])
    decorate = _page_decorator(document_id, regular_font)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)

    reader = PdfReader(str(target))
    if not reader.pages:
        raise RuntimeError("Generated PDF has no pages.")
    return target
