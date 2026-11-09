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

The result contains the rubric snapshot plus per-criterion score, confidence, evidence sufficiency, matched points, cited chunk IDs, strengths, and missing points. The scoring guide is loaded from the supplied `--rubric` file; Phase 05 does not infer scores from the number of retrieved evidence chunks. Phase 06 writes narrative only, while numeric roll-ups and Maturity Level are calculated by Python in Phase 07.

The maximum number of selected citation IDs is dynamic. For each criterion it is `min(--top-k, distinct evidence chunks actually supplied)`. The model may cite fewer chunks and is instructed to select only directly relevant evidence. `--batch-size` controls criteria per request and does not affect this per-criterion citation limit. Phase 06/07 display selected evidence, while the complete retrieved evidence list remains in each immutable Phase 05 snapshot for audit.

## Hybrid retrieval

Phase 05 defaults to `--retrieval hybrid`. It combines the existing lexical/Thai-trigram score with cosine similarity over Phase 04 embeddings. The default semantic weight is `0.7`. Each evidence item records `lexical_score`, `semantic_score`, and the combined `relevance_score`.

```powershell
$env:PHASE04_EMBEDDING_PROVIDER = "sentence_transformers"
$env:PHASE04_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
python -m phase04.cli --input ".\data\output\DOC_xxx.json" --output ".\data\output\DOC_xxx_phase04.json" --embed
python -m phase05.cli --phase04 ".\data\output\DOC_xxx_phase04.json" --rubric "..\data\knowledge_base\iop_reference.json" --output ".\data\output\DOC_xxx_phase05.json" --retrieval hybrid --semantic-weight 0.7
```

If the Phase 04 file has no embeddings or the embedding model is unavailable, hybrid mode reports the reason and falls back to lexical retrieval. `--retrieval embedding` is strict and stops instead of falling back.

## Claude API assessment

Install the base requirements, set `ANTHROPIC_API_KEY`, and select the native
Anthropic provider. The API key stays in the server environment and is not
written to the assessment output.

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
python -m phase05.cli --phase04 ".\data\output\DOC_xxx_phase04.json" --rubric "..\data\knowledge_base\iop_reference.json" --output ".\data\output\DOC_xxx_phase05_claude.json" --provider anthropic --model claude-sonnet-5
```

## Assess selected dimensions

Use `--dimensions` with comma-separated dimension IDs to process only the required sections. This reduces retrieval, LLM calls, and processing time. Omitting the option keeps the existing D1-D8 behavior.

```powershell
python -m phase05.cli `
  --phase04 ".\data\output\DOC_DD3C35531469_phase04.json" `
  --rubric "..\data\knowledge_base\iop_reference.json" `
  --output ".\data\output\DOC_DD3C35531469_phase05_D1-D5.json" `
  --provider ollama `
  --model qwen3:8b `
  --dimensions D1,D5
```
