-- The process group of each process in a snapshot. A shell gives every
-- command its own group and the command's children inherit it, so two bursts
-- of one group are one build, while a new group is a new command. NULL for
-- snapshots of agents that did not report it.
ALTER TABLE process_samples ADD COLUMN pgid integer;
