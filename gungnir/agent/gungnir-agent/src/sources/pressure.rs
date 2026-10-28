//! Pressure stall information (PSI) of the whole host from `/proc/pressure`.
//!
//! The kernel keeps a running total of stalled time in microseconds next to
//! its smoothed averages. The difference of two totals divided by the
//! elapsed time is the exact share of our own interval spent stalled, while
//! `avg10` would smooth a short stall over several intervals.

use std::io;
use std::path::{Path, PathBuf};
use std::time::Instant;

use super::{Collected, Reading, Roots, Source, invalid, read};

/// Stall totals in microseconds. `full` is absent for CPU on older kernels
/// and meaningless for CPU at the host level, so it is optional.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub(super) struct Totals {
    pub some: u64,
    pub full: Option<u64>,
}

pub(super) fn parse(path: &Path, text: &str) -> io::Result<Totals> {
    let mut some = None;
    let mut full = None;
    for line in text.lines() {
        let mut fields = line.split_ascii_whitespace();
        let kind = fields.next();
        let total = fields.find_map(|f| f.strip_prefix("total=")).and_then(|v| v.parse::<u64>().ok());
        match kind {
            Some("some") => some = total,
            Some("full") => full = total,
            _ => {}
        }
    }
    let some = some.ok_or_else(|| invalid(format!("{}: no 'some' total", path.display())))?;
    Ok(Totals { some, full })
}

/// Share of `seconds` spent stalled, from two totals in microseconds.
/// Timer jitter can push the share a little above one, so it is clamped.
pub(super) fn share(prev: u64, cur: u64, seconds: f64) -> Option<f64> {
    (cur >= prev && seconds > 0.0).then(|| ((cur - prev) as f64 / (seconds * 1e6)).min(1.0))
}

/// Resources with their signal names for the `some` and `full` shares.
const RESOURCES: [(&str, &str, Option<&str>); 3] = [
    ("cpu", "cpu.pressure_some_ratio", None),
    ("memory", "mem.pressure_some_ratio", Some("mem.pressure_full_ratio")),
    ("io", "io.pressure_some_ratio", Some("io.pressure_full_ratio")),
];

pub struct ProcPressure {
    dir: PathBuf,
    prev: Option<(Instant, [Totals; 3])>,
}

pub fn open_proc_pressure(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = ProcPressure { dir: roots.proc.join("pressure"), prev: None };
    source.collect(now, &mut Collected::default())?;
    Ok(Box::new(source))
}

impl ProcPressure {
    fn read_all(&self) -> io::Result<[Totals; 3]> {
        let mut totals = [Totals::default(); 3];
        for (slot, (file, _, _)) in totals.iter_mut().zip(RESOURCES) {
            let path = self.dir.join(file);
            *slot = parse(&path, &read(&path)?)?;
        }
        Ok(totals)
    }
}

impl Source for ProcPressure {
    fn origin(&self) -> String {
        self.dir.display().to_string()
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let cur = self.read_all()?;
        if let Some((then, prev)) = self.prev {
            let seconds = now.duration_since(then).as_secs_f64();
            for ((p, c), (_, some_signal, full_signal)) in prev.iter().zip(&cur).zip(RESOURCES) {
                if let Some(v) = share(p.some, c.some, seconds) {
                    out.readings.push(Reading::host(some_signal, v));
                }
                if let (Some(signal), Some(pf), Some(cf)) = (full_signal, p.full, c.full)
                    && let Some(v) = share(pf, cf, seconds)
                {
                    out.readings.push(Reading::host(signal, v));
                }
            }
        }
        self.prev = Some((now, cur));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::sources::{find, scratch_dir};

    fn write(dir: &Path, some: u64, full: u64) {
        for file in ["cpu", "memory", "io"] {
            std::fs::write(
                dir.join(file),
                format!(
                    "some avg10=0.00 avg60=0.00 avg300=0.00 total={some}\n\
                     full avg10=0.00 avg60=0.00 avg300=0.00 total={full}\n"
                ),
            )
            .unwrap();
        }
    }

    #[test]
    fn stalled_share_comes_from_totals() {
        let dir = scratch_dir("psi");
        write(&dir, 1_000_000, 500_000);
        let t0 = Instant::now();
        let mut source = ProcPressure { dir: dir.clone(), prev: None };
        let mut out = Collected::default();
        source.collect(t0, &mut out).unwrap();
        assert!(out.readings.is_empty());

        // 1 s of some-stall and 0.5 s of full-stall over 5 s.
        write(&dir, 2_000_000, 1_000_000);
        source.collect(t0 + Duration::from_secs(5), &mut out).unwrap();
        assert_eq!(find(&out.readings, "io.pressure_some_ratio", "host").unwrap().value, 0.2);
        assert_eq!(find(&out.readings, "mem.pressure_full_ratio", "host").unwrap().value, 0.1);
        assert!(find(&out.readings, "cpu.pressure_full_ratio", "host").is_none());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn share_is_clamped_and_ignores_resets() {
        assert_eq!(share(0, 6_000_000, 5.0), Some(1.0));
        assert_eq!(share(10, 5, 5.0), None);
    }
}
