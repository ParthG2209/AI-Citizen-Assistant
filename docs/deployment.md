# Local Development & Deployment

Target: a student laptop (Intel i3 / Ryzen 3, **8 GB RAM**, GPU optional, Windows / Linux / macOS). Every component is free and open source; Gemini is an optional free-tier fallback.

All commands run from the **repository root**. Windows users: run them in Git Bash or PowerShell (replace `.venv/Scripts/python.exe` with `.venv/bin/python` on Linux/macOS).

## 0. Prerequisites

| Tool | Needed for | Notes |
|---|---|---|
| Python 3.11 | backend + AI modules | `uv` recommended (`pip install uv`) |
| Docker Desktop / Engine | PostgreSQL, Neo4j (and optionally the backend) | 8 GB machines: give Docker ≤ 3 GB |
| Ollama | RAG explanations | https://ollama.com — optional; without it explanations are extractive (quotes only) |
| Java 17 | PySpark engine only | optional; the Pandas engine needs no Java |
| Node 20 | Parth's frontend | see `frontend/` |

## 1. Python environment

```bash
uv venv --python 3.11 .venv
# Core API only (small, ~150 MB):
uv pip install --python .venv/Scripts/python.exe -r backend/requirements.txt -r backend/requirements-dev.txt
# Full AI stack (PaddleOCR, sentence-transformers, FAISS, PySpark; ~2-3 GB download):
uv pip install --python .venv/Scripts/python.exe -r ai_modules/requirements.txt
cp .env.example .env    # then edit if needed
```

The API starts with only the core requirements. OCR and RAG endpoints answer `503` (`ocr_unavailable` / `rag_unavailable`) with a clear error until the AI stack is installed; that includes a core-only install missing Pillow or PyMuPDF.

**Safety and connection settings** (in `.env`; the backend loads `.env` itself, and real environment variables win):

| Variable | Default | Effect |
|---|---|---|
| `APP_ENV` | `development` | Must be one of `development`, `dev`, `test`, `testing`, `demo`, `local`, unless `ALLOW_UNAUTHENTICATED=true`; otherwise the app refuses to start (`UnsafeConfigurationError`) |
| `ALLOW_UNAUTHENTICATED` | `false` | Explicitly acknowledges serving the API **without authentication** outside the environments above. Only set it behind your own access control |
| `ALLOW_NON_SYNTHETIC` | `false` | When false, `POST /citizens` with `is_synthetic: false` gets 403 `non_synthetic_not_allowed`. Keep it false for this prototype |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated explicit origins. `*` is always refused at startup |
| `DB_CONNECT_TIMEOUT_S` | `5` | PostgreSQL connect timeout, so requests and `/ready` fail fast when the database is down |

## 2. PostgreSQL

```bash
docker compose up -d postgres            # host port 5433 (avoids a native Postgres on 5432)
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head   # head: 9b1f3c2d7e4a (evaluation freshness)
.venv/Scripts/python.exe scripts/seed_database.py      # schemes + rule versions + synthetic demo citizens (idempotent; re-run after rule changes)
```

Evaluation runs stored before migration `9b1f3c2d7e4a` have no `profile_fingerprint`/`rule_set_hash` and are treated as stale. Recommendations and explanations re-evaluate them automatically.

## 3. Neo4j (optional — the API falls back to an in-memory graph)

```bash
docker compose up -d neo4j               # browser: http://localhost:7474 (neo4j / aca_dev_password)
.venv/Scripts/python.exe scripts/load_knowledge_graph.py
```

Neo4j is capped at 1 GB heap + 256 MB page cache in `docker-compose.yml`. On a tight 8 GB machine skip it (`KG_USE_NEO4J=false`): recommendations are identical (tests assert parity).

## 4. OCR models (PaddleOCR)

```bash
.venv/Scripts/python.exe -m ai_modules.ocr.paddle_engine --download
.venv/Scripts/python.exe scripts/generate_synthetic_documents.py   # synthetic sample certificates
```

Models are cached in the user profile (see `ai_modules/ocr/paddle_engine.py`). CPU inference is the default.

## 5. RAG index (official documents → FAISS)

```bash
.venv/Scripts/python.exe scripts/build_vector_index.py            # downloads the official PDFs listed in data/raw/circulars/manifest.yaml
.venv/Scripts/python.exe scripts/build_vector_index.py --offline  # rebuild from already-downloaded files
```

Downloaded documents and the index are git-ignored. The embedding model (multilingual MiniLM, ~470 MB) is downloaded on first use.

## 6. Ollama

```bash
ollama pull qwen2.5:3b-instruct     # 8 GB RAM machines (~2 GB)
ollama pull qwen2.5:7b-instruct     # 16 GB RAM or a GPU (~4.7 GB)
```

Set `OLLAMA_MODEL` in `.env`. Optional fallback: set `GEMINI_API_KEY`. With neither, `/explain` still returns cited passages with `provider: "none"`.

## 7. District analytics (synthetic data)

```bash
.venv/Scripts/python.exe scripts/run_district_analytics.py --engine pandas   # ~3 s, no Java
.venv/Scripts/python.exe scripts/run_district_analytics.py --engine spark    # needs JAVA_HOME (Java 17)
```

Outputs go to `data/processed/analytics/` (JSON, Parquet, CSV, `report.html`). All figures are **synthetic** and labelled `is_synthetic: true`.

Windows + Spark: paths containing `(` `)` break Spark's launcher scripts; `ai_modules/analytics/spark_session.py` works around this automatically via a link under `%LOCALAPPDATA%`.

## 8. Run the API

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8000 --reload
```

- Swagger UI: http://localhost:8000/docs · OpenAPI JSON: http://localhost:8000/openapi.json
- Readiness: http://localhost:8000/api/v1/ready
- Windows note: if port 8000 is refused (`WinError 10013`), Hyper-V has reserved it; use `--port 8500`.

Docker alternative (core API only; add `--build-arg INSTALL_AI=true` for OCR/RAG/Spark):

```bash
BACKEND_PORT=8000 docker compose --profile app up -d --build
```

## 9. Tests

```bash
.venv/Scripts/python.exe -m pytest -m "not slow"     # fast suite (needs PostgreSQL; Neo4j/Ollama tests skip if down)
.venv/Scripts/python.exe -m pytest                   # everything incl. real PaddleOCR / embeddings / Spark
.venv/Scripts/python.exe -m ruff check backend ai_modules scripts
```

Markers: `integration` (needs PostgreSQL / Neo4j / Ollama, skipped when unreachable) and `slow` (loads heavy models).

**`REQUIRE_SERVICES=1`** (also `true`/`yes`): tests that need PostgreSQL or Neo4j **fail** instead of skipping when the service is unreachable. CI sets it in the backend job (`.github/workflows/ci.yml`) so a broken service container can't produce a green run of skipped tests. Locally, leave it unset to skip what isn't running. Example:

```bash
REQUIRE_SERVICES=1 .venv/Scripts/python.exe -m pytest -m "not slow" backend/tests ai_modules/knowledge_graph
```

## 10. Connect the frontend

See [`frontend_integration_guide.md`](frontend_integration_guide.md). In short: `VITE_API_BASE_URL=http://localhost:8000/api/v1` and keep `CORS_ORIGINS` in `.env` matching the Vite origin.

## Memory budget on 8 GB

| Component | Approx. RAM | Run when |
|---|---|---|
| FastAPI + rule engine | 150 MB | always |
| PostgreSQL | ≤ 512 MB (capped) | always |
| Neo4j | ≤ 1.5 GB (capped) | optional |
| PaddleOCR (CPU) | ~1 GB while processing | on upload |
| Embeddings + FAISS | ~600 MB | explanations |
| Ollama 3B Q4 | ~2.5 GB | explanations |
| Spark local[2] | ~1.5 GB | batch analytics only (or use the Pandas engine) |

Run heavy pieces separately: build the FAISS index and analytics outputs once, stop Neo4j if not needed, and use `qwen2.5:3b-instruct`.
