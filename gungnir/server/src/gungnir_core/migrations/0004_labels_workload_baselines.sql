-- Ground truth from labeled runs, host capacity from the calibration run,
-- the workload classifier's progress and the hourly histograms that
-- baselines are computed from.

-- One row per phase of a labeled run (calibration, overhead, fault
-- scenario). Phases marked normal may enter baselines; the rest never do.
CREATE TABLE labeled_runs (
    id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_id  integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    run      text NOT NULL,
    kind     text NOT NULL,
    phase    text NOT NULL,
    since    timestamptz NOT NULL,
    until    timestamptz NOT NULL CHECK (until > since),
    normal   boolean NOT NULL,
    -- Signals the phase should show up in; evaluation counts it found when
    -- a record of one of them overlaps it.
    expected text[] NOT NULL,
    scope    text,
    command  text,
    UNIQUE (host_id, run, phase)
);
CREATE INDEX labeled_runs_host_since ON labeled_runs (host_id, since);

-- What the host can do, measured through the agent's own signals during
-- the calibration run. Cold-start thresholds and throughput spread floors.
CREATE TABLE host_capacity (
    host_id     integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    signal      text NOT NULL,
    entity      text NOT NULL,
    value       double precision NOT NULL,
    run         text NOT NULL,
    phase       text NOT NULL,
    -- End of the run: a replay may use the capacity only after it.
    measured_at timestamptz NOT NULL,
    PRIMARY KEY (host_id, signal, entity)
);

-- Where the workload classifier stopped and the class it was about to
-- accept, so that a restart continues exactly.
CREATE TABLE workload_state (
    host_id         integer PRIMARY KEY REFERENCES hosts ON DELETE CASCADE,
    at              timestamptz NOT NULL,
    current         text,
    candidate       text,
    candidate_since timestamptz,
    candidate_n     integer NOT NULL DEFAULT 0
);

-- Log-bucket histogram of one series' five-second samples in one hour,
-- per workload class and power profile. Only non-empty buckets are kept,
-- as parallel arrays of bucket keys and counts.
CREATE TABLE histograms (
    series_id integer NOT NULL REFERENCES series ON DELETE CASCADE,
    class     text NOT NULL,
    power     text NOT NULL,
    hour      timestamptz NOT NULL,
    keys      integer[] NOT NULL,
    counts    integer[] NOT NULL,
    PRIMARY KEY (series_id, class, power, hour)
);
CREATE INDEX histograms_hour ON histograms (hour);

-- Hours are built only once the classifier has settled them; per host,
-- since the classifier runs per host.
CREATE TABLE histogram_state (
    host_id integer PRIMARY KEY REFERENCES hosts ON DELETE CASCADE,
    next_hour timestamptz NOT NULL
);
