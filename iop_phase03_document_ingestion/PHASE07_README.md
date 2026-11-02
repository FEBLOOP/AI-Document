# Phase 07 - PDF and DOCX Reports

Phase 07 converts a validated Phase 06 JSON package into a Thai PDF or editable Word report. It does not call an LLM and does not change scores, strengths, improvement points, or citations. The output extension selects the format.

```powershell
python -m phase07.cli `
  --phase06 .\data\output\DOC_DD3C35531469_phase06_summary.json `
  --output .\output\pdf\DOC_DD3C35531469_iop_report.pdf
```

To create an editable Word document, change the output extension:

```powershell
python -m phase07.cli `
  --phase06 .\data\output\DOC_DD3C35531469_phase06_summary.json `
  --output .\data\output\DOC_DD3C35531469_iop_report.docx
```

The report includes an overall summary, a D1-D8 score overview, dimension summaries, criterion scores, strengths, improvement points, and Phase 05 evidence citations.

## Thai fonts

PDF output uses Tahoma automatically on Windows. On Linux, install Noto Sans Thai or set explicit TrueType font paths:

```bash
export IOP_PDF_FONT=/path/to/NotoSansThai-Regular.ttf
export IOP_PDF_FONT_BOLD=/path/to/NotoSansThai-Bold.ttf
```

DOCX output uses `TH Sarabun New` as its preferred Word font. Microsoft Word will use an available fallback font when it is not installed.

## Dependencies

Install the base project requirements before running Phase 07:

```powershell
python -m pip install -r requirements.txt
```
