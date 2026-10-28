//! Bounded store-and-forward buffer of samples, held in memory.

use std::collections::VecDeque;

use serde::Serialize;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Sample {
    /// Milliseconds since the Unix epoch, host wall clock.
    pub ts: i64,
    pub signal: &'static str,
    pub entity: String,
    pub value: f64,
}

pub struct Buffer {
    samples: VecDeque<Sample>,
    capacity: usize,
    dropped: u64,
}

impl Buffer {
    pub fn new(capacity: usize) -> Self {
        Self { samples: VecDeque::with_capacity(capacity), capacity, dropped: 0 }
    }

    /// Adds a sample; when full, the oldest sample is dropped so that the
    /// most recent state of the host is what survives an outage.
    pub fn push(&mut self, sample: Sample) {
        if self.samples.len() == self.capacity {
            self.samples.pop_front();
            self.dropped += 1;
        }
        self.samples.push_back(sample);
    }

    /// The oldest samples, at most `n`, in order.
    pub fn head(&mut self, n: usize) -> &[Sample] {
        let n = n.min(self.samples.len());
        &self.samples.make_contiguous()[..n]
    }

    /// Removes the `n` oldest samples once the core has accepted them.
    pub fn consume(&mut self, n: usize) {
        self.samples.drain(..n.min(self.samples.len()));
    }

    pub fn len(&self) -> usize {
        self.samples.len()
    }

    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    /// Samples lost to overflow since the agent started.
    pub fn dropped(&self) -> u64 {
        self.dropped
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(ts: i64) -> Sample {
        Sample { ts, signal: "cpu.idle_ratio", entity: "host".into(), value: 1.0 }
    }

    #[test]
    fn overflow_drops_oldest() {
        let mut buffer = Buffer::new(3);
        (1..=5).for_each(|ts| buffer.push(sample(ts)));
        assert_eq!(buffer.dropped(), 2);
        assert_eq!(buffer.head(10).iter().map(|s| s.ts).collect::<Vec<_>>(), vec![3, 4, 5]);
    }

    #[test]
    fn consume_removes_only_what_was_sent() {
        let mut buffer = Buffer::new(10);
        (1..=5).for_each(|ts| buffer.push(sample(ts)));
        assert_eq!(buffer.head(2).len(), 2);
        buffer.consume(2);
        assert_eq!(buffer.head(1)[0].ts, 3);
        assert_eq!(buffer.len(), 3);
    }
}
