//! Paging, reclaim and OOM activity from `/proc/vmstat`, and the kernel's
//! threshold for background writeback.

use std::collections::HashMap;
use std::io;
use std::path::PathBuf;
use std::time::Instant;

use super::{Collected, Reading, Roots, Source, page_bytes, rate, read};

/// Counters this source turns into signals. Direct reclaim is the sum of the
/// per-zone `allocstall_*` counters.
#[derive(Debug, Clone, Default)]
struct Counters(HashMap<&'static str, u64>);

const KEYS: &[&str] = &["pswpin", "pswpout", "pgmajfault", "oom_kill", "nr_dirty_background_threshold"];

fn parse(text: &str) -> Counters {
    let mut counters = HashMap::new();
    let mut allocstall = None;
    for line in text.lines() {
        let Some((key, value)) = line.split_once(' ') else { continue };
        let Ok(value) = value.trim().parse::<u64>() else { continue };
        if let Some(known) = KEYS.iter().find(|k| **k == key) {
            counters.insert(*known, value);
        } else if key.starts_with("allocstall_") {
            *allocstall.get_or_insert(0) += value;
        }
    }
    if let Some(sum) = allocstall {
        counters.insert("allocstall", sum);
    }
    Counters(counters)
}

pub struct ProcVmstat {
    path: PathBuf,
    prev: Option<(Instant, Counters)>,
}

pub fn open_proc_vmstat(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = ProcVmstat { path: roots.proc.join("vmstat"), prev: None };
    source.collect(now, &mut Collected::default())?;
    Ok(Box::new(source))
}

impl Source for ProcVmstat {
    fn origin(&self) -> String {
        self.path.display().to_string()
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let cur = parse(&read(&self.path)?);
        // Dirty pages above this make the flusher threads start writing; the
        // kernel derives it from vm.dirty_background_ratio or _bytes and the
        // memory that can be dirtied (admin-guide/sysctl/vm).
        if let Some(&pages) = cur.0.get("nr_dirty_background_threshold") {
            out.readings.push(Reading::host("mem.dirty_background_bytes", (pages * page_bytes()) as f64));
        }
        if let Some((then, prev)) = &self.prev {
            let seconds = now.duration_since(*then).as_secs_f64();
            // Older kernels lack some counters; their signals are skipped.
            let pair = |key| Some((*prev.0.get(key)?, *cur.0.get(key)?));
            let per_s = [
                ("pswpin", "swap.in_pages_per_s"),
                ("pswpout", "swap.out_pages_per_s"),
                ("pgmajfault", "mem.major_faults_per_s"),
                ("allocstall", "mem.direct_reclaims_per_s"),
            ];
            for (key, signal) in per_s {
                if let Some(v) = pair(key).and_then(|(p, c)| rate(p, c, seconds)) {
                    out.readings.push(Reading::host(signal, v));
                }
            }
            // OOM kills are rare events, so the signal is a count per interval.
            if let Some((p, c)) = pair("oom_kill").filter(|(p, c)| c >= p) {
                out.readings.push(Reading::host("mem.oom_kills", (c - p) as f64));
            }
        }
        self.prev = Some((now, cur));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::fixture_roots;

    #[test]
    fn sums_allocstall_zones() {
        let c = parse("allocstall_dma 1\nallocstall_normal 48\nallocstall_movable 864\npswpin 0\n");
        assert_eq!(c.0["allocstall"], 913);
        assert_eq!(c.0["pswpin"], 0);
    }

    #[test]
    fn parses_real_vmstat() {
        let text = std::fs::read_to_string(fixture_roots().proc.join("vmstat")).unwrap();
        let c = parse(&text);
        assert!(KEYS.iter().all(|k| c.0.contains_key(k)));
    }

    #[test]
    fn reports_the_background_writeback_threshold_in_bytes() {
        let roots = fixture_roots();
        let mut source = ProcVmstat { path: roots.proc.join("vmstat"), prev: None };
        let mut out = Collected::default();
        source.collect(Instant::now(), &mut out).unwrap();
        // Fixture: nr_dirty_background_threshold 215383 pages.
        let r = out.readings.iter().find(|r| r.signal == "mem.dirty_background_bytes").unwrap();
        assert_eq!(r.value, (215383 * page_bytes()) as f64);
    }
}
