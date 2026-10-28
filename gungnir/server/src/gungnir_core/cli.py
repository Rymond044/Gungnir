"""Command line: `gungnir-core serve | migrate | maintain | status | host add|list|rotate-token`."""

import argparse
import logging
import sys
from datetime import UTC, datetime

import psycopg

from gungnir_core import hosts, maintenance, telemetry
from gungnir_core.db import database_url, migrate
from gungnir_core.display import render_status


def main() -> None:
    parser = argparse.ArgumentParser(prog="gungnir-core")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the API (applies migrations first)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    commands.add_parser("migrate", help="apply pending schema migrations")
    commands.add_parser("maintain", help="create partitions, roll up and expire data now")

    status = commands.add_parser("status", help="show a host's agent, signal sources and latest values")
    status.add_argument("name")
    status.add_argument("--groups", action="store_true", help="also list control group series")

    host = commands.add_parser("host", help="manage hosts").add_subparsers(dest="action", required=True)
    host.add_parser("add", help="add a host and print its agent token once").add_argument("name")
    host.add_parser("rotate-token", help="replace a host's agent token").add_argument("name")
    host.add_parser("list", help="list hosts")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        import uvicorn

        from gungnir_core.api import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return

    with psycopg.connect(database_url()) as conn:
        if args.command == "migrate":
            applied = migrate(conn)
            print("\n".join(applied) if applied else "schema is up to date")
            return
        migrate(conn)
        if args.command == "maintain":
            print(maintenance.run(conn))
        elif args.command == "status":
            show_status(conn, args.name, args.groups)
        elif args.action == "add":
            try:
                token = hosts.add(conn, args.name)
            except psycopg.errors.UniqueViolation:
                sys.exit(f"host '{args.name}' already exists")
            print(token)
        elif args.action == "rotate-token":
            token = hosts.rotate_token(conn, args.name)
            if token is None:
                sys.exit(f"no host '{args.name}'")
            print(token)
        elif args.action == "list":
            for h in hosts.summaries(conn):
                seen = h.last_seen_at.isoformat(timespec="seconds") if h.last_seen_at else "never"
                print(f"{h.name}\tlast seen {seen}\tagent {h.agent_version or '-'}")


def show_status(conn: psycopg.Connection, name: str, groups: bool) -> None:
    summary = hosts.summary(conn, name)
    host = hosts.by_name(conn, name)
    if summary is None or host is None:
        sys.exit(f"no host '{name}'")
    sources = telemetry.source_states(conn, host)
    series = telemetry.series_of(conn, host)
    now = datetime.now(UTC)
    facts = telemetry.latest_facts(conn, host)
    processes = telemetry.processes_at(conn, host, now)
    print(render_status(summary, sources, series, now, facts, processes, groups))


if __name__ == "__main__":
    main()
