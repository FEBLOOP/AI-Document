FROM python:3.12-slim-bookworm

# Switch Debian APT mirrors from HTTP to HTTPS to bypass HTTP proxy/CDN corruption
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get -o Acquire::https::Verify-Peer=false -o Acquire::Retries=3 update && \
    apt-get -o Acquire::https::Verify-Peer=false -o Acquire::Retries=3 install -y --no-install-recommends \
    ca-certificates \
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