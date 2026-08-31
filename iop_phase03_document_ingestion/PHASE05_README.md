# Phase 05 — D1–D8 Assessment

Phase 05 retrieves Phase 04 chunks for every IOP criterion, preserving page/block citations. It can produce an evidence package for review (`--provider none`, default) or ask OpenAI to score each criterion (`--provider openai`). The LLM is explicitly constrained to supplied evidence; it may not invent evidence.

## Evidence-only pass (recommended first)

```powershell
python -m phase05.cli --phase04 ".\data\output\DOC_DD3C35531469_phase04.json" --rubric ".\data\knowledge_base\iop_reference.json" --output ".\data\output\DOC_DD3C35531469_phase05_evidence.json"
```

## LLM assessment

Install `openai`, set `OPENAI_API_KEY`, then run:

```powershell
python -m phase05.cli --phase04 ".\data\output\DOC_DD3C35531469_phase04.json" --rubric ".\data\knowledge_base\iop_reference.json" --output ".\data\output\DOC_DD3C35531469_phase05_assessment.json" --provider openai --model gpt-4.1-mini
```

The result contains per-criterion candidates, score, confidence, cited chunk IDs, strengths and missing points. Dimension roll-ups and narrative summaries are intentionally left to Phase 06.
