# FastAPI backend image. Build from the repository root:
#   docker build -f backend/Dockerfile -t aca-backend .
#   docker build -f backend/Dockerfile --build-arg INSTALL_AI=true -t aca-backend:full .
# INSTALL_AI=false (default) keeps the image small: OCR / RAG / Spark endpoints report 503 until the
# AI stack is installed. INSTALL_AI=true adds PaddleOCR, sentence-transformers, FAISS, PySpark (+ JRE).
FROM python:3.11-slim

ARG INSTALL_AI=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/backend

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
COPY ai_modules/requirements.txt ai_modules/requirements.txt

RUN pip install -r backend/requirements.txt \
 && if [ "$INSTALL_AI" = "true" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends openjdk-17-jre-headless libgl1 libglib2.0-0 libgomp1 fonts-noto-core \
      && rm -rf /var/lib/apt/lists/* \
      && pip install -r ai_modules/requirements.txt; \
    fi

COPY ai_modules ai_modules
COPY backend backend
COPY scripts scripts

RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "python -m alembic -c backend/alembic.ini upgrade head && python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000"]
