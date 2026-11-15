"""Accounts and sessions of the interface (ADR 0064).

At its first start the server creates `admin` with a random initial
password. Only its argon2 hash checks a login; the plain one stays in the
database until it is changed, to be shown on the server's machine by
`gungnir-server admin initial-password`, never over HTTP. While it is set the
account may do nothing but change it.
"""

import hashlib
import logging
import secrets
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

log = logging.getLogger(__name__)

ADMIN = "admin"
COOKIE = "gungnir_session"
IDLE = timedelta(hours=12)
MIN_PASSWORD = 10
# Failed logins of one name in this long lock it for the rest of it.
FAILURES = 5
LOCK = timedelta(minutes=15)

_hasher = PasswordHasher()


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    must_change: bool


class Refused(Exception):
    pass


def ensure_admin(conn: psycopg.Connection) -> None:
    """Creates the first account if there is none and logs its password, the
    only time it is shown outside the CLI."""
    with conn.transaction():
        conn.execute("LOCK TABLE accounts IN EXCLUSIVE MODE")
        if conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
            return
        password = _new_password()
        conn.execute(
            "INSERT INTO accounts (name, password_hash, initial_password) VALUES (%s, %s, %s)",
            (ADMIN, _hasher.hash(password), password),
        )
    log.warning(
        "account '%s' created; its initial password is %s (again: gungnir-server admin initial-password)",
        ADMIN,
        password,
    )


def initial_password(conn: psycopg.Connection, name: str = ADMIN) -> str | None:
    row = conn.execute("SELECT initial_password FROM accounts WHERE name = %s", (name,)).fetchone()
    return row[0] if row else None


def reset(conn: psycopg.Connection, name: str = ADMIN) -> str:
    """A new initial password; every session of the account ends."""
    password = _new_password()
    with conn.transaction():
        row = conn.execute(
            "UPDATE accounts SET password_hash = %s, initial_password = %s, changed_at = now()"
            " WHERE name = %s RETURNING id",
            (_hasher.hash(password), password, name),
        ).fetchone()
        if row is None:
            raise Refused(f"no account '{name}'")
        conn.execute("DELETE FROM sessions WHERE account_id = %s", (row[0],))
    return password


class Throttle:
    """Failed logins per name, in memory: a restart forgets them."""

    def __init__(self) -> None:
        self._failed: dict[str, list[datetime]] = defaultdict(list)
        self._lock = threading.Lock()

    def locked(self, name: str, now: datetime) -> bool:
        with self._lock:
            recent = [t for t in self._failed[name] if now - t < LOCK]
            self._failed[name] = recent
            return len(recent) >= FAILURES

    def failed(self, name: str, now: datetime) -> None:
        with self._lock:
            self._failed[name].append(now)

    def passed(self, name: str) -> None:
        with self._lock:
            self._failed.pop(name, None)


def login(conn: psycopg.Connection, name: str, password: str, now: datetime) -> tuple[str, Account] | None:
    """A new session token for a right password."""
    row = conn.execute(
        "SELECT id, password_hash, initial_password IS NOT NULL FROM accounts WHERE name = %s", (name,)
    ).fetchone()
    if row is None:
        # As long as a real check, so that a name cannot be told by timing.
        _hasher.hash(password)
        return None
    account_id, hashed, must_change = row
    try:
        _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return None
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, account_id, created_at, seen_at) VALUES (%s, %s, %s, %s)",
        (_digest(token), account_id, now, now),
    )
    conn.commit()
    return token, Account(account_id, name, must_change)


def session(conn: psycopg.Connection, token: str, now: datetime) -> Account | None:
    row = conn.execute(
        "UPDATE sessions s SET seen_at = %s FROM accounts a WHERE s.token_hash = %s"
        " AND a.id = s.account_id AND s.seen_at > %s RETURNING a.id, a.name, a.initial_password IS NOT NULL",
        (now, _digest(token), now - IDLE),
    ).fetchone()
    conn.commit()
    return Account(*row) if row else None


def logout(conn: psycopg.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_digest(token),))
    conn.commit()


def change(conn: psycopg.Connection, account: Account, old: str, new: str, token: str) -> None:
    """A new password; the initial one stops working, and so do the
    account's other sessions."""
    (hashed,) = conn.execute("SELECT password_hash FROM accounts WHERE id = %s", (account.id,)).fetchone()
    try:
        _hasher.verify(hashed, old)
    except (VerificationError, InvalidHashError) as e:
        raise Refused("the current password is wrong") from e
    if len(new) < MIN_PASSWORD:
        raise Refused(f"the new password needs at least {MIN_PASSWORD} characters")
    if new == old:
        raise Refused("the new password must differ from the current one")
    conn.execute(
        "UPDATE accounts SET password_hash = %s, initial_password = NULL, changed_at = now() WHERE id = %s",
        (_hasher.hash(new), account.id),
    )
    conn.execute(
        "DELETE FROM sessions WHERE account_id = %s AND token_hash <> %s", (account.id, _digest(token))
    )
    conn.commit()


def _new_password() -> str:
    return secrets.token_urlsafe(12)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
