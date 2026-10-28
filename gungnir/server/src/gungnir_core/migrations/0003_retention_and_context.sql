-- Retention by daily partitions, minute rollups, and the context that agents
-- now send besides samples: facts, process snapshots and events.

-- Creates the missing daily partitions of a table range-partitioned on ts,
-- named <table>_YYYYMMDD, for the UTC days from first to last inclusive.
CREATE FUNCTION ensure_daily_partitions(tbl text, first_day date, last_day date) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    d date := first_day;
BEGIN
    WHILE d <= last_day LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
            tbl || '_' || to_char(d, 'YYYYMMDD'), tbl,
            (d::timestamp AT TIME ZONE 'UTC'), ((d + 1)::timestamp AT TIME ZONE 'UTC'));
        d := d + 1;
    END LOOP;
END $$;

-- Raw samples, now partitioned by day so that expiry is a cheap DROP.
ALTER TABLE samples RENAME TO samples_unpartitioned;
ALTER TABLE samples_unpartitioned RENAME CONSTRAINT samples_pkey TO samples_unpartitioned_pkey;

CREATE TABLE samples (
    series_id integer NOT NULL REFERENCES series ON DELETE CASCADE,
    ts        timestamptz NOT NULL,
    value     double precision NOT NULL,
    PRIMARY KEY (series_id, ts)
) PARTITION BY RANGE (ts);

SELECT ensure_daily_partitions(
    'samples',
    coalesce((SELECT min(ts) FROM samples_unpartitioned), now())::date,
    (now() + interval '2 days')::date);
INSERT INTO samples SELECT * FROM samples_unpartitioned;
DROP TABLE samples_unpartitioned;
-- Rollups read all series of a few minutes; samples arrive in time order,
-- so a block range index finds them cheaply.
CREATE INDEX samples_ts ON samples USING brin (ts);

-- Minute aggregates of every series, kept far longer than raw samples.
-- Sum and count rather than a mean: series sent sparsely (control groups)
-- omit zeros, so their mean needs the expected count, which the reader knows.
CREATE TABLE samples_1m (
    series_id integer NOT NULL REFERENCES series ON DELETE CASCADE,
    ts        timestamptz NOT NULL,
    min       double precision NOT NULL,
    max       double precision NOT NULL,
    sum       double precision NOT NULL,
    n         integer NOT NULL,
    PRIMARY KEY (series_id, ts)
) PARTITION BY RANGE (ts);

-- Time ranges whose raw samples outlive retention: incidents and labeled
-- runs needed for evaluation. Expiry copies them to samples_kept.
CREATE TABLE kept_ranges (
    id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_id integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    since   timestamptz NOT NULL,
    until   timestamptz NOT NULL CHECK (until > since),
    reason  text NOT NULL
);

CREATE TABLE samples_kept (
    series_id integer NOT NULL REFERENCES series ON DELETE CASCADE,
    ts        timestamptz NOT NULL,
    value     double precision NOT NULL,
    PRIMARY KEY (series_id, ts)
);

-- Progress of background maintenance, e.g. the first minute whose rollup
-- must be (re)computed. Ingest moves it back when late data arrives.
CREATE TABLE maintenance_state (
    key text PRIMARY KEY,
    at  timestamptz NOT NULL
);
INSERT INTO maintenance_state (key, at)
    SELECT 'rollup_from', date_trunc('minute', coalesce(min(ts), now())) FROM samples;

-- Text-valued facts such as the power profile, sent on change.
CREATE TABLE facts (
    host_id integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    name    text NOT NULL,
    entity  text NOT NULL,
    ts      timestamptz NOT NULL,
    value   text NOT NULL,
    PRIMARY KEY (host_id, name, entity, ts)
);

-- Snapshots of the top processes, context for diagnosis and for the
-- workload class. Same retention as raw samples.
CREATE TABLE process_samples (
    host_id   integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    ts        timestamptz NOT NULL,
    pid       integer NOT NULL,
    ppid      integer NOT NULL,
    comm      text NOT NULL,
    state     text NOT NULL,
    threads   integer NOT NULL,
    cpu_cores double precision NOT NULL,
    rss_bytes bigint NOT NULL,
    cgroup    text,
    unit      text,
    PRIMARY KEY (host_id, ts, pid)
) PARTITION BY RANGE (ts);

SELECT ensure_daily_partitions('process_samples', now()::date, (now() + interval '2 days')::date);

-- Kernel and service events. The key (journal cursor or kernel sequence)
-- makes a resent batch harmless.
CREATE TABLE events (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_id    integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    ts         timestamptz NOT NULL,
    source     text NOT NULL,
    priority   smallint NOT NULL,
    unit       text,
    message_id text,
    message    text NOT NULL,
    key        text NOT NULL,
    UNIQUE (host_id, key)
);
CREATE INDEX events_host_ts ON events (host_id, ts);

ALTER TABLE hosts RENAME COLUMN dropped_samples TO dropped_rows;
