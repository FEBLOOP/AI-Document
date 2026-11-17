# IOP Web UI

Start the local FastAPI application from this directory:

```powershell
& "C:\Users\UsEr\AppData\Local\Programs\Python\Python313\python.exe" -m uvicorn webapp.main:app --reload
```

Open `http://127.0.0.1:8000`. Upload one PDF to keep using the original single-document workflow, or select/drop multiple PDFs to assess all of them together under one `ASSESSMENT_<id>`. You can add and remove files before submitting. Choose Qwen3-8B, Ollama, OpenAI API, Claude API, or evidence-only processing, and select PDF, DOCX, or both as the Phase 7 report format. For an LLM assessment, the web pipeline creates Phase 03 through Phase 07 and shows download buttons for the manifest, JSON packages, and final reports.

The multi-PDF endpoint is `POST /api/assessments` with repeated multipart field `files`. It validates the PDF content and configurable count/size/storage limits, stores each source under a unique Source ID, and runs retrieval once against the combined Phase 04 corpus. The existing `POST /api/jobs` single-file endpoint remains unchanged.

Multi-upload limits can be set with `IOP_MULTI_MAX_FILES`, `IOP_MULTI_MAX_FILE_BYTES`, `IOP_MULTI_MAX_TOTAL_BYTES`, and `IOP_MULTI_MIN_FREE_BYTES`; see `.env.example`. Work files are isolated under `data/work/<assessment_id>` and removed when a job exits. Original source PDFs remain protected by File Manager bulk output deletion.

The Web workflow requests strict Hybrid Retrieval by default. It creates Phase 04 embeddings with `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` when no Phase 04 provider was configured, then reports both requested and effective retrieval modes in the progress panel. Install `requirements-embeddings.txt` before using this mode. An explicitly configured `PHASE04_EMBEDDING_PROVIDER=none` is rejected for Hybrid mode instead of silently running lexical retrieval; set `IOP_WEB_RETRIEVAL_MODE=lexical` when lexical-only behavior is intentional.

Web Phase 05 defaults are Top-K 5, semantic weight 0.7, batch size 1, and 1,200 evidence characters per chunk. The subprocess always uses `--resume`; every successfully validated batch is written atomically. If Phase 05 exits after a later batch fails, the Web worker restarts it once and skips the validated criteria already stored in the checkpoint. These Web settings can be overridden with the `IOP_WEB_*` variables documented in `.env.example`; standalone CLI defaults are unchanged.

Set the provider API key in the server environment before using a hosted API. For Ollama, set `IOP_LLM_BASE_URL` in the Container and enter the model tag returned by `/api/tags`. The first Qwen or embedding-model run downloads its model weights from Hugging Face.
