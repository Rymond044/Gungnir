-- The agent's modules are called collectors (ADR 0050): one row per collector
-- with the source it probed.
ALTER TABLE source_states RENAME TO collector_states;
ALTER TABLE collector_states RENAME COLUMN module TO collector;
ALTER TABLE collector_states RENAME CONSTRAINT source_states_pkey TO collector_states_pkey;
