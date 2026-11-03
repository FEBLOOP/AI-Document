# Phase 07 - PDF and DOCX Reports

Phase 07 converts a validated Phase 06 JSON package into a Thai PDF or editable Word report. It does not call an LLM and does not change scores, strengths, improvement points, or citations. The output extension selects the format.

```powershell
python -m phase07.cli `
  --phase06 .\data\output\DOC_DD3C35531469_phase06_summary.json `
  --output .\output\pdf\DOC_DD3C35531469_iop_report.pdf `
  --scoring-output .\data\output\DOC_DD3C35531469_phase07_scoring.json
```

To create an editable Word document, change the output extension:

```powershell
python -m phase07.cli `
  --phase06 .\data\output\DOC_DD3C35531469_phase06_summary.json `
  --output .\data\output\DOC_DD3C35531469_iop_report.docx
```

The report includes an overall summary, criterion scores, D1-D8 averages, the criterion-weighted organization score, Maturity Level, dimension summaries, strengths, improvement points, and Phase 05 evidence citations. Phase 07 verifies the result against the project rubric (49 criteria by default). A partial run, such as D1 only, is marked `incomplete` and does not receive an organization score or Maturity Level.

The organization formula is `SUM(all valid criterion scores) / total rubric criteria`. It does not average the eight dimension averages because the dimensions contain different numbers of criteria.

Maturity classification uses a separately rounded classification score. Until the official rounding rule is confirmed, the defaults are one decimal with half-up rounding. These settings are configurable through `IOP_SCORE_CLASSIFICATION_DECIMALS`, `IOP_SCORE_DISPLAY_DECIMALS`, and `IOP_SCORE_ROUNDING` (`half_up` or `half_even`).

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
