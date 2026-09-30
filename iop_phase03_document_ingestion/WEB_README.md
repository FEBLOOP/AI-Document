# IOP Web UI

Start the local FastAPI application from this directory:

```powershell
& "C:\Users\UsEr\AppData\Local\Programs\Python\Python313\python.exe" -m uvicorn webapp.main:app --reload
```

Open `http://127.0.0.1:8000`. Upload a PDF, then choose Qwen3-8B, OpenAI API, or evidence-only processing. Set `OPENAI_API_KEY` (or `IOP_LLM_API_KEY`) in the server environment before choosing OpenAI API. The first Qwen run downloads its model weights from Hugging Face.
