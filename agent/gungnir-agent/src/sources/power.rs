//! Power state of the host: mains or battery, and the energy profile.
//!
//! Both modify the baseline of other signals rather than being watched for
//! anomalies themselves: a laptop on battery in an eco profile is slower,
//! and that is not a fault.

use std::io;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use super::{Collected, Fact, Reading, Roots, Source, invalid, read_trimmed};

/// A fact is repeated this often even when unchanged, so the core learns it
/// after its own restart.
const FACT_EVERY: Duration = Duration::from_secs(300);

pub struct PowerSupply {
    dir: PathBuf,
}

pub fn open_power_supply(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = PowerSupply { dir: roots.sys.join("class/power_supply") };
    let mut out = Collected::default();
    source.collect(now, &mut out)?;
    if out.readings.is_empty() {
        return Err(invalid(format!("{}: no mains or battery supply", source.dir.display())));
    }
    Ok(Box::new(source))
}

#[derive(Debug, Default, PartialEq)]
struct Supplies {
    external_online: bool,
    battery_present: bool,
    discharging: bool,
    capacity_percent: Option<f64>,
}

impl PowerSupply {
    fn read(&self) -> io::Result<Supplies> {
        let mut s = Supplies::default();
        for entry in std::fs::read_dir(&self.dir)? {
            let dir = entry?.path();
            let get = |file: &str| read_trimmed(&dir.join(file)).ok();
            match get("type").as_deref() {
                Some("Mains" | "USB") => s.external_online |= get("online").as_deref() == Some("1"),
                Some("Battery") if get("present").as_deref() != Some("0") && get("status").is_some() => {
                    s.battery_present = true;
                    s.discharging |= get("status").as_deref() == Some("Discharging");
                    if let Some(c) = get("capacity").and_then(|v| v.parse::<f64>().ok()) {
                        s.capacity_percent.get_or_insert(c);
                    }
                }
                _ => {}
            }
        }
        Ok(s)
    }
}

/// Whether the host runs from an external supply. A discharging battery is
/// the most direct evidence, because USB-C ports report `online` loosely.
fn on_external(s: &Supplies) -> Option<bool> {
    if s.discharging {
        Some(false)
    } else if s.external_online || s.battery_present {
        Some(true)
    } else {
        None
    }
}

impl Source for PowerSupply {
    fn origin(&self) -> String {
        self.dir.display().to_string()
    }

    fn collect(&mut self, _now: Instant, out: &mut Collected) -> io::Result<()> {
        let s = self.read()?;
        if let Some(external) = on_external(&s) {
            out.readings.push(Reading::host("power.on_ac", if external { 1.0 } else { 0.0 }));
        }
        if let Some(c) = s.capacity_percent {
            out.readings.push(Reading::host("power.battery_ratio", c / 100.0));
        }
        Ok(())
    }
}

/// Maps the kernel's profile names to the three levels the baseline knows.
/// Unknown names pass through unchanged.
fn level(raw: &str) -> String {
    match raw {
        "low-power" | "cool" | "quiet" | "power" | "balance_power" => "eco",
        "balanced" | "balance_performance" | "default" => "balanced",
        "balanced-performance" | "performance" => "performance",
        other => other,
    }
    .to_owned()
}

pub struct Profile {
    path: PathBuf,
    last: Option<(Instant, String)>,
}

/// ACPI platform profile, set by firmware-aware tools such as
/// power-profiles-daemon on laptops that support it.
pub fn open_platform_profile(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    open_profile(roots.sys.join("firmware/acpi/platform_profile"), now)
}

/// Energy-performance preference of the CPU frequency driver; the same
/// daemons set it when there is no platform profile.
pub fn open_cpufreq_epp(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    open_profile(roots.sys.join("devices/system/cpu/cpufreq/policy0/energy_performance_preference"), now)
}

fn open_profile(path: PathBuf, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = Profile { path, last: None };
    source.collect(now, &mut Collected::default())?;
    // The probe read must not count as sent.
    source.last = None;
    Ok(Box::new(source))
}

impl Source for Profile {
    fn origin(&self) -> String {
        self.path.display().to_string()
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let value = level(&read_trimmed(&self.path)?);
        let due = match &self.last {
            Some((at, last)) => *last != value || now.duration_since(*at) >= FACT_EVERY,
            None => true,
        };
        if due {
            out.facts.push(Fact { name: "power.profile", entity: "host".into(), value: value.clone() });
            self.last = Some((now, value));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::scratch_dir;

    fn supply(root: &std::path::Path, name: &str, files: &[(&str, &str)]) {
        let dir = root.join(name);
        std::fs::create_dir_all(&dir).unwrap();
        for (file, value) in files {
            std::fs::write(dir.join(file), format!("{value}\n")).unwrap();
        }
    }

    #[test]
    fn discharging_battery_means_battery_power() {
        let root = scratch_dir("power");
        supply(&root, "ADP1", &[("type", "Mains"), ("online", "0")]);
        supply(&root, "BAT1", &[("type", "Battery"), ("status", "Discharging"), ("capacity", "57")]);
        let mut out = Collected::default();
        PowerSupply { dir: root.clone() }.collect(Instant::now(), &mut out).unwrap();
        assert_eq!(out.readings[0], Reading::host("power.on_ac", 0.0));
        assert_eq!(out.readings[1], Reading::host("power.battery_ratio", 0.57));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn profile_is_sent_on_change_only() {
        let dir = scratch_dir("profile");
        let path = dir.join("energy_performance_preference");
        std::fs::write(&path, "balance_power\n").unwrap();
        let t0 = Instant::now();
        let mut source = Profile { path: path.clone(), last: None };
        let mut out = Collected::default();
        source.collect(t0, &mut out).unwrap();
        source.collect(t0 + Duration::from_secs(5), &mut out).unwrap();
        std::fs::write(&path, "performance\n").unwrap();
        source.collect(t0 + Duration::from_secs(10), &mut out).unwrap();
        let values: Vec<_> = out.facts.iter().map(|f| f.value.as_str()).collect();
        assert_eq!(values, ["eco", "performance"]);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
