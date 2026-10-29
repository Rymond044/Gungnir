-- Detection output: anomaly and impact records, replays that write them
-- for evaluation, the live loop's progress and the trained forests.

-- A replay of the detection loop over stored data (ADR 0027).
CREATE TABLE replays (
    id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_id    integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    variant    text NOT NULL,
    since      timestamptz NOT NULL,
    until      timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    params     jsonb NOT NULL
);

-- One record per interval in which a detector's condition held on a
-- series (ADR 0021): opened after three samples in a row, closed after
-- three normal ones; ended_at is empty while open.
CREATE TABLE anomalies (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_id        integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    series_id      integer NOT NULL REFERENCES series ON DELETE CASCADE,
    kind           text NOT NULL CHECK (kind IN ('anomaly', 'impact')),
    detector       text NOT NULL,
    -- Impact only: noticeable or severe.
    level          text CHECK (level IN ('noticeable', 'severe')),
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz CHECK (ended_at >= started_at),
    direction      text NOT NULL CHECK (direction IN ('up', 'down')),
    peak_score     double precision NOT NULL,
    -- The sample that opened the record.
    value          double precision,
    workload_class text,
    power_profile  text,
    -- What the value was judged against: a baseline (median, scale,
    -- version), calibration (capacity, share), a threshold, or a forest.
    baseline       jsonb NOT NULL,
    -- Records of one Isolation Forest window share an episode.
    episode        bigint,
    -- Set later by the reasoner.
    expected       boolean,
    replay_id      integer REFERENCES replays ON DELETE CASCADE
);
CREATE INDEX anomalies_host_started ON anomalies (host_id, started_at);
CREATE INDEX anomalies_open ON anomalies (host_id) WHERE ended_at IS NULL AND replay_id IS NULL;
CREATE INDEX anomalies_replay ON anomalies (replay_id) WHERE replay_id IS NOT NULL;
CREATE SEQUENCE anomaly_episodes;

-- Where the live loop stopped and the state of its detectors and open
-- records, saved with every step so that a restart continues exactly.
CREATE TABLE detection_state (
    host_id integer PRIMARY KEY REFERENCES hosts ON DELETE CASCADE,
    at      timestamptz NOT NULL,
    state   jsonb NOT NULL
);

-- Isolation Forest per host and workload class, retrained daily.
CREATE TABLE forests (
    host_id    integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    class      text NOT NULL,
    trained_at timestamptz NOT NULL,
    windows    integer NOT NULL,
    threshold  double precision NOT NULL,
    model      bytea NOT NULL,
    PRIMARY KEY (host_id, class, trained_at)
);
