//! Bounded store-and-forward buffer in memory. It holds rounds of
//! collection (ticks) rather than single samples, so that the samples,
//! facts, process snapshots and events of one moment travel together.

use std::collections::VecDeque;

use crate::sources::Collected;

pub struct Tick {
    /// Milliseconds since the Unix epoch, host wall clock.
    pub ts: i64,
    pub data: Collected,
}

impl Tick {
    /// Rows this tick adds to a batch.
    pub fn rows(&self) -> usize {
        let d = &self.data;
        d.readings.len() + d.facts.len() + d.processes.len() + d.events.len()
    }
}

pub struct Buffer {
    ticks: VecDeque<Tick>,
    rows: usize,
    capacity: usize,
    dropped: u64,
}

impl Buffer {
    pub fn new(capacity: usize) -> Self {
        Self { ticks: VecDeque::new(), rows: 0, capacity, dropped: 0 }
    }

    /// Adds a tick; when full, the oldest ticks are dropped so that the most
    /// recent state of the host is what survives an outage.
    pub fn push(&mut self, tick: Tick) {
        while self.rows + tick.rows() > self.capacity
            && let Some(old) = self.ticks.pop_front()
        {
            self.rows -= old.rows();
            self.dropped += old.rows() as u64;
        }
        self.rows += tick.rows();
        self.ticks.push_back(tick);
    }

    /// The oldest ticks holding at most `max_rows` rows, but at least one
    /// tick, so a tick larger than a batch still leaves.
    pub fn head(&mut self, max_rows: usize) -> &[Tick] {
        let ticks = self.ticks.make_contiguous();
        let mut rows = 0;
        let mut n = 0;
        for tick in ticks.iter() {
            if n > 0 && rows + tick.rows() > max_rows {
                break;
            }
            rows += tick.rows();
            n += 1;
        }
        &ticks[..n]
    }

    /// Removes the `n` oldest ticks once they are delivered or spooled.
    pub fn consume(&mut self, n: usize) {
        for tick in self.ticks.drain(..n.min(self.ticks.len())) {
            self.rows -= tick.rows();
        }
    }

    pub fn rows(&self) -> usize {
        self.rows
    }

    pub fn is_empty(&self) -> bool {
        self.ticks.is_empty()
    }

    /// Rows lost since the agent started, here or in the spool.
    pub fn dropped(&self) -> u64 {
        self.dropped
    }

    pub fn add_dropped(&mut self, rows: u64) {
        self.dropped += rows;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::Reading;

    fn tick(ts: i64, rows: usize) -> Tick {
        let data =
            Collected { readings: vec![Reading::host("cpu.idle_ratio", 1.0); rows], ..Default::default() };
        Tick { ts, data }
    }

    #[test]
    fn overflow_drops_oldest_ticks() {
        let mut buffer = Buffer::new(6);
        (1..=4).for_each(|ts| buffer.push(tick(ts, 2)));
        assert_eq!(buffer.dropped(), 2);
        assert_eq!(buffer.head(100).iter().map(|t| t.ts).collect::<Vec<_>>(), vec![2, 3, 4]);
        assert_eq!(buffer.rows(), 6);
    }

    #[test]
    fn head_respects_the_batch_size_but_never_returns_nothing() {
        let mut buffer = Buffer::new(100);
        buffer.push(tick(1, 30));
        buffer.push(tick(2, 30));
        assert_eq!(buffer.head(40).len(), 1);
        assert_eq!(buffer.head(10).len(), 1);
        buffer.consume(1);
        assert_eq!(buffer.rows(), 30);
        assert_eq!(buffer.head(100)[0].ts, 2);
    }
}
