//! Thermal state of the CPU: how often the hardware throttled the clock
//! because of heat, and the package temperature.
//!
//! The preferred source is the per-CPU `thermal_throttle` counters of x86
//! (Documentation/ABI/testing/sysfs-devices-system-cpu) together with a
//! temperature zone; without the counters, the zone alone; without either,
//! the module is disabled.

use std::collections::HashSet;
use std::io;
use std::path::{Path, PathBuf};
use std::time::Instant;

use super::{Collected, Reading, Roots, Source, invalid, read_trimmed};

/// Zones that measure the CPU package, in order of preference: Intel's
/// package sensor, the ACPI CPU zone, AMD's control temperature.
const CPU_ZONES: &[&str] = &["x86_pkg_temp", "TCPU", "k10temp", "cpu-thermal", "acpitz"];

/// Finds the temperature file of the best CPU zone.
fn cpu_zone(sys: &Path) -> Option<PathBuf> {
    let mut found: Vec<(usize, PathBuf)> = std::fs::read_dir(sys.join("class/thermal"))
        .ok()?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter_map(|dir| {
            let kind = read_trimmed(&dir.join("type")).ok()?;
            let rank = CPU_ZONES.iter().position(|z| *z == kind)?;
            Some((rank, dir.join("temp")))
        })
        .collect();
    found.sort();
    found.into_iter().map(|(_, p)| p).find(|p| read_temp(p).is_ok())
}

/// A zone's temperature in degrees Celsius; the file holds millidegrees.
fn read_temp(path: &Path) -> io::Result<f64> {
    read_trimmed(path)?
        .parse::<f64>()
        .map(|m| m / 1000.0)
        .map_err(|_| invalid(format!("{}: not a number", path.display())))
}

/// The counter files to sum: every CPU's core counter, and the package
/// counter of one CPU per package (all CPUs of a package show the same one).
fn throttle_files(sys: &Path) -> io::Result<Vec<PathBuf>> {
    let cpus = sys.join("devices/system/cpu");
    let mut files = Vec::new();
    let mut packages = HashSet::new();
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(&cpus)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .and_then(|n| n.strip_prefix("cpu"))
                .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
        })
        .collect();
    dirs.sort();
    for dir in dirs {
        let counters = dir.join("thermal_throttle");
        if !counters.join("core_throttle_count").exists() {
            continue;
        }
        files.push(counters.join("core_throttle_count"));
        let package = read_trimmed(&dir.join("topology/physical_package_id")).unwrap_or_default();
        if packages.insert(package) && counters.join("package_throttle_count").exists() {
            files.push(counters.join("package_throttle_count"));
        }
    }
    if files.is_empty() {
        return Err(invalid(format!("{}: no thermal_throttle counters", cpus.display())));
    }
    Ok(files)
}

pub struct Thermal {
    counters: Vec<PathBuf>,
    zone: Option<PathBuf>,
    prev: Option<u64>,
}

impl Thermal {
    fn total(&self) -> io::Result<u64> {
        self.counters.iter().try_fold(0u64, |sum, path| {
            let n = read_trimmed(path)?
                .parse::<u64>()
                .map_err(|_| invalid(format!("{}: not a number", path.display())))?;
            Ok(sum + n)
        })
    }
}

pub fn open_throttle_counters(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source =
        Thermal { counters: throttle_files(&roots.sys)?, zone: cpu_zone(&roots.sys), prev: None };
    source.collect(now, &mut Collected::default())?;
    Ok(Box::new(source))
}

pub fn open_thermal_zone(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let zone = cpu_zone(&roots.sys).ok_or_else(|| {
        invalid(format!("{}: no CPU thermal zone", roots.sys.join("class/thermal").display()))
    })?;
    let mut source = Thermal { counters: Vec::new(), zone: Some(zone), prev: None };
    source.collect(now, &mut Collected::default())?;
    Ok(Box::new(source))
}

impl Source for Thermal {
    fn origin(&self) -> String {
        let mut parts = Vec::new();
        if let Some(first) = self.counters.first() {
            parts.push(first.parent().map(|p| p.display().to_string()).unwrap_or_default());
        }
        if let Some(zone) = &self.zone {
            parts.push(zone.display().to_string());
        }
        parts.join(", ")
    }

    fn collect(&mut self, _now: Instant, out: &mut Collected) -> io::Result<()> {
        if !self.counters.is_empty() {
            let total = self.total()?;
            // Throttling is a rare event, so the signal is a count per interval.
            if let Some(prev) = self.prev.filter(|p| total >= *p) {
                out.readings.push(Reading::host("cpu.thermal_throttles", (total - prev) as f64));
            }
            self.prev = Some(total);
        }
        if let Some(zone) = &self.zone {
            out.readings.push(Reading::host("cpu.temperature_c", read_temp(zone)?));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::{find, scratch_dir};

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    fn roots(sys: PathBuf) -> Roots {
        Roots {
            proc: PathBuf::from("/nonexistent"),
            sys,
            cgroup: PathBuf::from("/nonexistent"),
            disk_exclude: vec![],
            state: None,
        }
    }

    #[test]
    fn counts_throttles_once_per_package_and_reads_the_package_zone() {
        let sys = scratch_dir("thermal");
        for (cpu, core) in [(0, 2), (1, 5)] {
            let dir = sys.join(format!("devices/system/cpu/cpu{cpu}"));
            write(&dir.join("thermal_throttle/core_throttle_count"), &format!("{core}\n"));
            write(&dir.join("thermal_throttle/package_throttle_count"), "26\n");
            write(&dir.join("topology/physical_package_id"), "0\n");
        }
        write(&sys.join("class/thermal/thermal_zone0/type"), "acpitz\n");
        write(&sys.join("class/thermal/thermal_zone0/temp"), "40000\n");
        write(&sys.join("class/thermal/thermal_zone1/type"), "x86_pkg_temp\n");
        write(&sys.join("class/thermal/thermal_zone1/temp"), "71500\n");

        let mut source = open_throttle_counters(&roots(sys.clone()), Instant::now()).unwrap();
        write(&sys.join("devices/system/cpu/cpu1/thermal_throttle/core_throttle_count"), "6\n");
        write(&sys.join("devices/system/cpu/cpu0/thermal_throttle/package_throttle_count"), "29\n");
        let mut out = Collected::default();
        source.collect(Instant::now(), &mut out).unwrap();
        assert_eq!(find(&out.readings, "cpu.thermal_throttles", "host").unwrap().value, 4.0);
        assert_eq!(find(&out.readings, "cpu.temperature_c", "host").unwrap().value, 71.5);
        std::fs::remove_dir_all(sys).unwrap();
    }

    #[test]
    fn without_counters_the_zone_alone_is_the_fallback() {
        let sys = scratch_dir("thermal-zone");
        write(&sys.join("class/thermal/thermal_zone0/type"), "TCPU\n");
        write(&sys.join("class/thermal/thermal_zone0/temp"), "55000\n");
        assert!(open_throttle_counters(&roots(sys.clone()), Instant::now()).is_err());
        let mut source = open_thermal_zone(&roots(sys.clone()), Instant::now()).unwrap();
        let mut out = Collected::default();
        source.collect(Instant::now(), &mut out).unwrap();
        assert!(find(&out.readings, "cpu.thermal_throttles", "host").is_none());
        assert_eq!(find(&out.readings, "cpu.temperature_c", "host").unwrap().value, 55.0);
        std::fs::remove_dir_all(sys).unwrap();
    }
}
