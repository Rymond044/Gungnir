-- Actions the actuator carried out for confirmed proposals, their
-- verification and rollback (slice S8, ADRs 0061-0063), and the accounts
-- that confirm them (ADR 0064).
CREATE TABLE actions (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proposal_id   bigint NOT NULL UNIQUE REFERENCES proposals ON DELETE CASCADE,
    host_id       integer NOT NULL REFERENCES hosts ON DELETE CASCADE,
    action        text NOT NULL,
    target        text NOT NULL,
    -- What the actuator is told: a value, or a share of what the group holds
    -- now for the actions that can read it themselves.
    value         double precision,
    share         double precision,
    revert_after  interval NOT NULL,
    -- The longest window of the hypothesis's rule, at least a minute.
    verify_after  interval NOT NULL,
    state         text NOT NULL DEFAULT 'queued'
                  CHECK (state IN ('queued', 'applied', 'kept', 'refused', 'reverted')),
    outcome       text CHECK (outcome IN ('worked', 'failed', 'nothing_to_judge')),
    -- What the actuator wrote: group, file, key, before, after.
    files         jsonb,
    -- Why it was refused or rolled back.
    reason        text,
    -- A revert or keep the server repeats to the agent until it hears back.
    asked         text CHECK (asked IN ('revert', 'keep')),
    asked_reason  text,
    confirmed_by  text NOT NULL,
    confirmed_at  timestamptz NOT NULL,
    applied_at    timestamptz,
    verified_at   timestamptz,
    ended_at      timestamptz
);
CREATE INDEX actions_host ON actions (host_id, confirmed_at);
CREATE INDEX actions_live ON actions (host_id) WHERE state IN ('queued', 'applied', 'kept');

-- Everything that happened to an action, who did it and the details: the
-- audit log of ADR 0007.
CREATE TABLE action_log (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_id bigint NOT NULL REFERENCES actions ON DELETE CASCADE,
    at        timestamptz NOT NULL,
    event     text NOT NULL,
    by        text NOT NULL,
    detail    jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX action_log_action ON action_log (action_id, at);

CREATE TABLE accounts (
    id               integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             text NOT NULL UNIQUE,
    password_hash    text NOT NULL,
    -- Kept in plain only until the first change, and shown only on the
    -- server's machine; while it is set, the account must change it.
    initial_password text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    changed_at       timestamptz
);

CREATE TABLE sessions (
    -- SHA-256 of the cookie's token; the token itself is never stored.
    token_hash text PRIMARY KEY,
    account_id integer NOT NULL REFERENCES accounts ON DELETE CASCADE,
    created_at timestamptz NOT NULL,
    seen_at    timestamptz NOT NULL
);
