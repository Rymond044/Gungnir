"""Preserve demonstration labels and optionally import through the normal CLI.

This only labels the supplied fresh demonstration directory. It never replays,
scores, changes the С-1…С-12 table, or edits historical scenario runs.
"""

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from demo import ROOT, stall_share


def make_labels(folder):
    result = json.loads((folder / "result.json").read_text())
    starts = json.loads((folder / "scope-starts.json").read_text())
    sample_file = folder / "victim.jsonl"
    samples = (
        [json.loads(line) for line in sample_file.read_text().splitlines()]
        if sample_file.exists()
        else []
    )
    story = result["story"]
    main = "build" if story == "expected" else "culprit"
    if "victim" not in starts:
        return []
    onset = starts.get(main, starts["victim"])["started_ms_estimate"]
    end = round(datetime.fromisoformat(result["end"]).timestamp() * 1000)
    begin = starts["victim"]["started_ms_estimate"]
    base = {
        "run": folder.name,
        "kind": "demonstration",
        "normal": False,
        "expected": [],
    }
    labels = []
    if main not in starts:
        return [
            {
                **base,
                "phase": "victim-lead-aborted",
                "start_ms": begin,
                "end_ms": end,
                "scope": folder.name + "-victim.scope",
                "cause": None,
                "harm": None,
            }
        ]
    if begin < onset:
        labels.append(
            {
                **base,
                "phase": "victim-lead",
                "start_ms": begin,
                "end_ms": onset,
                "scope": folder.name + "-victim.scope",
                "cause": None,
                "harm": None,
            }
        )
    harm = None
    if story != "expected" and result.get("victim_before_30s") is not None:
        harm = result["victim_before_30s"] >= 0.10
    elif story == "expected" and result.get("victim_stall"):
        shares = list(result["victim_stall"].values())
        if all(s is not None for s in shares):
            harm = any(s >= 0.10 for s in shares)
    elif end - onset >= 30000:
        shares = [
            stall_share(samples, onset / 1000, end / 1000, r)
            for r in ("cpu", "io", "memory")
        ]
        if all(s is not None for s in shares):
            harm = any(s >= 0.10 for s in shares)
    if end > onset:
        labels.append(
            {
                **base,
                "phase": story,
                "start_ms": onset,
                "end_ms": end,
                "scope": folder.name + "-" + main + ".scope",
                "cause": None if story == "expected" else "cpu_hog",
                "harm": harm,
            }
        )
    return labels


def save_and_import(folder, host, import_now=False):
    labels = make_labels(folder)
    path = folder / "labels.jsonl"
    path.write_text("".join(json.dumps(label) + "\n" for label in labels))
    if import_now and labels:
        with path.open() as source:
            completed = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(ROOT / "deploy/compose.yaml"),
                    "exec",
                    "-T",
                    "server",
                    "gungnir-server",
                    "runs",
                    "import",
                    host,
                    "-",
                ],
                stdin=source,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        (folder / "label-import.json").write_text(
            json.dumps(
                {
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
                indent=2,
            )
            + "\n"
        )
        completed.check_returncode()
    return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--import-now", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.host):
        parser.error("invalid host")
    folder = args.folder.resolve()
    if folder.parent != (
        ROOT / "scenarios/runs"
    ).resolve() or not folder.name.startswith("gungnir-demo-"):
        parser.error("supply one fresh gungnir-demo directory under scenarios/runs")
    labels = save_and_import(folder, args.host, args.import_now)
    print(json.dumps(labels, indent=2))


if __name__ == "__main__":
    main()
