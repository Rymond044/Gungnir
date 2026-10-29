import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from gungnir_core import hosts
from gungnir_core.api import create_app
from gungnir_core.db import open_pool

# Server of `deploy/compose.yaml`; tests use their own database on it.
ADMIN_URL = os.environ.get("GUNGNIR_TEST_ADMIN_URL", "postgresql://gungnir:gungnir@localhost:5432/gungnir")
TEST_DB = "gungnir_test"


@pytest.fixture
def database_url():
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as err:
        pytest.skip(f"no PostgreSQL for tests: {err}")
    with admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    return psycopg.conninfo.make_conninfo(ADMIN_URL, dbname=TEST_DB)


@pytest.fixture
def pool(database_url):
    pool = open_pool(database_url)
    yield pool
    pool.close()


@pytest.fixture
def client(pool):
    with TestClient(create_app(pool)) as client:
        yield client


@pytest.fixture
def token(client, pool):
    with pool.connection() as conn:
        return hosts.add(conn, "laptop")


@pytest.fixture
def conn(database_url):
    """A migrated database without the app and its background workers."""
    from datetime import UTC, datetime

    from gungnir_core import maintenance
    from gungnir_core.db import migrate

    with psycopg.connect(database_url) as conn:
        migrate(conn)
        maintenance.ensure_partitions(conn, datetime.now(UTC))
        conn.commit()
        yield conn
