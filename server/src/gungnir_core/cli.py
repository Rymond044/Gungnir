"""Command line of the core.

`serve | migrate | maintain | process | status | host add|list|rotate-token |
runs import|list|quiet | calibrate | workload show|unmatched|assign | baselines |
anomalies | replay | evaluate | smd | knowledge check | diagnose`.
"""

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg

from gungnir_core import (
    anomalies,
    base,
    baselines,
    capacity,
    gather,
    hosts,
    labels,
    maintenance,
    ontology,
    params,
    pipeline,
    telemetry,
    workload,
)
from gungnir_core.db import database_url, migrate
from gungnir_core.detection import evaluation, replay, smd
from gungnir_core.display import (
    host_zone,
    render_baselines,
    render_diagnosis,
    render_records,
    render_status,
    stamp,
)
from gungnir_core.frame import fact_spans, host_series
from gungnir_core.knowledge import KnowledgeError
from gungnir_core.reasoner import RuleReasoner, as_dict


def main() -> None:
    parser = argparse.ArgumentParser(prog="gungnir-core")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the API (applies migrations first)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    commands.add_parser("migrate", help="apply pending schema migrations")
    commands.add_parser("maintain", help="create partitions, roll up and expire data now")
    commands.add_parser("process", help="run the workload classifier and due histograms for all hosts now")

    status = commands.add_parser("status", help="show a host's agent, signal sources and latest values")
    status.add_argument("name")
    status.add_argument("--groups", action="store_true", help="also list control group series")

    host = commands.add_parser("host", help="manage hosts").add_subparsers(dest="action", required=True)
    host.add_parser("add", help="add a host and print its agent token once").add_argument("name")
    host.add_parser("rotate-token", help="replace a host's agent token").add_argument("name")
    host.add_parser("list", help="list hosts")

    runs = commands.add_parser("runs", help="labeled runs").add_subparsers(dest="action", required=True)
    imp = runs.add_parser("import", help="import a label file ('-' reads standard input)")
    imp.add_argument("name")
    imp.add_argument("file")
    runs.add_parser("list", help="list a host's labeled runs").add_argument("name")
    quiet = runs.add_parser("quiet", help="mark an interval the owner knows was quiet (UTC or with offset)")
    quiet.add_argument("name")
    quiet.add_argument("since", type=datetime.fromisoformat)
    quiet.add_argument("until", type=datetime.fromisoformat)

    cal = commands.add_parser("calibrate", help="derive host capacity from an imported calibration run")
    cal.add_argument("name")
    cal.add_argument("run")

    wl = commands.add_parser("workload", help="workload classes").add_subparsers(dest="action", required=True)
    show = wl.add_parser("show", help="class changes of the last hours")
    show.add_argument("name")
    show.add_argument("--hours", type=float, default=24)
    um = wl.add_parser("unmatched", help="processes and units with a noticeable share that no rule covers")
    um.add_argument("name")
    um.add_argument("--hours", type=float, default=24)
    asg = wl.add_parser("assign", help="add a host-local rule: a command or unit belongs to a class")
    asg.add_argument("name")
    asg.add_argument("cls", metavar="class", choices=workload.RULED)
    which = asg.add_mutually_exclusive_group(required=True)
    which.add_argument("--command")
    which.add_argument("--unit")

    an = commands.add_parser("anomalies", help="anomaly and impact records, newest first")
    an.add_argument("name")
    an.add_argument("--hours", type=float, default=24, help="records that overlap the last hours (24)")
    an.add_argument("--open", action="store_true", help="only records still open")
    an.add_argument("--detector", help="only this detector, e.g. robust, threshold, impact, iforest")
    an.add_argument("--replay", type=int, help="records of this replay instead of live ones")
    an.add_argument("--limit", type=int, default=50)

    bl = commands.add_parser("baselines", help="show a host's current baselines")
    bl.add_argument("name")
    bl.add_argument("--signal", help="only series of this signal (glob)")

    rp = commands.add_parser("replay", help="run detection variants over stored data")
    rp.add_argument("name")
    rp.add_argument("variants", nargs="*", help=f"default: {', '.join(replay.COMPARED)} and impact")
    rp.add_argument("--since", type=datetime.fromisoformat)
    rp.add_argument("--until", type=datetime.fromisoformat)

    ev = commands.add_parser("evaluate", help="score the latest replay of each variant against labeled runs")
    ev.add_argument("name")
    ev.add_argument("--out", type=Path, help="also write the table to this Markdown file")

    sm = commands.add_parser("smd", help="robust branch and forest on the Server Machine Dataset")
    sm.add_argument("root", type=Path, help="the dataset's directory: train/, test/, test_label/, ...")
    sm.add_argument("--machine", action="append", help="only these machines")
    sm.add_argument("--out", type=Path)

    dg = commands.add_parser("diagnose", help="ranked hypotheses of an interval, with their derivation trees")
    dg.add_argument("name")
    dg.add_argument("--minutes", type=float, default=30, help="the last minutes (30)")
    dg.add_argument("--since", type=datetime.fromisoformat, help="UTC unless an offset is given")
    dg.add_argument("--until", type=datetime.fromisoformat)
    dg.add_argument("--run", help="each phase of this labeled run that is not normal, with its tail")
    dg.add_argument("--replay", type=int, help="records of this replay instead of live ones")
    dg.add_argument("--json", action="store_true")

    kn = commands.add_parser("knowledge", help="knowledge base").add_subparsers(dest="action", required=True)
    kc = kn.add_parser("check", help="check the ontology and the names the knowledge files use")
    kc.add_argument("--no-reasoner", action="store_true", help="skip HermiT, which needs Java and owlready2")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        import uvicorn

        from gungnir_core.api import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return

    if args.command == "knowledge":
        sys.exit(check_knowledge(not args.no_reasoner))

    if args.command == "smd":
        report, _ = smd.run(args.root, params.load(), args.machine)
        report = f"# Server Machine Dataset, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC\n\n{report}\n"
        print(report)
        if args.out:
            args.out.write_text(report)
        return

    with psycopg.connect(database_url()) as conn:
        if args.command == "migrate":
            applied = migrate(conn)
            print("\n".join(applied) if applied else "schema is up to date")
            return
        migrate(conn)
        if args.command == "maintain":
            print(maintenance.run(conn))
        elif args.command == "process":
            now = datetime.now(UTC)
            for h in pipeline.all_hosts(conn):
                pipeline.run_host(conn, h, params.load(), now)
        elif args.command == "runs":
            run_labels(conn, args)
        elif args.command == "calibrate":
            host = need_host(conn, args.name)
            for c in capacity.calibrate(conn, host, args.run, params.load()):
                print(f"{c.signal}\t{c.entity}\t{c.value:.6g}\t({c.phase})")
        elif args.command == "workload":
            run_workload(conn, args)
        elif args.command == "replay":
            run_replays(conn, args)
        elif args.command == "evaluate":
            run_evaluation(conn, args)
        elif args.command == "anomalies":
            show_anomalies(conn, args)
        elif args.command == "diagnose":
            run_diagnosis(conn, args)
        elif args.command == "baselines":
            host = need_host(conn, args.name)
            book = baselines.Book(conn, host, params.load()).at(datetime.now(UTC))
            print(render_baselines(host_series(conn, host), book, args.signal))
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


def check_knowledge(reasoner: bool) -> int:
    try:
        kb = base.load()
        onto = kb.ontology
        counts = ", ".join(f"{category} {len(onto.names(category))}" for category in ontology.CATEGORIES)
        print(f"{ontology.path()}: {counts}")
        print(f"detection and workload files: {kb.references} entries name only what the ontology has")
        shown = sum(len(r.manifestations) for r in kb.rules.values())
        print(f"rules: {len(kb.rules)} causes, {shown} manifestations")
        print(f"expected behavior: {len(kb.expected)} entries")
        print(
            f"policy: {len(kb.policy.entries)} actions, {len(kb.policy.advisories)} advisories,"
            f" contraindications for {len(kb.policy.contraindicated)} causes"
        )
        ran, failures = base.run_tests(kb)
        if failures:
            raise KnowledgeError("knowledge tests failed:\n" + "\n".join(failures))
        print(f"knowledge tests: {ran} cases pass")
        if reasoner:
            classified = ontology.consistency()
            print("HermiT: consistent")
            for cls, causes in classified.items():
                print(f"  {cls}: {', '.join(causes) or '-'}")
    except KnowledgeError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


def need_host(conn: psycopg.Connection, name: str) -> hosts.Host:
    host = hosts.by_name(conn, name)
    if host is None:
        sys.exit(f"no host '{name}'")
    return host


def run_labels(conn: psycopg.Connection, args) -> None:
    host = need_host(conn, args.name)
    if args.action == "quiet":
        run = f"{args.since.astimezone(UTC):%Y%m%dT%H%M%SZ}-quiet"
        phase = labels.PhaseLabel(
            run=run,
            kind="quiet",
            phase="quiet",
            start_ms=round(args.since.timestamp() * 1000),
            end_ms=round(args.until.timestamp() * 1000),
            normal=True,
        )
        labels.import_phases(conn, host, [phase], timedelta(0))
        print(f"marked {run}")
        return
    if args.action == "import":
        try:
            if args.file == "-":
                phases = labels.parse(sys.stdin)
            else:
                with open(args.file) as f:
                    phases = labels.parse(f)
        except ValueError as err:
            sys.exit(f"{args.file}: {err}")
        print(f"imported {labels.import_phases(conn, host, phases, params.load().label_tail)} phases")
    else:
        zone = host_zone(hosts.summary(conn, host.name))
        for run, kind, n, since, until in labels.runs_of(conn, host):
            print(f"{run}\t{kind}\t{n} phases\t{stamp(since, zone)} … {stamp(until, zone)}")


def run_workload(conn: psycopg.Connection, args) -> None:
    host = need_host(conn, args.name)
    p = params.load()
    now = datetime.now(UTC)
    zone = host_zone(hosts.summary(conn, host.name))
    if args.action == "show":
        since = now - timedelta(hours=args.hours)
        for ts, cls in fact_spans(conn, host, workload.FACT, since, now):
            print(f"{stamp(ts, zone)}\t{cls}")
        state = workload.load_state(conn, host)
        if state.candidate:
            print(f"pending: {state.candidate} since {stamp(state.candidate_since, zone)}")
    elif args.action == "unmatched":
        classifier = workload.RuleClassifier(workload.load_rules(host.name), p)
        found = workload.unmatched(conn, host, classifier, p, now - timedelta(hours=args.hours), now)
        if not found:
            print("every process and unit with a noticeable share is covered by a rule")
        for u in found:
            peak = f"{u.peak:.2f} cores" if u.kind == "command" else f"{u.peak:.0%} of disk I/O"
            print(f"{u.kind} {u.name}\t{u.noticeable} samples\tpeak {peak}\t{u.example or ''}")
        if found:
            print("assign one: gungnir-core workload assign <host> <class> --command <name> | --unit <unit>")
    else:
        path = workload.assign(host.name, args.cls, args.command, args.unit, date.today())
        print(f"rule added to {path}")


def run_replays(conn: psycopg.Connection, args) -> None:
    host = need_host(conn, args.name)
    p = params.load()
    first, last = replay.stored_span(conn, host)
    if first is None:
        sys.exit(f"no stored samples for '{host.name}'")
    since, until = args.since or first, args.until or last
    for variant in args.variants or [*replay.COMPARED, "impact"]:
        if variant not in replay.VARIANTS:
            sys.exit(f"unknown variant '{variant}'; known: {', '.join(replay.VARIANTS)}")
        replay_id, written = replay.replay(conn, host, variant, since, until, p)
        span = f"{since:%Y-%m-%d %H:%M} to {until:%Y-%m-%d %H:%M} UTC"
        print(f"replay {replay_id}: {variant}, {written} records, {span}")


def run_evaluation(conn: psycopg.Connection, args) -> None:
    host = need_host(conn, args.name)
    p = params.load()
    latest = replay.latest_replays(conn, host)
    if not latest:
        sys.exit("no replays yet: run `gungnir-core replay <host>` first")
    order = [v for v in (*replay.COMPARED, "impact") if v in latest]
    rows = [(v, replay.score_replay(conn, host, latest[v], p)) for v in order]
    report = f"# Detection on {host.name}, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC\n\n"
    report += evaluation.table(rows) + "\n"
    missed = {v: s.missed for v, s in rows if s.missed}
    if missed:
        report += "\nMissed phases:\n\n" + "".join(f"- {v}: {', '.join(m)}\n" for v, m in missed.items())
    print(report)
    if args.out:
        args.out.write_text(report)


def show_anomalies(conn: psycopg.Connection, args) -> None:
    host = need_host(conn, args.name)
    now = datetime.now(UTC)
    rows = anomalies.records_of(
        conn, host, now - timedelta(hours=args.hours), args.replay, args.open, args.detector, args.limit
    )
    zone = host_zone(hosts.summary(conn, host.name))
    source = f"replay {args.replay}" if args.replay else "live"
    title = f"Records on {host.name} ({source}, last {args.hours:g} h, host local time)"
    print(render_records(rows, now, zone, title))


def run_diagnosis(conn: psycopg.Connection, args) -> None:
    host = need_host(conn, args.name)
    kb, p = base.load(), params.load()
    reasoner = RuleReasoner(kb, p)
    zone = host_zone(hosts.summary(conn, host.name))
    if args.run:
        phases = [ph for ph in labels.phases_of(conn, host, run=args.run) if not ph.normal]
        if not phases:
            sys.exit(f"run '{args.run}' has no phase that is not normal")
        spans = [(f"phase {ph.phase} ({ph.kind})", ph.since, ph.until + p.label_tail) for ph in phases]
    else:
        until = _utc(args.until) if args.until else datetime.now(UTC)
        since = _utc(args.since) if args.since else until - timedelta(minutes=args.minutes)
        spans = [("", since, until)]
    found = []
    for label, since, until in spans:
        d = reasoner.diagnose(gather.evidence(conn, host, kb, since, until, args.replay))
        if args.json:
            found.append(
                {"label": label, "since": since.isoformat(), "until": until.isoformat(), **as_dict(d)}
            )
            continue
        source = f", replay {args.replay}" if args.replay else ""
        title = f"{host.name}, {stamp(since, zone)} - {stamp(until, zone)}{source} {label}".rstrip()
        print(render_diagnosis(d, zone, title) + "\n")
    if args.json:
        print(json.dumps(found, indent=2, ensure_ascii=False))


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


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
