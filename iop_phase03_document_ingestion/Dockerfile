FROM python:3.12-slim

# Install Tesseract OCR + Thai/English language data for pytesseract
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-tha \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only PyTorch (~200 MB) to skip 4 GB NVIDIA CUDA bloat
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install active dependencies only (skipping requirements-huggingface.txt)
COPY requirements.txt requirements-embeddings.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-embeddings.txt

# Pre-download the exact default embedding model into the image cache
ENV HF_HOME=/opt/hf_cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# Enforce strict offline mode at runtime
ENV HF_HUB_OFFLINE=1

CMD ["uvicorn", "webapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
