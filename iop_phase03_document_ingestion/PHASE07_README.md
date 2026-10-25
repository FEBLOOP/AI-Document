# Phase 07 - PDF Report

Phase 07 converts a validated Phase 06 JSON package into a Thai PDF report. It does not call an LLM and does not change scores, strengths, improvement points, or citations.

```powershell
python -m phase07.cli `
  --phase06 .\data\output\DOC_DD3C35531469_phase06_summary.json `
  --output .\output\pdf\DOC_DD3C35531469_iop_report.pdf
```

The report includes an overall summary, a D1-D8 score overview, dimension summaries, criterion scores, strengths, improvement points, and Phase 05 evidence citations.

## Thai fonts

Windows uses Tahoma automatically. On Linux, install Noto Sans Thai or set explicit TrueType font paths:

```bash
export IOP_PDF_FONT=/path/to/NotoSansThai-Regular.ttf
export IOP_PDF_FONT_BOLD=/path/to/NotoSansThai-Bold.ttf
```

## Dependencies

Install the base project requirements before running Phase 07:

```powershell
python -m pip install -r requirements.txt
```
