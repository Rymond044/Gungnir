#!/usr/bin/env python3
"""One bounded demonstration, with confirmation only through the browser.

Run --check first. Does not confirm proposals, request keep/revert, rewrite
historical results, or run the control suite. Evidence remains local in runs/.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CGROUP = Path("/sys/fs/cgroup")


def command(args, **kwargs):
    return subprocess.run(
        args, check=True, text=True, capture_output=True, timeout=30, **kwargs
    ).stdout.strip()


def sql(query):
    # No credentials or host token enter the evidence. This connection cannot write.
    text = command(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "deploy/compose.yaml"),
            "exec",
            "-T",
            "db",
            "psql",
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "gungnir",
            "-d",
            "gungnir",
            "-c",
            "BEGIN READ ONLY; " + query + "; COMMIT",
        ]
    )
    lines = [line for line in text.splitlines() if line not in ("BEGIN", "COMMIT")]
    return json.loads("\n".join(lines))


def snapshot(host, start, unit):
    # Identifiers are validated on entry; timestamps and scope names are generated here.
    return sql(f"""SELECT json_build_object(
      'at', now(),
      'lag', (SELECT extract(epoch FROM now()-at) FROM detection_state WHERE host_id=h.id),
      'seen_age', extract(epoch FROM now()-h.last_seen_at),
      'incidents', (SELECT coalesce(json_agg(i), '[]') FROM incidents i WHERE i.host_id=h.id
          AND i.replay_id IS NULL AND (i.closed_at IS NULL OR i.closed_at >= '{start}')),
      'actions', (SELECT coalesce(json_agg(a), '[]') FROM actions a WHERE a.host_id=h.id
          AND (a.confirmed_at >= '{start}' OR a.state IN ('queued','applied','kept'))),
      'proposals', (SELECT coalesce(json_agg(p), '[]') FROM proposals p JOIN incidents i
          ON i.id=p.incident_id WHERE i.host_id=h.id AND i.replay_id IS NULL
          AND (p.created_at >= '{start}' OR p.state='proposed')),
      'facts', (SELECT coalesce(json_agg(f), '[]') FROM facts f WHERE f.host_id=h.id
          AND f.name='workload.class' AND (f.ts >= '{start}' OR f.ts=(SELECT max(ts) FROM facts
          WHERE host_id=h.id AND name='workload.class' AND ts <= '{start}'))),
      'records', (SELECT coalesce(json_agg(r), '[]') FROM (SELECT a.*, s.signal, s.entity
          FROM anomalies a JOIN series s ON s.id=a.series_id WHERE a.host_id=h.id
          AND a.replay_id IS NULL AND a.started_at >= '{start}' AND s.entity LIKE '%{unit}%') r),
      'host_records', (SELECT coalesce(json_agg(r), '[]') FROM (SELECT a.*, s.signal, s.entity
          FROM anomalies a JOIN series s ON s.id=a.series_id WHERE a.host_id=h.id
          AND a.replay_id IS NULL AND a.started_at >= '{start}' AND s.entity='host') r),
      'log', (SELECT coalesce(json_agg(l), '[]') FROM action_log l JOIN actions a ON a.id=l.action_id
          WHERE a.host_id=h.id AND a.confirmed_at >= '{start}')
      ) FROM hosts h WHERE h.name='{host}'""")


def build_annotations(records, explained, onset, end, unit):
    # Host build context and membership of a build scope are distinct evidence.
    explained = {str(rid) for rid in explained}
    return [
        r
        for r in records
        if str(r["id"]) in explained
        and r["kind"] == "anomaly"
        and onset <= datetime.fromisoformat(r["started_at"]).timestamp() <= end
        and (
            r["entity"].endswith("/" + unit + "-build.scope")
            or (r["entity"] == "host" and r["workload_class"] == "build")
        )
    ]


def pressure(path):
    # Missing PSI is unavailable evidence, never zero waiting.
    for line in path.read_text().splitlines():
        if line.startswith("some "):
            for item in line.split()[1:]:
                if item.startswith("total="):
                    return int(item[6:])
    raise ValueError(f"no some total in {path}")


def stall_share(rows, since, until, resource="cpu"):
    selected = [
        r for r in rows if since <= r["time"] <= until and r.get(resource) is not None
    ]
    if len(selected) < 2:
        return None
    first, last = selected[0], selected[-1]
    elapsed = last["time"] - first["time"]
    delta = last[resource] - first[resource]
    return delta / (elapsed * 1e6) if elapsed > 0 and delta >= 0 else None


def restored(action):
    files = action.get("files") or []
    if not files:
        return False
    for item in files:
        path = CGROUP / item["group"].lstrip("/") / item["file"]
        if item.get("key") or path.read_text().strip() != item["before"].strip():
            return False
    return True


class Runner:
    def __init__(self, args):
        self.args = args
        self.stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.unit = f"gungnir-demo-{self.stamp}-{args.story}"
        self.out = ROOT / "scenarios/runs" / self.unit
        self.units = []
        self.processes = []
        self.helper_groups = []
        self.scope_starts = {}
        self.samples = []
        self.last_db = 0
        self.state = None
        self.start = datetime.now(UTC).isoformat()
        self.apps = (
            CGROUP
            / f"user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice"
        )

    def preflight(self):
        for tool in (
            "docker",
            "systemd-run",
            "systemctl",
            "systemd-inhibit",
            "stress-ng",
            "cargo",
        ):
            if shutil.which(tool) is None:
                raise RuntimeError(f"missing {tool}")
        if len(os.sched_getaffinity(0)) < 4:
            raise RuntimeError("demonstration requires at least four available CPUs")
        for service in ("gungnir-agent", "gungnir-actuator"):
            if command(["systemctl", "is-active", service]) != "active":
                raise RuntimeError(f"{service} is not active")
        supplies = list(Path("/sys/class/power_supply").glob("*/online"))
        mains = any(p.read_text().strip() == "1" for p in supplies)
        battery = [
            int(p.read_text().strip())
            for p in Path("/sys/class/power_supply").glob("*/capacity")
        ]
        if not mains and battery and min(battery) < 20:
            raise RuntimeError(
                "battery below 20%; connect mains before a CPU demonstration"
            )
        state = snapshot(self.args.host, self.start, self.unit)
        if state is None:
            raise RuntimeError("host does not exist")
        if (
            state["lag"] is None
            or state["lag"] > 60
            or state["seen_age"] is None
            or state["seen_age"] > 30
        ):
            raise RuntimeError("collection or detection is stale")
        if state["incidents"] or state["actions"]:
            raise RuntimeError("wait until existing incidents and actions are closed")
        if not (self.apps / "cpu.pressure").is_file():
            raise RuntimeError("user cgroup PSI is unavailable")
        state["power"] = {"mains": mains, "battery_percent": battery}
        return state

    def launch(self, suffix, args):
        unit = self.unit + "-" + suffix + ".scope"
        self.units.append(unit)
        log = (self.out / (suffix + ".log")).open("w")
        process = subprocess.Popen(
            [
                "systemd-run",
                "--user",
                "--scope",
                "--quiet",
                "--unit=" + unit,
                "--",
                *args,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        self.processes.append(process)
        deadline = time.monotonic() + 10
        while not (self.apps / unit / "cpu.pressure").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"{unit} failed to start")
            time.sleep(0.1)
        properties = dict(
            line.split("=", 1)
            for line in command(
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit,
                    "-p",
                    "ActiveEnterTimestamp",
                    "-p",
                    "ActiveEnterTimestampMonotonic",
                    "-p",
                    "ControlGroup",
                ]
            ).splitlines()
        )
        properties["started_ms_estimate"] = round(
            (
                time.time()
                - time.monotonic()
                + int(properties["ActiveEnterTimestampMonotonic"]) / 1e6
            )
            * 1000
        )
        properties["command"] = args
        self.scope_starts[suffix] = properties
        (self.out / "scope-starts.json").write_text(
            json.dumps(self.scope_starts, indent=2) + "\n"
        )
        return process, self.apps / unit

    def tick(self, victim):
        row = {"time": time.time()}
        for resource in ("cpu", "io", "memory"):
            try:
                row[resource] = pressure(victim / (resource + ".pressure"))
            except (OSError, ValueError) as error:
                row[resource] = None
                row[resource + "_error"] = str(error)
        self.samples.append(row)
        with (self.out / "victim.jsonl").open("a") as file:
            file.write(json.dumps(row) + "\n")
        if time.monotonic() - self.last_db >= 5:
            self.state = snapshot(self.args.host, self.start, self.unit)
            with (self.out / "observations.jsonl").open("a") as file:
                file.write(json.dumps(self.state) + "\n")
            self.last_db = time.monotonic()
        time.sleep(1)

    def wait(self, seconds, victim):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.tick(victim)

    def cleanup(self):
        errors = []
        # Only the exact transient scopes started by this runner are stopped.
        for unit in reversed(self.units):
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop", unit],
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", unit],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=10,
                )
                if result.stdout.strip() in ("active", "activating", "deactivating"):
                    errors.append(unit)
            except subprocess.TimeoutExpired:
                errors.append(unit)
        for group in self.helper_groups:
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        (self.out / "cleanup.json").write_text(
            json.dumps({"remaining_scopes": errors}, indent=2) + "\n"
        )
        if errors:
            raise RuntimeError("scopes still running: " + ", ".join(errors))

    def run(self):
        preflight = self.preflight()
        if self.args.check:
            print(json.dumps(preflight, indent=2))
            print("Preflight passed; no workloads started.")
            return
        self.out.mkdir(parents=True)
        (self.out / "preflight.json").write_text(json.dumps(preflight, indent=2) + "\n")
        copied = []
        for source in (Path(__file__), Path(__file__).with_name("demo_labels.py")):
            shutil.copy2(source, self.out / source.name)
            copied.append(source)
        (self.out / "source-sha256.json").write_text(
            json.dumps(
                {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in copied},
                indent=2,
            )
            + "\n"
        )
        print(f"Evidence: {self.out}", flush=True)
        self.processes.append(
            subprocess.Popen(
                [
                    "systemd-inhibit",
                    "--what=idle:sleep:handle-lid-switch",
                    "--who=gungnir",
                    "--why=manual demonstration",
                    "sleep",
                    "900",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        )
        self.helper_groups.append(self.processes[-1].pid)
        result = {"story": self.args.story, "passed": False, "start": self.start}
        scratch = Path(tempfile.mkdtemp(prefix="gungnir-demo-", dir=self.out))
        try:
            result.update(self.story(scratch))
        except BaseException as error:
            result["error"] = str(error)
            raise
        finally:
            try:
                self.cleanup()
            except BaseException as error:
                result["passed"] = False
                result["cleanup_error"] = str(error)
                raise
            finally:
                shutil.rmtree(scratch)
                result["end"] = datetime.now(UTC).isoformat()
                (self.out / "result.json").write_text(
                    json.dumps(result, indent=2) + "\n"
                )
                if self.scope_starts:
                    try:
                        from demo_labels import save_and_import

                        save_and_import(self.out, self.args.host, import_now=True)
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        result["label_import_error"] = str(error)
                        result["passed"] = False
                        (self.out / "result.json").write_text(
                            json.dumps(result, indent=2) + "\n"
                        )
        print(json.dumps(result, indent=2), flush=True)
        if not result["passed"]:
            raise RuntimeError("demonstration acceptance failed; see result.json")

    def story(self, scratch):
        cpus = len(os.sched_getaffinity(0))
        duration = 900
        expected = self.args.story == "expected"
        workers = (
            1
            if expected
            else (getattr(self.args, "victim_workers", None) or cpus * 2 // 3)
        )
        if workers < 1 or workers > cpus:
            raise RuntimeError(
                "victim workers must be between 1 and the available CPU count"
            )
        (self.out / "workload.json").write_text(
            json.dumps(
                {
                    "available_cpus": cpus,
                    "victim_workers": workers,
                    "culprit_workers": 2 * cpus,
                },
                indent=2,
            )
            + "\n"
        )
        _, victim = self.launch(
            "victim",
            [
                "stress-ng",
                "--cpu",
                str(workers),
                "--timeout",
                f"{duration}s",
            ],
        )
        self.wait(90, victim)
        onset = time.time()
        if expected:
            build, _ = self.launch(
                "build",
                [
                    "env",
                    "CARGO_TARGET_DIR=" + str(scratch / "target"),
                    "timeout",
                    "180",
                    "cargo",
                    "build",
                    "--release",
                    "--offline",
                    "--jobs",
                    "2",
                    "--manifest-path",
                    str(ROOT / "agent/Cargo.toml"),
                ],
            )
            while build.poll() is None:
                self.tick(victim)
            end = time.time()
            # Require actual pipeline progress over the build, with a bounded wait.
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                self.tick(victim)
                at = datetime.fromisoformat(self.state["at"]).timestamp()
                if self.state["lag"] is not None and at - self.state["lag"] >= end:
                    break
            else:
                raise RuntimeError(
                    "detection did not reach the build's end within 120 seconds"
                )
            diagnosis = command(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(ROOT / "deploy/compose.yaml"),
                    "exec",
                    "-T",
                    "server",
                    "gungnir-server",
                    "diagnose",
                    self.args.host,
                    "--since",
                    datetime.fromtimestamp(onset, UTC).isoformat(),
                    "--until",
                    datetime.fromtimestamp(end, UTC).isoformat(),
                    "--json",
                ]
            )
            (self.out / "diagnosis.json").write_text(diagnosis + "\n")
            explained = set(json.loads(diagnosis)[0]["expected"])
            records = self.state["records"] + self.state["host_records"]
            annotations = build_annotations(records, explained, onset, end, self.unit)
            host_expected_ids = [r["id"] for r in annotations if r["entity"] == "host"]
            scope_expected_ids = [r["id"] for r in annotations if r["entity"] != "host"]
            expected_ids = scope_expected_ids + host_expected_ids
            share = {
                r: stall_share(self.samples, onset, end, r)
                for r in ("cpu", "io", "memory")
            }
            classes = [f for f in self.state["facts"] if f["value"] == "build"]
            no_impact = all(v is not None and v < 0.10 for v in share.values())
            return {
                "passed": build.returncode == 0
                and no_impact
                and bool(classes)
                and bool(expected_ids)
                and not self.state["incidents"]
                and not self.state["actions"],
                "build_exit": build.returncode,
                "victim_stall": share,
                "build_class_observed": bool(classes),
                "explicit_expected_record_ids": expected_ids,
                "scope_expected_record_ids": scope_expected_ids,
                "host_expected_record_ids": host_expected_ids,
                "incident_ids": [i["id"] for i in self.state["incidents"]],
                "note": "Explicit host build annotations or build-scope annotations are required; host context does not establish scope membership or a learned baseline.",
            }
        self.launch(
            "culprit", ["stress-ng", "--cpu", str(2 * cpus), "--timeout", "780s"]
        )
        if self.args.story == "rollback":
            self.launch(
                "other", ["stress-ng", "--cpu", str(2 * cpus), "--timeout", "780s"]
            )
        target = self.unit + "-culprit.scope"
        print(
            f"Confirm ONLY cpu_weight proposal for {target} in Chrome. No automatic confirmation.",
            flush=True,
        )
        deadline = time.monotonic() + 180
        action = None
        printed = set()
        while time.monotonic() < deadline:
            self.tick(victim)
            for proposal in self.state["proposals"]:
                if (
                    proposal["target"].endswith("/" + target)
                    and proposal["id"] not in printed
                ):
                    print(
                        f"Proposal {proposal['id']}: {proposal['action']}; incident {proposal['incident_id']}",
                        flush=True,
                    )
                    printed.add(proposal["id"])
            action = next(
                (
                    a
                    for a in self.state["actions"]
                    if a["target"].endswith("/" + target)
                ),
                None,
            )
            if action and action["state"] == "applied":
                break
            if action and action["state"] == "refused":
                raise RuntimeError("actuator refused browser-confirmed action")
        else:
            raise RuntimeError(
                "no browser-confirmed, applied action within 180 seconds"
            )
        if action["action"] != "cpu_weight":
            raise RuntimeError("wrong action confirmed")
        applied_files = action.get("files") or []
        applied_file_values = [
            (CGROUP / f["group"].lstrip("/") / f["file"]).read_text().strip()
            for f in applied_files
        ]
        application_observed = bool(applied_files) and all(
            value == f["after"].strip() and f["before"].strip() != f["after"].strip()
            for f, value in zip(applied_files, applied_file_values)
        )
        applied = datetime.fromisoformat(action["applied_at"]).timestamp()
        print(
            f"Action {action['id']} applied. Waiting for the actual verification verdict (~5 minutes).",
            flush=True,
        )
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            self.tick(victim)
            action = next(a for a in self.state["actions"] if a["id"] == action["id"])
            if action["outcome"]:
                break
        else:
            raise RuntimeError("verification did not finish within 420 seconds")
        verified = datetime.fromisoformat(action["verified_at"]).timestamp()
        before = stall_share(self.samples, applied - 30, applied)
        after = stall_share(self.samples, verified - 30, verified)
        print(f"Verdict: {action['outcome']}; victim {before} -> {after}.", flush=True)
        if self.args.story == "cpu":
            print(
                "Use Revert in Chrome now; Keep is not used. Waiting for actuator acknowledgement.",
                flush=True,
            )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self.tick(victim)
            action = next(a for a in self.state["actions"] if a["id"] == action["id"])
            if action["state"] == "reverted":
                break
        else:
            raise RuntimeError("rollback not acknowledged within 120 seconds")
        file_restored = restored(action)
        outcome = "worked" if self.args.story == "cpu" else "failed"
        passed = before is not None and after is not None and before >= 0.10
        passed = passed and (after < 0.10 if outcome == "worked" else after >= 0.10)
        passed = (
            passed
            and action["outcome"] == outcome
            and file_restored
            and application_observed
        )
        return {
            "passed": passed,
            "action_id": action["id"],
            "outcome": action["outcome"],
            "state": action["state"],
            "file_restored": file_restored,
            "application_observed": application_observed,
            "applied_file_values": applied_file_values,
            "victim_before_30s": before,
            "victim_after_30s": after,
            "applied_at": action["applied_at"],
            "verified_at": action["verified_at"],
            "note": "Independent PSI windows are aligned to application and verification, not confirmation.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("story", choices=("cpu", "rollback", "expected"))
    parser.add_argument(
        "--check", action="store_true", help="read-only preflight; no workloads"
    )
    parser.add_argument(
        "--victim-workers",
        type=int,
        help="CPU victim worker count (default: two thirds of CPUs)",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.host):
        parser.error("invalid host name")

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        Runner(args).run()
    except (
        RuntimeError,
        subprocess.SubprocessError,
        OSError,
        KeyboardInterrupt,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
