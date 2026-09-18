# Phase 05 — D1–D8 Assessment

Phase 05 retrieves Phase 04 chunks for every IOP criterion, preserving page/block citations. It can produce an evidence package for review (`--provider none`, default) or ask the configured Qwen LLM to score each criterion. The LLM is explicitly constrained to supplied evidence; it may not invent evidence.

## Evidence-only pass (recommended first)

```powershell
python -m phase05.cli --phase04 ".\data\output\DOC_DD3C35531469_phase04.json" --rubric ".\data\knowledge_base\iop_reference.json" --output ".\data\output\DOC_DD3C35531469_phase05_evidence.json"
```

## LLM assessment

Install direct Hugging Face support with `pip install -r requirements-huggingface.txt`, configure the shared LLM environment (see `.env.example`), then run. On Windows with NVIDIA CUDA, replace CPU PyTorch with `pip install --upgrade --force-reinstall torch --index-url https://download.pytorch.org/whl/cu128`. `IOP_LLM_MODEL` defaults to `Qwen/Qwen3-8B`. On a CUDA GPU, the provider loads 4-bit weights by default (`IOP_LLM_LOAD_IN_4BIT=true`) so Qwen3-8B fits on an 8 GB RTX 4060-class card.

```powershell
$env:IOP_LLM_MODEL = "Qwen/Qwen3-8B"
python -m phase05.cli --phase04 ".\data\output\DOC_DD3C35531469_phase04.json" --rubric ".\data\knowledge_base\iop_reference.json" --output ".\data\output\DOC_DD3C35531469_phase05_assessment.json" --provider huggingface
```

The result contains per-criterion candidates, score, confidence, cited chunk IDs, strengths and missing points. Dimension roll-ups and narrative summaries are intentionally left to Phase 06.
