# Commands

Everyday commands for running, checking and showing gungnIR. Run them from `code/gungnir` unless said otherwise. Times shown by the CLI are in the host's local time; SQL shows UTC.

A shorthand used below:

```sh
alias core='docker compose -f deploy/compose.yaml exec -T core gungnir-core'
alias dc='docker compose -f deploy/compose.yaml'
```

Commands that write files on the host (`workload assign`, `evaluate --out`, `smd`) run outside the container: `cd server && uv run gungnir-core ...`. The host-side CLI talks to the same database on `localhost:5432`.

## Start, stop, rebuild

| What | Command |
|---|---|
| Start core and database | `dc up -d` |
| Stop (data kept) | `dc down` |
| Delete all data | `dc down -v` (asks nothing, think first) |
| New core version after a code change | `dc up -d --build core` (migrations apply at start; the agent spools meanwhile) |
| Core logs | `dc logs -f core \| grep -v ingest` |
| New agent version | `(cd agent && cargo build --release) && (cd deploy/agent && sudo ./install.sh)` |
| Agent logs | `journalctl -u gungnir-agent -f` |
| What the agent sees | `gungnir-agent --probe` |

Back up the database before anything risky, restore into a scratch database to look at it:

```sh
mkdir -p ~/gungnir-backups
dc exec -T db pg_dump -U gungnir -Fc gungnir > ~/gungnir-backups/gungnir-$(date -u +%Y%m%dT%H%M%SZ).dump
dc exec -T db psql -U gungnir -c "CREATE DATABASE scratch"
dc exec -T db pg_restore -U gungnir -d scratch < ~/gungnir-backups/<file>.dump
```

## Is it working?

```sh
core host list                       # hosts and when their agent was last seen
core status laptop                   # sources, facts (incl. workload.class), top processes, latest values
core status laptop --groups          # also control group series
core workload show laptop            # class changes of the last 24 h, and a class still pending
core anomalies laptop                # live records of the last 24 h
core anomalies laptop --open         # records still open
dc exec db psql -U gungnir -c "SELECT now() - at AS behind FROM detection_state"   # detection lag, about 20-30 s
```

API in the browser: http://127.0.0.1:8080/docs.

## Tests and code checks

```sh
cd server && uv run ruff check && uv run ruff format --check && uv run pytest -q    # core; needs the database up
cd agent && cargo fmt --check && cargo clippy --all-targets && cargo test          # agent
bash -n scenarios/*.sh                                                              # scripts parse
```

## Detection

### Records

```sh
core anomalies laptop --hours 2                  # last two hours
core anomalies laptop --detector robust          # robust.mad and robust.ewma only (also: threshold, impact, iforest)
core anomalies laptop --replay 7 --hours 200     # records of a replay
```

Each record is two lines: when (start, end or `open`, length), what (kind, detector, signal and entity, direction, score), then the value that opened it, the workload class and power profile, and what it was judged against. The score means:

- robust: |z|, alarm above 3;
- threshold and impact: value divided by the limit, alarm above 1 (`>999` when a "below" limit met zero);
- forest: window score divided by the forest's threshold, alarm above 1.

### Workload classes

```sh
core workload show laptop --hours 48
core workload unmatched laptop                    # noticeable processes and units no rule covers
cd server && uv run gungnir-core workload assign laptop build --command zig       # or --unit name.service
```

`assign` appends a rule with provenance "operator, host, date" to `knowledge/local/laptop.toml`; the core reads it on its next step. Shipped rules: `knowledge/workload_classes.toml`.

### Baselines

```sh
core baselines laptop                    # every series and class with a baseline
core baselines laptop --signal 'disk.*'
```

A class gets a baseline after 6 hours of samples in the last 7 days; until then the command shows how many hours each class has.

### Labeled runs and calibration

```sh
scenarios/calibrate.sh laptop            # about 17 min, leave the laptop alone; imports, calibrates, lists unmatched
scenarios/overhead.sh 60                 # agent overhead, 60 s per phase; then import its file:
core runs import laptop - < scenarios/runs/<file>.jsonl
core runs list laptop
core calibrate laptop <run>              # derive capacity again from an imported calibration run
core runs quiet laptop 2026-10-30T22:00+03:00 2026-10-30T23:00+03:00    # an hour you know was quiet
```

Label files and result tables in `scenarios/runs/` are local, not in git.

## Comparing detectors

```sh
core replay laptop                               # five compared variants and impact over all stored data
core replay laptop robust-classes --since 2026-10-30T00:00+00:00 --until 2026-10-31T00:00+00:00
core evaluate laptop                             # table of the latest replay of each variant
cd server && uv run gungnir-core evaluate laptop --out ../scenarios/runs/$(date -u +%Y%m%dT%H%M%SZ)-evaluation.md
cd server && uv run gungnir-core smd ~/datasets/OmniAnomaly/ServerMachineDataset \
    --out ../scenarios/runs/$(date -u +%Y%m%dT%H%M%SZ)-smd.md                 # all 28 machines, about a minute
cd server && uv run gungnir-core smd ~/datasets/OmniAnomaly/ServerMachineDataset --machine machine-1-1
```

Variants: `thresholds`, `robust-host`, `robust-classes`, `iforest`, `robust-classes+iforest`, `impact`. Table columns: phases found (a record of an expected signal overlaps the phase), opened inside (that record started inside the phase), recall, median delay, false alarms per hour outside phases, false alarms per hour on quiet intervals.

### Trying other parameters

Every tunable is in `server/src/gungnir_core/params.py`. Override some for a host-side run with a TOML file:

```sh
printf 'robust_k = 4.0\nbaseline_min_hours = 12\n' > /tmp/p.toml
cd server && GUNGNIR_PARAMS=/tmp/p.toml uv run gungnir-core replay laptop robust-classes
```

The live core uses the defaults.

## Showing it (about 7 minutes)

Before: core up, calibration run done, a day of data collected.

1. `core status laptop`: the agent, its sources, the current `workload.class`.
2. `core workload show laptop`, then start a build (`cd agent && cargo clean -p gungnir-agent && cargo build --release`); after about 30 s `workload show` reports `build`. Open `knowledge/workload_classes.toml`: every rule has its source.
3. `stress-ng --cpu 0 --timeout 60s`, wait 40 s, `core anomalies laptop --hours 1`: a `threshold` record on `cpu.idle_ratio`, and after a baseline exists a `robust.mad` one; it closes about 15 s after the load ends.
4. `systemd-run --user --scope -p MemoryHigh=200M stress-ng --vm 1 --vm-bytes 400M --timeout 60s`: memory pressure in the group, an `impact` record when it is high enough.
5. `core baselines laptop --signal 'cpu.*'`: medians per class, power factors.
6. The comparison tables: `core evaluate laptop` and the latest `scenarios/runs/*-smd.md`.
7. `cd server && uv run pytest -q`.

## Raw SQL

```sh
dc exec db psql -U gungnir
```

```sql
SELECT detector, count(*) FROM anomalies WHERE replay_id IS NULL GROUP BY 1;
SELECT ts, value FROM facts WHERE name = 'workload.class' ORDER BY ts DESC LIMIT 20;
SELECT * FROM host_capacity;
SELECT id, variant, since, until FROM replays ORDER BY id;
SELECT hour, class, power, count(*) FROM histograms GROUP BY 1, 2, 3 ORDER BY 1 DESC LIMIT 20;
```

## When something is off

- No records although loads ran: `dc logs core | grep -i "cannot read knowledge"`. The core runs as uid 999 and reads `knowledge/` through a read-only mount, so the directory must be world-readable: `chmod 755 knowledge`. Then `dc restart core`.
- `calibrate.sh` stopped after the loads: the phases are in `scenarios/runs/<time>-calibration.jsonl`; finish by hand with `core runs import laptop - < <file>`, `core calibrate laptop <run>`, `core workload unmatched laptop`.
- Detection far behind: `SELECT now() - at FROM detection_state`; after a long stop it catches up one hour per step.

## Explanatory note

From the workspace (`DIPLOMA/`):

```sh
cd report/coursework && make -C diagrams && latexmk -pdf -shell-escape cource/cource_report.tex
```
