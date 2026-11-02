# IOP Web UI

Start the local FastAPI application from this directory:

```powershell
& "C:\Users\UsEr\AppData\Local\Programs\Python\Python313\python.exe" -m uvicorn webapp.main:app --reload
```

Open `http://127.0.0.1:8000`. Upload a PDF, choose Qwen3-8B, Ollama, OpenAI API, Claude API, or evidence-only processing, and select PDF, DOCX, or both as the Phase 7 report format. For an LLM assessment, the web pipeline creates Phase 03 through Phase 07 and shows download buttons for the JSON packages and final reports.

Set the provider API key in the server environment before using a hosted API. For Ollama, set `IOP_LLM_BASE_URL` in the Container and enter the model tag returned by `/api/tags`. The first Qwen or embedding-model run downloads its model weights from Hugging Face.
