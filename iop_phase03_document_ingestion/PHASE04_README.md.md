# AI-Document — Phase 04: Document Understanding

Phase 04 turns a Phase 03 ingestion JSON into retrieval-ready chunks.  It cleans OCR text, detects headings, creates provenance-preserving semantic chunks, extracts lightweight metadata, and can add embeddings.  It deliberately does **not** score D1–D8; that belongs to Phase 05.

## Input compatibility

The reader accepts the common Phase 03 shapes:

- `{ "document_id": "…", "pages": [{"page": 1, "blocks": […]}] }`
- a top-level array of page objects
- a page with `text` but no `blocks` (a synthetic page block is created)

Blocks may carry `block_id`, `text`, `bbox`, and `confidence`.  Original OCR and source metadata are retained wherever supplied.

## Install and run

```powershell
python -m pip install -r requirements.txt
python -m phase04.cli --input path\to\phase03.json --output output\phase04.json --jsonl output\phase04.jsonl
```

Run the tests with `python -m pytest`.

### Configuration

All switches have CLI flags and environment-variable counterparts:

```powershell
$env:PHASE04_EMBEDDING_PROVIDER = "hash"       # none (default), hash, sentence_transformers, openai
$env:PHASE04_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
$env:PHASE04_CHUNK_MAX_CHARS = "1400"
$env:PHASE04_CHUNK_OVERLAP_BLOCKS = "1"
python -m phase04.cli --input phase03.json --output phase04.json --embed
```

`hash` is deterministic, dependency-free, and useful for end-to-end testing; it is not a semantic model. For local semantic embeddings install `pip install -r requirements-embeddings.txt` and use `sentence_transformers`. For OpenAI embeddings install `openai`, set `OPENAI_API_KEY`, and set `PHASE04_EMBEDDING_PROVIDER=openai`. Embeddings are omitted unless `--embed` is supplied.

Outputs use schema `ai-document.phase04/v1`. Each chunk has `chunk_id`, `document_id`, normalized text, page range, source blocks (including page/bbox/confidence), section, metadata, and optional embedding. This is the intended evidence unit for Phase 05 retrieval: retrieve chunks by embedding/metadata, then cite `source_blocks`.

`llm_ready` contains the normalized content and compact evidence citation (`evidence_id`, pages, block IDs). It lets Phase 05 send retrieved evidence to an LLM without losing its trace back to the scanned document.
