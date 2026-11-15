-- What a scenario phase should give (slice S9): the root cause and whether
-- it hurts someone, so that an incident is expected. NULL where a phase only
-- loads the host (calibration, overhead).
ALTER TABLE labeled_runs ADD COLUMN cause text;
ALTER TABLE labeled_runs ADD COLUMN harm boolean;
