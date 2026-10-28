"""Database connection pool and schema migrations.

Migrations are numbered SQL files applied in order, each in its own
transaction, and recorded in `schema_migrations`.
"""

import logging
import os
from importlib import resources

import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

DEFAULT_URL = "postgresql://gungnir:gungnir@localhost:5432/gungnir"

# Arbitrary constant; serialises concurrent migration runs.
MIGRATION_LOCK = 7_370_001


def database_url() -> str:
    return os.environ.get("GUNGNIR_DATABASE_URL", DEFAULT_URL)


def open_pool(url: str | None = None) -> ConnectionPool:
    # Checking each connection on checkout costs a round trip but survives a
    # database restart without failing the first requests after it.
    pool = ConnectionPool(
        url or database_url(), min_size=1, max_size=8, check=ConnectionPool.check_connection, open=False
    )
    pool.open(wait=True, timeout=30)
    return pool


def migrations() -> list[tuple[int, str, str]]:
    """Bundled migrations as (version, name, sql), in order."""
    found = []
    for entry in resources.files("gungnir_core.migrations").iterdir():
        if entry.name.endswith(".sql"):
            version = int(entry.name.split("_", 1)[0])
            found.append((version, entry.name, entry.read_text()))
    return sorted(found)


def migrate(conn: psycopg.Connection) -> list[str]:
    """Applies pending migrations; returns the names applied."""
    applied_now = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version integer PRIMARY KEY, name text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, sql in migrations():
            if version in done:
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))
            applied_now.append(name)
            log.info("applied migration %s", name)
    return applied_now
