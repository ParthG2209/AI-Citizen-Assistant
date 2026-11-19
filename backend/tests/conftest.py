"""Test fixtures backed by a real, throw-away PostgreSQL database.

A dedicated database (``aca_test`` by default, override with TEST_DATABASE_URL) is created, migrated
with Alembic (upgrade -> downgrade -> upgrade, which doubles as migration verification) and dropped
at the end of the session. Tests needing it skip cleanly when PostgreSQL is unreachable, unless
``REQUIRE_SERVICES=1`` (CI): then they fail instead (Neo4j-dependent tests use ``skip_or_fail`` too).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from app.config import Settings, get_settings
from app.database import get_engine, get_sessionmaker
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"

# Same server as the app (DATABASE_URL from env / .env / default), separate database.
_dev_url = make_url(Settings().database_url)
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", _dev_url.set(database="aca_test").render_as_string(hide_password=False)
)
# Point the application at the test database; nothing below may use a previously cached engine.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
get_settings.cache_clear()
get_engine.cache_clear()
get_sessionmaker.cache_clear()

REQUIRE_SERVICES = os.environ.get("REQUIRE_SERVICES", "").strip().lower() in {"1", "true", "yes"}


def skip_or_fail(reason: str) -> None:
    """Skip a test whose external service is unavailable; fail instead when REQUIRE_SERVICES=1."""
    if REQUIRE_SERVICES:
        pytest.fail(f"REQUIRE_SERVICES=1 but {reason}", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(name="skip_or_fail")
def skip_or_fail_fixture():
    """``skip_or_fail`` for test modules (importing conftest directly would load it twice)."""
    return skip_or_fail


def _admin_conninfo() -> tuple[str, str]:
    url = make_url(TEST_DATABASE_URL)
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    return admin, url.database or "aca_test"


def alembic_config():
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = TEST_DATABASE_URL
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def database() -> Iterator[str]:
    from alembic import command

    admin_url, name = _admin_conninfo()
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        skip_or_fail(
            f"PostgreSQL not reachable for tests ({type(exc).__name__}); run `docker compose up -d postgres`"
        )
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')

    config = alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    yield TEST_DATABASE_URL

    get_engine().dispose()
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def app(database: str):
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture(scope="session")
def client(app) -> Iterator:
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(database: str) -> Iterator:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def citizen_id(client) -> str:
    response = client.post("/api/v1/citizens", json={"label": "pytest synthetic citizen"})
    assert response.status_code == 201, response.text
    return response.json()["id"]
