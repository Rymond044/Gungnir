"""Hosts and their agent tokens.

A token is 32 random bytes shown once when the host is added. The core keeps
only its SHA-256: a leaked database does not let anyone impersonate an agent,
and a high-entropy token needs no slow password hash.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

import psycopg


@dataclass(frozen=True)
class Host:
    id: int
    name: str


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def add(conn: psycopg.Connection, name: str) -> str:
    """Creates a host and returns its token."""
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO hosts (name, token_sha256) VALUES (%s, %s)", (name, token_hash(token)))
    return token


def rotate_token(conn: psycopg.Connection, name: str) -> str | None:
    token = secrets.token_urlsafe(32)
    cur = conn.execute("UPDATE hosts SET token_sha256 = %s WHERE name = %s", (token_hash(token), name))
    return token if cur.rowcount == 1 else None


def authenticate(conn: psycopg.Connection, token: str) -> Host | None:
    row = conn.execute("SELECT id, name FROM hosts WHERE token_sha256 = %s", (token_hash(token),)).fetchone()
    return Host(*row) if row else None


def by_name(conn: psycopg.Connection, name: str) -> Host | None:
    row = conn.execute("SELECT id, name FROM hosts WHERE name = %s", (name,)).fetchone()
    return Host(*row) if row else None


@dataclass(frozen=True)
class HostSummary:
    name: str
    created_at: datetime
    last_seen_at: datetime | None
    agent_version: str | None
    agent_started_at: datetime | None
    dropped_samples: int


def summaries(conn: psycopg.Connection) -> list[HostSummary]:
    rows = conn.execute(
        "SELECT name, created_at, last_seen_at, agent_version, agent_started_at, dropped_samples"
        " FROM hosts ORDER BY name"
    ).fetchall()
    return [HostSummary(*row) for row in rows]
