"""Application settings loaded from environment variables (and an optional repo-root ``.env``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://aca:aca_dev_password@localhost:5433/aca"
    # Comma-separated list of allowed browser origins.
    cors_origins: str = "http://localhost:5173"
    max_upload_mb: int = 10
    # Fail fast when PostgreSQL is unreachable instead of hanging requests / readiness.
    db_connect_timeout_s: int = 5

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("aca_dev_password")
    # False forces the in-memory knowledge-graph fallback (same data, no Neo4j connection).
    kg_use_neo4j: bool = True
    # JSON outputs of scripts/run_district_analytics.py (synthetic demo dataset).
    analytics_output_dir: Path = REPO_ROOT / "data" / "processed" / "analytics"
    # FAISS index built by scripts/build_vector_index.py (official scheme documents).
    rag_index_dir: Path = REPO_ROOT / "data" / "vector_index"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    gemini_api_key: SecretStr | None = None

    # None -> the rule engine's default directory (ai_modules/rule_engine/rules/).
    rules_dir: Path | None = None
    data_dir: Path = REPO_ROOT / "data"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    # ai_modules read their own settings (OCR, RAG, Neo4j timeouts) via os.getenv, so make the
    # repo-root .env visible to them too. Real environment variables still take precedence.
    load_dotenv(REPO_ROOT / ".env", override=False)
    return Settings()
