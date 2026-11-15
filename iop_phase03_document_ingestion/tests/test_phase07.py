from pypdf import PdfReader
from docx import Document
import pytest

from phase07.report import build_pdf_report, build_report


def _phase06_with_strength_mapping():
    long_strength = "มีระบบติดตามผลนวัตกรรมอย่างต่อเนื่องและครอบคลุมหน่วยงานที่เกี่ยวข้อง"
    return {
        "document_id": "DOC_MAPPING",
        "summary_provider": "none",
        "summary_model": None,
        "overall_summary": "สรุปผล",
        "dimension_summaries": [
            {
                "dimension_id": "D1",
                "dimension_name": "การนำองค์กร",
                "summary": "สรุปมิติ",
                "strengths": ["มีนโยบาย", long_strength],
                "areas_for_improvement": [],
                "criterion_summaries": [
                    {
                        "criterion_id": "1.1.1",
                        "criterion": "กำหนดทิศทางองค์กร",
                        "status": "assessed",
                        "score": 3,
                        "confidence": 0.8,
                        "summary": "พบหลักฐาน",
                        "strengths": ["มีนโยบาย", long_strength, "ยังไม่มีหลักฐานเชื่อมโยง"],
                        "strength_evidence_mapping": [
                            {"strength": "มีนโยบาย", "evidence_chunk_ids": ["C1"]},
                            {"strength": long_strength, "evidence_chunk_ids": ["C1"]},
                            {"strength": "ยังไม่มีหลักฐานเชื่อมโยง", "evidence_chunk_ids": []},
                        ],
                        "areas_for_improvement": [],
                        "citations": [
                            {"chunk_id": "C1", "page_start": 2, "page_end": 2, "text": "หลักฐานที่เชื่อมโยง"},
                            {"chunk_id": "C2", "page_start": 3, "page_end": 4, "text": "หลักฐานที่ไม่มี mapping"},
                        ],
                    }
                ],
            }
        ],
    }


def test_build_pdf_report_creates_readable_pdf(tmp_path):
    phase06 = {
        "document_id": "DOC_TEST",
        "summary_provider": "none",
        "summary_model": None,
        "overall_summary": "สรุปผลการประเมินภาพรวม",
        "dimension_summaries": [
            {
                "dimension_id": "D1",
                "dimension_name": "การนำองค์กร",
                "summary": "มีหลักฐานสนับสนุนการดำเนินงาน",
                "strengths": ["มีนโยบายชัดเจน"],
                "areas_for_improvement": ["ควรเพิ่มตัวชี้วัด"],
                "criterion_summaries": [
                    {
                        "criterion_id": "1.1.1",
                        "criterion": "กำหนดทิศทางองค์กร",
                        "status": "assessed",
                        "score": 3,
                        "confidence": 0.8,
                        "summary": "พบหลักฐานบางส่วน",
                        "strengths": ["มีเอกสารนโยบาย"],
                        "areas_for_improvement": ["ยังไม่มี KPI"],
                        "citations": [
                            {
                                "chunk_id": "C1",
                                "page_start": 2,
                                "page_end": 2,
                                "text": "ข้อความหลักฐาน",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    output = tmp_path / "report.pdf"

    build_pdf_report(phase06, output)

    reader = PdfReader(str(output))
    assert output.read_bytes().startswith(b"%PDF")
    assert len(reader.pages) >= 2
    assert reader.metadata.title == "IOP Assessment Report - DOC_TEST"


def test_build_report_creates_editable_docx(tmp_path):
    phase06 = {
        "document_id": "DOC_TEST",
        "summary_provider": "none",
        "summary_model": None,
        "overall_summary": "สรุปผลการประเมินภาพรวม",
        "dimension_summaries": [
            {
                "dimension_id": "D1",
                "dimension_name": "การนำองค์กร",
                "summary": "มีหลักฐานสนับสนุนการดำเนินงาน",
                "strengths": ["มีนโยบายชัดเจน"],
                "areas_for_improvement": ["ควรเพิ่มตัวชี้วัด"],
                "criterion_summaries": [],
            }
        ],
    }
    output = tmp_path / "report.docx"

    build_report(phase06, output)

    assert output.read_bytes().startswith(b"PK")
    document = Document(output)
    rendered_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "รายงานผลการประเมิน IOP" in rendered_text
    assert "สรุปผลการประเมินภาพรวม" in rendered_text


def test_build_report_rejects_unknown_extension(tmp_path):
    try:
        build_report({"dimension_summaries": []}, tmp_path / "report.txt")
    except ValueError as exc:
        assert ".pdf or .docx" in str(exc)
    else:
        raise AssertionError("Expected an unsupported extension error")


def test_pdf_and_docx_append_related_strengths_column_and_keep_strengths_section(tmp_path):
    phase06 = _phase06_with_strength_mapping()
    pdf_output = tmp_path / "mapping.pdf"
    docx_output = tmp_path / "mapping.docx"

    build_report(phase06, pdf_output)
    build_report(phase06, docx_output)

    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_output).pages)
    assert "จุดแข็ง" in pdf_text
    assert "จุดแข็งที่เกี่ยวข้อง" in pdf_text
    assert "มีนโยบาย" in pdf_text
    assert "—" in pdf_text

    document = Document(docx_output)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "จุดแข็ง" in paragraph_text
    assert "มีนโยบาย" in paragraph_text
    evidence_table = next(
        table
        for table in document.tables
        if table.rows[0].cells[-1].text == "จุดแข็งที่เกี่ยวข้อง"
    )
    assert len(evidence_table.columns) == 4
    assert evidence_table.autofit is False
    widths_cm = [cell.width.cm for cell in evidence_table.rows[0].cells]
    assert widths_cm == pytest.approx([3.0, 1.4, 8.0, 5.0], abs=0.05)
    assert "มีนโยบาย" in evidence_table.rows[1].cells[3].text
    assert "มีระบบติดตามผลนวัตกรรม" in evidence_table.rows[1].cells[3].text
    assert evidence_table.rows[2].cells[3].text == "—"


def test_historical_phase06_without_mapping_renders_dash_in_new_column(tmp_path):
    phase06 = _phase06_with_strength_mapping()
    criterion = phase06["dimension_summaries"][0]["criterion_summaries"][0]
    criterion.pop("strength_evidence_mapping")
    output = tmp_path / "legacy.docx"

    build_report(phase06, output)

    document = Document(output)
    evidence_table = next(
        table
        for table in document.tables
        if table.rows[0].cells[-1].text == "จุดแข็งที่เกี่ยวข้อง"
    )
    assert [row.cells[3].text for row in evidence_table.rows[1:]] == ["—", "—"]


def test_pdf_and_docx_display_the_same_overall_score_and_maturity(tmp_path):
    phase06 = {
        "document_id": "DOC_SCORE",
        "summary_provider": "none",
        "summary_model": None,
        "overall_summary": "สรุป",
        "dimension_summaries": [
            {
                "dimension_id": "D1",
                "dimension_name": "ยุทธศาสตร์นวัตกรรม",
                "summary": "สรุปมิติ",
                "strengths": [],
                "areas_for_improvement": [],
                "criterion_summaries": [
                    {
                        "criterion_id": "1.1.1",
                        "criterion": "เกณฑ์",
                        "status": "assessed",
                        "score": 3,
                        "confidence": 0.8,
                        "summary": "เหตุผล",
                        "strengths": [],
                        "areas_for_improvement": [],
                        "citations": [],
                    }
                ],
            }
        ],
    }
    rubric = {"criteria": [{"criterion_id": "1.1.1", "dimension_id": "D1"}]}
    pdf_output = tmp_path / "score.pdf"
    docx_output = tmp_path / "score.docx"

    build_report(phase06, pdf_output, rubric)
    build_report(phase06, docx_output, rubric)

    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_output).pages)
    docx = Document(docx_output)
    docx_text = "\n".join(
        [paragraph.text for paragraph in docx.paragraphs]
        + [cell.text for table in docx.tables for row in table.rows for cell in row.cells]
    )
    for expected in ("3.00/5", "Defined"):
        assert expected in pdf_text
        assert expected in docx_text
