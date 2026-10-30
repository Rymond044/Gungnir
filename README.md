# gungnIR

Intelligent monitoring, diagnosis and controlled remediation for Linux hosts.

| Directory | Contents |
|---|---|
| `agent/` | Rust workspace: `gungnir-agent` (signal collection, buffer, transport), `gungnir-actuator` (privileged actions, not yet built) |
| `server/` | Python package `gungnir_core`: ingest, storage, workload classes, baselines, detection, API, CLI; diagnosis to come |
| `web/` | Web interface |
| `knowledge/` | Workload class rules, control and impact thresholds (with provenance); ontology, causal rules, action policy and knowledge tests to come |
| `scenarios/` | Calibration and overhead runs, fault scenarios with ground truth; `runs/` holds label files and result tables |
| `deploy/` | Docker Compose for the core, systemd unit and installer for the agent |

Design lives in the workspace repository: `docs/architecture.md` and `docs/adr/`.

## Run

```sh
docker compose -f deploy/compose.yaml up -d --build      # core on 127.0.0.1:8080, PostgreSQL on 127.0.0.1:5432
docker compose -f deploy/compose.yaml exec core gungnir-core host add <name> > token   # shown once

(cd agent && cargo build --release)
(cd deploy/agent && sudo ./install.sh ../../token)         # systemd service gungnir-agent
```

Check: `docker compose -f deploy/compose.yaml exec core gungnir-core status <name>` shows the agent, its signal sources and the latest value of every signal in its unit; `gungnir-agent --probe` prints what the agent itself sees. API docs: `http://127.0.0.1:8080/docs`.

Detection runs inside the core. Calibrate a new host once, with the host left alone for about 17 minutes:

```sh
scenarios/calibrate.sh <name>                     # graded loads, labels, capacity, unmatched processes
docker compose -f deploy/compose.yaml exec core gungnir-core workload show <name>
docker compose -f deploy/compose.yaml exec core gungnir-core replay <name>      # compare the detector variants
docker compose -f deploy/compose.yaml exec core gungnir-core evaluate <name>
```

Knowledge files (`knowledge/`) are mounted read-only into the core; commands that write files (`workload assign`, `evaluate --out`, `smd --out`) run on the host with `cd server && uv run gungnir-core ...`.

Everyday commands (checks, rebuilds, runs, replays, the demo): `COMMANDS.md`.

`docker compose down` keeps the database (named volume `gungnir_db`); `down -v` deletes it.

## Develop

```sh
cd agent && cargo fmt && cargo clippy --all-targets && cargo test
cd server && uv sync && uv run ruff check && uv run ruff format && uv run pytest   # needs the Compose PostgreSQL
```
