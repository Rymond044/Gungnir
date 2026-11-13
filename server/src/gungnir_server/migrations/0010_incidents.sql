-- Incidents, their hypotheses and proposals (slice S6, ADR 0060). An incident
-- opens on harm (an impact record, an event, or a hypothesis that hurt the
-- others), collects every record of the host while it is open and closes
-- after a quiet period without harm. One open incident per host (ADR 0015).
CREATE TABLE incidents (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_id      integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    opened_at    timestamptz NOT NULL,
    -- The end of the latest harm; the quiet period counts from it.
    last_harm_at timestamptz NOT NULL,
    closed_at    timestamptz CHECK (closed_at >= opened_at),
    -- The workload class when it opened.
    workload_class text,
    replay_id    integer REFERENCES replays ON DELETE CASCADE
);
CREATE UNIQUE INDEX incidents_one_open ON incidents (host_id)
    WHERE closed_at IS NULL AND replay_id IS NULL;
CREATE INDEX incidents_host_opened ON incidents (host_id, opened_at);

-- Where the incident manager stopped, per host.
CREATE TABLE incident_state (
    host_id integer PRIMARY KEY REFERENCES hosts ON DELETE CASCADE,
    at      timestamptz NOT NULL
);

-- A hypothesis per cause and binding, kept up to date while the incident is
-- open and frozen when it closes: the stable id proposals and decisions
-- refer to (ADR 0007).
CREATE TABLE hypotheses (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id     bigint NOT NULL REFERENCES incidents ON DELETE CASCADE,
    cause           text NOT NULL,
    -- Empty when the role is not bound.
    culprit         text NOT NULL DEFAULT '',
    device          text NOT NULL DEFAULT '',
    filesystem      text NOT NULL DEFAULT '',
    first_seen      timestamptz NOT NULL,
    last_seen       timestamptz NOT NULL,
    confidence      double precision NOT NULL,
    peak_confidence double precision NOT NULL,
    harm            boolean NOT NULL,
    expected        boolean NOT NULL,
    -- Rank in the latest diagnosis; NULL once it is no longer given.
    rank            integer,
    -- The explanation tree of the latest diagnosis that gave it.
    tree            jsonb NOT NULL,
    UNIQUE (incident_id, cause, culprit, device, filesystem)
);

-- A policy entry applied to a hypothesis's culprit, with the parameter
-- computed when it was proposed, and the operator's decision. One action on
-- one target per incident, for the best-ranked hypothesis that calls for it.
CREATE TABLE proposals (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id   bigint NOT NULL REFERENCES incidents ON DELETE CASCADE,
    hypothesis_id bigint NOT NULL REFERENCES hypotheses ON DELETE CASCADE,
    action        text NOT NULL,
    target        text NOT NULL,
    -- signal, share, current, value; a weight has only the share.
    parameter     jsonb NOT NULL,
    effectiveness text NOT NULL,
    -- The likelihood of its most likely side effect, in the words of strength.
    risk          text,
    side_effects  jsonb NOT NULL,
    -- Every action of the actuator is undone by timer unless kept (ADR 0007).
    rollback_after interval NOT NULL,
    source        text NOT NULL,
    rank          integer NOT NULL,
    state         text NOT NULL DEFAULT 'proposed'
                  CHECK (state IN ('proposed', 'confirmed', 'rejected', 'expired')),
    created_at    timestamptz NOT NULL,
    decided_at    timestamptz,
    decided_by    text,
    UNIQUE (incident_id, target, action)
);

ALTER TABLE anomalies ADD COLUMN incident_id bigint REFERENCES incidents ON DELETE SET NULL;
CREATE INDEX anomalies_incident ON anomalies (incident_id) WHERE incident_id IS NOT NULL;
