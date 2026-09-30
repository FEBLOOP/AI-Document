# IOP Web UI

Start the local FastAPI application from this directory:

```powershell
& "C:\Users\UsEr\AppData\Local\Programs\Python\Python313\python.exe" -m uvicorn webapp.main:app --reload
```

Open `http://127.0.0.1:8000`. Upload a PDF, then choose Qwen3-8B, Ollama, OpenAI API, or evidence-only processing. Set `OPENAI_API_KEY` (or `IOP_LLM_API_KEY`) in the server environment before choosing OpenAI API. For Ollama, set `IOP_LLM_BASE_URL` in the Container and enter the model tag returned by `/api/tags`. The first Qwen run downloads its model weights from Hugging Face.
