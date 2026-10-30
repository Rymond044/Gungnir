"""The processing loop of the core: every few seconds, for every host, the
workload classifier, then hourly histograms, then detection.

Each stage keeps its own progress in the database, so a restart continues
where it stopped and a stage that fails for one host does not hold up the
others.
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from psycopg_pool import ConnectionPool

from gungnir_core import baselines, params, workload
from gungnir_core.db import database_url
from gungnir_core.detection.runner import Runner
from gungnir_core.hosts import Host
from gungnir_core.params import Params

log = logging.getLogger(__name__)


def all_hosts(conn: psycopg.Connection) -> list[Host]:
    return [Host(*row) for row in conn.execute("SELECT id, name FROM hosts ORDER BY id").fetchall()]


def run_host(conn: psycopg.Connection, host: Host, p: Params, now: datetime) -> None:
    classifier = workload.RuleClassifier(workload.load_rules(host.name), p)
    _, accepted = workload.run(conn, host, classifier, p, now - p.detect_settle)
    if accepted:
        log.info("workload of %s: %d class changes", host.name, accepted)
    baselines.build_due(conn, host, p, now)


class Detection:
    """Live detection per host. Runners keep their baselines and forests in
    memory between steps, so they hold a connection of their own."""

    __name__ = "detect"

    def __init__(self, url: str | None = None) -> None:
        self.url = url or database_url()
        self.conn: psycopg.Connection | None = None
        self.runners: dict[int, Runner] = {}

    def __call__(self, _pooled: psycopg.Connection, host: Host, p: Params, now: datetime) -> None:
        if self.conn is None or self.conn.closed:
            # Autocommit, so that each step's own transaction is a real one.
            self.conn = psycopg.connect(self.url, autocommit=True)
            self.runners.clear()
        runner = self.runners.get(host.id)
        if runner is None:
            runner = self.runners[host.id] = Runner.live(self.conn, host, p)
        try:
            written = runner.run(now - p.detect_settle)
        except Exception:
            # Start over from the saved state.
            self.conn.rollback()
            self.runners.pop(host.id, None)
            raise
        if written:
            log.info("detection on %s: %d new records", host.name, written)


Stage = Callable[[psycopg.Connection, Host, Params, datetime], None]


class Worker:
    def __init__(self, pool: ConnectionPool, stages: list[Stage] | None = None) -> None:
        self._pool = pool
        self._params = params.load()
        self._stages = stages or [run_host, Detection(pool.conninfo)]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pipeline", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def _loop(self) -> None:
        while not self._stop.wait(self._params.detect_every.total_seconds()):
            try:
                with self._pool.connection() as conn:
                    hosts = all_hosts(conn)
            except Exception:
                log.exception("pipeline: no database; retrying")
                continue
            for host in hosts:
                for stage in self._stages:
                    try:
                        with self._pool.connection() as conn:
                            stage(conn, host, self._params, datetime.now(UTC))
                    except Exception:
                        log.exception("pipeline stage %s failed for %s", stage.__name__, host.name)
