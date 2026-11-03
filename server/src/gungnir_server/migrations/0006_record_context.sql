-- Context values of a record: medians of the entity's context signals over
-- the record's interval, such as the average request size of a disk while
-- its operations were unusual. Detection computes them, so the reasoner
-- reads records and never raw samples (ADR 0001); causal rules match them
-- as manifestations of kind value (ADR 0040).
ALTER TABLE anomalies ADD COLUMN context jsonb;
