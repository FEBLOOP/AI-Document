# Phase 06 — Summary Generation

Phase 06 converts a **completed or review-ready Phase 05 JSON** into an assessment-report summary. It uses the repository's `good_examples_kb.json` as a real style reference: its overall report structure, per-dimension structure, tone, and evidence-first pattern are loaded into the output and supplied to the LLM.

It is not an assessment engine. Phase 06 copies each Phase 05 criterion's score, confidence, assessment fields, and evidence into an immutable `phase05_snapshot`. Validation fails if a summary changes those source values or cites a chunk that was not Phase 05 evidence for that criterion.

## Template/evidence-only run

This is the recommended first run. It does not call an API, is deterministic, and produces a report-ready package while preserving candidate evidence and citations.

```powershell
python -m phase06.cli --phase05 ".\data\output\DOC_DD3C35531469_phase05_evidence.json" --good-examples "..\data\knowledge_base\good_examples_kb.json" --output ".\data\output\DOC_DD3C35531469_phase06_summary.json" --provider none
```

Because `DOC_DD3C35531469_phase05_evidence.json` is an evidence-only package, it has no scores yet. Phase 06 will state that it is awaiting Phase 05 assessment; it will not infer scores, strengths, missing points, or facts from the raw evidence.

## LLM narrative run

Use a Phase 05 output produced with `--provider openai`, install `openai`, and set `OPENAI_API_KEY`.

```powershell
python -m phase06.cli --phase05 ".\data\output\DOC_DD3C35531469_phase05_assessment.json" --good-examples "..\data\knowledge_base\good_examples_kb.json" --output ".\data\output\DOC_DD3C35531469_phase06_summary.json" --provider openai --model gpt-4.1-mini
```

The model receives only Phase 05 and the Good Examples style patterns. Its contract is to write prose and choose only existing evidence chunk IDs. It must not rescore, add evidence, add missing points, add strengths, add numbers, or state new facts. The program retains the original citation objects, including page and source-block provenance.

## Output

The JSON contains `assessment_id`, `document_id`, `overall_summary`, all `dimension_summaries` (D1–D8 supplied by Phase 05), criterion summaries, strengths, areas for improvement, citations, Good Examples references, and immutable Phase 05 snapshots. Run validation in code with:

```python
from phase06.validation import validate_summary_integrity
validate_summary_integrity(summary)
```
