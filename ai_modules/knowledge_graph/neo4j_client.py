"""Thin wrapper around the official ``neo4j`` driver.

Configuration (environment variables):
    NEO4J_URI       default ``bolt://localhost:7687``
    NEO4J_USER      default ``neo4j``
    NEO4J_PASSWORD  default ``aca_dev_password`` (the public docker-compose dev default; override it)
    NEO4J_DATABASE  default ``neo4j`` (the only database in Community edition)
    NEO4J_CONNECT_TIMEOUT  seconds, default ``3``
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from neo4j import Driver, Session

logger = logging.getLogger(__name__)

CYPHER_DIR = Path(__file__).resolve().parent / "cypher"


@dataclass(frozen=True, slots=True)
class Neo4jSettings:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "aca_dev_password"  # noqa: S105 - public docker-compose dev default, override via env
    database: str = "neo4j"
    connect_timeout: float = 3.0

    @classmethod
    def from_env(cls) -> Neo4jSettings:
        default = cls()
        return cls(
            uri=os.getenv("NEO4J_URI", default.uri),
            user=os.getenv("NEO4J_USER", default.user),
            password=os.getenv("NEO4J_PASSWORD", default.password),
            database=os.getenv("NEO4J_DATABASE", default.database),
            connect_timeout=float(os.getenv("NEO4J_CONNECT_TIMEOUT", str(default.connect_timeout))),
        )


class Neo4jClient:
    """Owns one driver; use as a context manager or call ``close()``."""

    def __init__(self, settings: Neo4jSettings | None = None) -> None:
        from neo4j import GraphDatabase

        self.settings = settings or Neo4jSettings.from_env()
        self._driver: Driver = GraphDatabase.driver(
            self.settings.uri,
            auth=(self.settings.user, self.settings.password),
            connection_timeout=self.settings.connect_timeout,
            connection_acquisition_timeout=self.settings.connect_timeout * 2,
            max_transaction_retry_time=self.settings.connect_timeout * 2,
        )

    def verify(self) -> None:
        """Raise a ``neo4j.exceptions`` error if the server is unreachable or auth fails."""
        self._driver.verify_connectivity()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._driver.session(database=self.settings.database) as session:
            yield session

    def run(self, query: str, parameters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run one parametrised statement in an auto-commit transaction and return the records."""
        with self.session() as session:
            result = session.run(query, dict(parameters or {}))
            return [record.data() for record in result]

    def run_in_transaction(self, statements: list[tuple[str, Mapping[str, Any]]]) -> None:
        """Run several write statements atomically (all or nothing)."""

        def work(tx: Any) -> None:
            for query, params in statements:
                tx.run(query, dict(params)).consume()

        with self.session() as session:
            session.execute_write(work)

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> Neo4jClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def available(settings: Neo4jSettings | None = None, timeout: float = 2.0) -> bool:
    """True when Neo4j accepts a connection with the configured credentials within ``timeout``."""
    return unavailable_reason(settings, timeout) is None


def unavailable_reason(settings: Neo4jSettings | None = None, timeout: float = 2.0) -> str | None:
    """None when reachable, otherwise a short human-readable reason (for logs / test skips)."""
    try:
        import neo4j  # noqa: F401
    except ImportError:
        return "neo4j Python driver is not installed"
    base = settings or Neo4jSettings.from_env()
    probe = Neo4jSettings(base.uri, base.user, base.password, base.database, timeout)
    try:
        with Neo4jClient(probe) as client:
            client.verify()
    except Exception as exc:  # driver raises several unrelated exception types
        return f"{type(exc).__name__}: {exc}"
    return None


_STATEMENT_NAME = re.compile(r"^//\s*@name\s+(\w+)\s*$")


def load_cypher_script(filename: str) -> list[str]:
    """Split a ``.cypher`` file into statements (comments dropped, one statement per ';')."""
    text = (CYPHER_DIR / filename).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("//")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def load_named_statements(filename: str) -> dict[str, str]:
    """Parse ``// @name <id>`` blocks of a ``.cypher`` file into ``{id: statement}``."""
    statements: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in (CYPHER_DIR / filename).read_text(encoding="utf-8").splitlines():
        match = _STATEMENT_NAME.match(line.strip())
        if match:
            current, buffer = match.group(1), []
            continue
        if current is None or line.lstrip().startswith("//"):
            continue
        buffer.append(line)
        if line.rstrip().endswith(";"):
            statements[current] = "\n".join(buffer).rstrip().rstrip(";").strip()
            current, buffer = None, []
    if current is not None:
        raise ValueError(f"{filename}: statement '{current}' is not terminated with ';'")
    return statements
