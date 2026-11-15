//! The action types of the policy as writes to control group files
//! (ADR 0062). Each change remembers the value it replaced, to write it back.

use std::path::Path;

use serde::{Deserialize, Serialize};

const MIB: f64 = 1024.0 * 1024.0;
const PAGE: u64 = 4096;
/// The kernel's default period of `cpu.max`, used when the file has none.
const CPU_PERIOD: u64 = 100_000;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Kind {
    /// A key of `io.max` and the `io.stat` counter that shows the group used it.
    IoMax {
        key: &'static str,
        stat: &'static str,
    },
    MemoryHigh,
    CpuWeight,
    CpuMax,
}

pub fn kind(action: &str) -> Option<Kind> {
    Some(match action {
        "io_max_rbps" => Kind::IoMax { key: "rbps", stat: "rbytes" },
        "io_max_wbps" => Kind::IoMax { key: "wbps", stat: "wbytes" },
        "io_max_riops" => Kind::IoMax { key: "riops", stat: "rios" },
        "io_max_wiops" => Kind::IoMax { key: "wiops", stat: "wios" },
        "memory_high" => Kind::MemoryHigh,
        "cpu_weight" => Kind::CpuWeight,
        "cpu_max" => Kind::CpuMax,
        _ => return None,
    })
}

/// One file of one group: what it held and what the actuator wrote. For
/// `io.max` the key names the device and the limit (`259:0 wbps`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FileChange {
    pub group: String,
    pub file: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub key: Option<String>,
    pub before: String,
    pub after: String,
}

/// The writes that carry out an action on one group, from the value the
/// server computed or from a share of what the group holds now; refused when
/// the value is out of bounds or the controller is off for the group.
pub fn plan(
    kind: Kind,
    group: &str,
    dir: &Path,
    value: Option<f64>,
    share: Option<f64>,
) -> Result<Vec<FileChange>, String> {
    let change = |file: &str, key: Option<String>, before: String, after: String| FileChange {
        group: group.to_owned(),
        file: file.to_owned(),
        key,
        before,
        after,
    };
    match kind {
        Kind::IoMax { key, stat } => {
            let v = value.ok_or("an I/O limit needs a value")?;
            let least = if key.ends_with("bps") { MIB } else { 10.0 };
            if !v.is_finite() || v < least {
                return Err(format!("{key} {v} is below the least allowed {least}"));
            }
            let limits = read(dir, "io.max").map_err(|_| "the io controller is off for this group")?;
            let stat_text = read(dir, "io.stat").unwrap_or_default();
            let stats = io_lines(&stat_text);
            let mut devices: Vec<&str> =
                stats.iter().filter(|(_, kv)| field(kv, stat).unwrap_or(0) > 0).map(|(d, _)| *d).collect();
            if devices.is_empty() {
                devices = stats.iter().map(|(d, _)| *d).collect();
            }
            if devices.is_empty() {
                return Err("the group did no disk I/O, there is no disk to limit".into());
            }
            let after = format!("{}", v.round() as u64);
            Ok(devices
                .into_iter()
                .map(|dev| {
                    let before = io_limit(&limits, dev, key).unwrap_or_else(|| "max".into());
                    change("io.max", Some(format!("{dev} {key}")), before, after.clone())
                })
                .collect())
        }
        Kind::MemoryHigh => {
            let current: u64 = number(dir, "memory.current").map_err(|_| "the memory controller is off")?;
            let v = value.or_else(|| share.map(|s| s * current as f64)).ok_or("memory.high needs a value")?;
            let least = (64.0 * MIB).max(current as f64 / 2.0);
            if !v.is_finite() || v < least {
                return Err(format!("memory.high {v:.0} is below the least allowed {least:.0}"));
            }
            // Page-aligned, so that reading the file back gives the same number.
            let after = (v as u64) / PAGE * PAGE;
            Ok(vec![change("memory.high", None, read(dir, "memory.high")?, after.to_string())])
        }
        Kind::CpuWeight => {
            let current: u64 = number(dir, "cpu.weight").map_err(|_| "the cpu controller is off")?;
            let v = value.or_else(|| share.map(|s| s * current as f64)).ok_or("cpu.weight needs a value")?;
            if !v.is_finite() {
                return Err("cpu.weight needs a finite value".into());
            }
            let after = (v.round() as u64).clamp(1, 10_000);
            Ok(vec![change("cpu.weight", None, current.to_string(), after.to_string())])
        }
        Kind::CpuMax => {
            let cores = value.ok_or("cpu.max needs a value in cores")?;
            if !cores.is_finite() || cores < 0.1 {
                return Err(format!("cpu.max of {cores} cores is below the least allowed 0.1"));
            }
            let before = read(dir, "cpu.max").map_err(|_| "the cpu controller is off")?;
            let period = before.split_whitespace().nth(1).and_then(|p| p.parse().ok()).unwrap_or(CPU_PERIOD);
            let quota = (cores * period as f64).round() as u64;
            Ok(vec![change("cpu.max", None, before, format!("{quota} {period}"))])
        }
    }
}

/// Writes the new value of one change.
pub fn apply(root: &Path, c: &FileChange) -> Result<(), String> {
    write(root, c, &c.after)
}

/// Writes back the value a change replaced.
pub fn revert(root: &Path, c: &FileChange) -> Result<(), String> {
    write(root, c, &c.before)
}

/// Whether the file still holds what the actuator wrote; otherwise someone
/// else changed it since, or the group is another one of the same name.
pub fn holds(root: &Path, c: &FileChange) -> bool {
    let Ok(text) = read(&root.join(&c.group), &c.file) else { return false };
    match &c.key {
        Some(key) => {
            let (dev, name) = key.split_once(' ').unwrap_or((key, ""));
            io_limit(&text, dev, name).as_deref() == Some(c.after.as_str())
        }
        None => text == c.after,
    }
}

fn write(root: &Path, c: &FileChange, value: &str) -> Result<(), String> {
    let path = root.join(&c.group).join(&c.file);
    let line = match &c.key {
        Some(key) => {
            let (dev, name) = key.split_once(' ').unwrap_or((key, ""));
            format!("{dev} {name}={value}")
        }
        None => value.to_owned(),
    };
    std::fs::write(&path, line).map_err(|e| format!("{}: {e}", path.display()))
}

fn read(dir: &Path, file: &str) -> Result<String, String> {
    let path = dir.join(file);
    std::fs::read_to_string(&path)
        .map(|s| s.trim().to_owned())
        .map_err(|e| format!("{}: {e}", path.display()))
}

fn number(dir: &Path, file: &str) -> Result<u64, String> {
    read(dir, file)?.parse().map_err(|e| format!("{file}: {e}"))
}

/// Lines of `io.stat` or `io.max`: a device and its `key=value` fields.
fn io_lines(text: &str) -> Vec<(&str, Vec<(&str, &str)>)> {
    text.lines()
        .filter_map(|line| {
            let mut it = line.split_whitespace();
            let dev = it.next()?;
            Some((dev, it.filter_map(|kv| kv.split_once('=')).collect()))
        })
        .collect()
}

fn field(kv: &[(&str, &str)], name: &str) -> Option<u64> {
    kv.iter().find(|(k, _)| *k == name).and_then(|(_, v)| v.parse().ok())
}

fn io_limit(text: &str, dev: &str, key: &str) -> Option<String> {
    io_lines(text)
        .into_iter()
        .find(|(d, _)| *d == dev)
        .and_then(|(_, kv)| kv.iter().find(|(k, _)| *k == key).map(|(_, v)| (*v).to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn group(name: &str, files: &[(&str, &str)]) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("gungnir-actuator-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let dir = root.join("app.scope");
        std::fs::create_dir_all(&dir).unwrap();
        for (file, text) in files {
            std::fs::write(dir.join(file), text).unwrap();
        }
        root
    }

    #[test]
    fn an_io_limit_covers_the_disks_the_group_wrote_to() {
        let root = group(
            "io",
            &[
                (
                    "io.stat",
                    "259:0 rbytes=10 wbytes=900 rios=1 wios=9\n8:0 rbytes=5 wbytes=0 rios=1 wios=0\n",
                ),
                ("io.max", "8:0 rbps=max wbps=2097152 riops=max wiops=max\n"),
            ],
        );
        let kind = kind("io_max_wbps").unwrap();
        let plan = plan(kind, "app.scope", &root.join("app.scope"), Some(40e6), None).unwrap();
        assert_eq!(plan.len(), 1);
        assert_eq!(plan[0].key.as_deref(), Some("259:0 wbps"));
        assert_eq!((plan[0].before.as_str(), plan[0].after.as_str()), ("max", "40000000"));
        apply(&root, &plan[0]).unwrap();
        assert!(holds(&root, &plan[0]));
        revert(&root, &plan[0]).unwrap();
        assert!(!holds(&root, &plan[0]));
        assert!(super::plan(kind, "app.scope", &root.join("app.scope"), Some(1000.0), None).is_err());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn memory_high_is_a_share_of_what_the_group_holds_and_never_under_half() {
        let root = group("mem", &[("memory.current", "1073741824\n"), ("memory.high", "max\n")]);
        let dir = root.join("app.scope");
        let plan = plan(Kind::MemoryHigh, "app.scope", &dir, None, Some(1.1)).unwrap();
        assert_eq!(plan[0].before, "max");
        assert_eq!(plan[0].after.parse::<u64>().unwrap() % PAGE, 0);
        assert!(super::plan(Kind::MemoryHigh, "app.scope", &dir, Some(400.0 * MIB), None).is_err());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn cpu_limits_keep_the_period_and_weights_stay_in_range() {
        let root = group("cpu", &[("cpu.max", "max 100000\n"), ("cpu.weight", "100\n")]);
        let dir = root.join("app.scope");
        let max = plan(Kind::CpuMax, "app.scope", &dir, Some(1.5), None).unwrap();
        assert_eq!(max[0].after, "150000 100000");
        assert!(plan(Kind::CpuMax, "app.scope", &dir, Some(0.01), None).is_err());
        let weight = plan(Kind::CpuWeight, "app.scope", &dir, None, Some(0.25)).unwrap();
        assert_eq!((weight[0].before.as_str(), weight[0].after.as_str()), ("100", "25"));
        let least = plan(Kind::CpuWeight, "app.scope", &dir, Some(0.2), None).unwrap();
        assert_eq!(least[0].after, "1");
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_group_without_the_controller_is_refused() {
        let root = group("off", &[("io.stat", "259:0 wbytes=1\n")]);
        let err = plan(kind("io_max_wbps").unwrap(), "app.scope", &root.join("app.scope"), Some(2e6), None);
        assert!(err.unwrap_err().contains("io controller is off"));
        std::fs::remove_dir_all(&root).unwrap();
    }
}
