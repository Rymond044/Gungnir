-- Hosts, their signal sources and the samples their agents deliver.

CREATE TABLE hosts (
    id               integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             text NOT NULL UNIQUE,
    -- Only the hash is kept; the token itself is shown once, at creation.
    token_sha256     bytea NOT NULL UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz,
    agent_version    text,
    agent_started_at timestamptz,
    -- Samples the agent lost to buffer overflow since it started.
    dropped_samples  bigint NOT NULL DEFAULT 0
);

-- Probe outcome per module, as last reported by the agent.
CREATE TABLE source_states (
    host_id     integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    module      text NOT NULL,
    state       text NOT NULL CHECK (state IN ('preferred', 'fallback', 'disabled')),
    origin      text,
    reason      text,
    reported_at timestamptz NOT NULL,
    PRIMARY KEY (host_id, module)
);

-- One row per time series: a signal of an entity (the host, a disk, a
-- mount point) on one host. Samples refer to it by a small integer.
CREATE TABLE series (
    id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_id integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    signal  text NOT NULL,
    entity  text NOT NULL,
    UNIQUE (host_id, signal, entity)
);

-- The key makes a resent batch harmless: duplicates are ignored on insert.
CREATE TABLE samples (
    series_id integer NOT NULL REFERENCES series ON DELETE CASCADE,
    ts        timestamptz NOT NULL,
    value     double precision NOT NULL,
    PRIMARY KEY (series_id, ts)
);
