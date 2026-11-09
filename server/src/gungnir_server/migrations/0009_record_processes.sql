-- Who did it: the commands behind a CPU or memory record, from the process
-- snapshots during it (ADR 0057). Detection computes it, the audit reads it;
-- the inference engine never does (ADR 0001).
ALTER TABLE anomalies ADD COLUMN processes jsonb;
