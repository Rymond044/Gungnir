-- Times are stored in UTC; clients show them in the host's local time using
-- the offset its agent last reported.
ALTER TABLE hosts ADD COLUMN utc_offset_s integer;
