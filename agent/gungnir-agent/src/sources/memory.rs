//! Memory and swap occupancy from `/proc/meminfo`, or a reduced set from
//! `sysinfo(2)` when procfs is unavailable. Whether swap is configured is a
//! fact: a rule about swapping applies only where there is swap.

use std::collections::HashMap;
use std::io;
use std::path::PathBuf;
use std::time::Instant;

use super::{Collected, FactLog, Reading, Roots, Source, invalid, read};

/// Values of `/proc/meminfo` in bytes, keyed by field name.
fn parse(text: &str) -> HashMap<&str, u64> {
    let mut fields = HashMap::new();
    for line in text.lines() {
        let Some((key, rest)) = line.split_once(':') else { continue };
        let mut parts = rest.split_ascii_whitespace();
        let Some(Ok(value)) = parts.next().map(str::parse::<u64>) else { continue };
        let bytes = match parts.next() {
            Some("kB") => value * 1024,
            None => value,
            Some(_) => continue,
        };
        fields.insert(key, bytes);
    }
    fields
}

pub struct Meminfo {
    path: PathBuf,
    facts: FactLog,
}

pub fn open_meminfo(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = Meminfo { path: roots.proc.join("meminfo"), facts: FactLog::default() };
    source.collect(now, &mut Collected::default())?;
    source.facts.clear();
    Ok(Box::new(source))
}

impl Source for Meminfo {
    fn origin(&self) -> String {
        self.path.display().to_string()
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let text = read(&self.path)?;
        let m = parse(&text);
        let get = |key: &str| {
            m.get(key).copied().ok_or_else(|| invalid(format!("{}: no '{key}'", self.path.display())))
        };

        let total = get("MemTotal")?;
        let available = get("MemAvailable")?;
        out.readings.push(Reading::host("mem.available_bytes", available as f64));
        out.readings.push(Reading::host("mem.free_bytes", get("MemFree")? as f64));
        if total > 0 {
            out.readings.push(Reading::host("mem.used_ratio", 1.0 - available as f64 / total as f64));
        }
        out.readings.push(Reading::host("mem.cached_bytes", get("Cached")? as f64));
        out.readings.push(Reading::host("mem.anon_bytes", get("AnonPages")? as f64));
        out.readings.push(Reading::host("mem.dirty_bytes", get("Dirty")? as f64));
        out.readings.push(Reading::host("mem.writeback_bytes", get("Writeback")? as f64));
        // Older kernels lack these; they only add evidence, so their absence is no error.
        // Shmem holds tmpfs and shared memory, which no reclaim can free without swap.
        if let Some(&v) = m.get("Shmem") {
            out.readings.push(Reading::host("mem.shmem_bytes", v as f64));
        }
        // Kernel slab memory that cannot be reclaimed: where a kernel leak shows.
        if let Some(&v) = m.get("SUnreclaim") {
            out.readings.push(Reading::host("mem.slab_unreclaimable_bytes", v as f64));
        }
        push_swap(get("SwapTotal")?, get("SwapFree")?, out);
        push_swap_fact(&mut self.facts, now, get("SwapTotal")?, out);
        Ok(())
    }
}

/// Swap usage; the ratio is omitted on hosts without swap.
fn push_swap(total: u64, free: u64, out: &mut Collected) {
    let used = total.saturating_sub(free);
    out.readings.push(Reading::host("swap.used_bytes", used as f64));
    if total > 0 {
        out.readings.push(Reading::host("swap.used_ratio", used as f64 / total as f64));
    }
}

fn push_swap_fact(facts: &mut FactLog, now: Instant, total: u64, out: &mut Collected) {
    facts.push(now, "swap.enabled", "host", if total > 0 { "yes" } else { "no" }.to_owned(), out);
}

/// `sysinfo(2)` has no notion of available memory or page cache, so this
/// fallback reports only what it measures exactly.
#[derive(Default)]
pub struct Sysinfo {
    facts: FactLog,
}

pub fn open_sysinfo(_roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = Sysinfo::default();
    source.collect(now, &mut Collected::default())?;
    source.facts.clear();
    Ok(Box::new(source))
}

impl Source for Sysinfo {
    fn origin(&self) -> String {
        "sysinfo(2)".to_owned()
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let info = rustix::system::sysinfo();
        let unit = u64::from(info.mem_unit.max(1));
        out.readings.push(Reading::host("mem.free_bytes", (info.freeram as u64 * unit) as f64));
        push_swap(info.totalswap as u64 * unit, info.freeswap as u64 * unit, out);
        push_swap_fact(&mut self.facts, now, info.totalswap as u64 * unit, out);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::{find, fixture_roots};

    #[test]
    fn reads_real_meminfo_in_bytes() {
        let mut source = Meminfo { path: fixture_roots().proc.join("meminfo"), facts: FactLog::default() };
        let mut out = Collected::default();
        source.collect(Instant::now(), &mut out).unwrap();
        // Fixture: MemAvailable 8914408 kB, MemTotal 15860504 kB.
        assert_eq!(find(&out.readings, "mem.available_bytes", "host").unwrap().value, 8914408.0 * 1024.0);
        let used = find(&out.readings, "mem.used_ratio", "host").unwrap().value;
        assert!((used - (1.0 - 8914408.0 / 15860504.0)).abs() < 1e-12);
        assert_eq!(out.facts[0].name, "swap.enabled");
        assert_eq!(find(&out.readings, "mem.shmem_bytes", "host").unwrap().value, 228440.0 * 1024.0);
        assert_eq!(
            find(&out.readings, "mem.slab_unreclaimable_bytes", "host").unwrap().value,
            338236.0 * 1024.0
        );
    }

    #[test]
    fn sysinfo_reports_free_memory() {
        let mut out = Collected::default();
        Sysinfo::default().collect(Instant::now(), &mut out).unwrap();
        assert!(find(&out.readings, "mem.free_bytes", "host").unwrap().value > 0.0);
    }
}
