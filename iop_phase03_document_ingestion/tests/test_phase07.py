from pypdf import PdfReader
from docx import Document

from phase07.report import build_pdf_report, build_report


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
