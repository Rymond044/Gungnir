# gungnIR

Intelligent monitoring, diagnosis and controlled remediation for Linux hosts.

| Directory | Contents |
|---|---|
| `agent/` | Rust workspace: `gungnir-agent` (signal collection, buffer, transport), `gungnir-actuator` (privileged actions, not yet built) |
| `server/` | Python package `gungnir_core`: ingest, storage, API, CLI; detection and diagnosis to come |
| `web/` | Web interface |
| `knowledge/` | Ontology, causal rules, action policy, knowledge tests |
| `scenarios/` | Fault injection scenarios with ground truth |
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

`docker compose down` keeps the database (named volume `gungnir_db`); `down -v` deletes it.

## Develop

```sh
cd agent && cargo fmt && cargo clippy --all-targets && cargo test
cd server && uv sync && uv run ruff check && uv run ruff format && uv run pytest   # needs the Compose PostgreSQL
```
