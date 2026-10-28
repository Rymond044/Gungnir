//! Resource use of control groups from the cgroup v2 hierarchy.
//!
//! Groups are the stable identity of work on a systemd host: a service, a
//! user session or a desktop application each get one. They are also the
//! finest level at which disk I/O can be attributed without privileges,
//! because `/proc/<pid>/io` of other users' processes is closed to us.
//!
//! Reported groups: every populated leaf, plus the top-level slices. Most
//! groups are idle most of the time, so values are sent sparsely: a rate or
//! a count only when it is not zero, memory only when it moved noticeably,
//! and everything every five minutes. Missing rates therefore mean zero, and
//! a series not refreshed by a full snapshot has ended.

use std::collections::{BTreeMap, HashMap};
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use super::{Collected, Reading, Roots, Source, pressure, read, read_trimmed};

const FULL_EVERY: Duration = Duration::from_secs(300);
const MEM: &str = "cgroup.mem_bytes";
const THROTTLED: &str = "cgroup.throttled_ratio";
/// Smallest change of memory worth a sample: 1 MiB or 1 %, whichever is larger.
const MEM_STEP_BYTES: f64 = 1024.0 * 1024.0;
const MEM_STEP_SHARE: f64 = 0.01;

#[derive(Debug, Clone, Copy, PartialEq)]
enum Kind {
    /// Counter in units per second after dividing by the scale.
    PerSecond(f64),
    /// Counter reported as the number of increments in the interval.
    PerInterval,
    /// Stall total in microseconds, reported as a share of the interval.
    Stall,
}

/// Counters read from each group, keyed by `file.field`.
const COUNTERS: &[(&str, &str, Kind)] = &[
    ("cpu.usage_usec", "cgroup.cpu_cores", Kind::PerSecond(1e6)),
    ("io.rbytes", "cgroup.read_bytes_per_s", Kind::PerSecond(1.0)),
    ("io.wbytes", "cgroup.write_bytes_per_s", Kind::PerSecond(1.0)),
    ("memory.high", "cgroup.mem_high_events", Kind::PerInterval),
    ("memory.max", "cgroup.mem_max_events", Kind::PerInterval),
    ("memory.oom_kill", "cgroup.oom_kills", Kind::PerInterval),
    ("cpu.pressure.some", "cgroup.cpu_pressure_some_ratio", Kind::Stall),
    ("memory.pressure.some", "cgroup.mem_pressure_some_ratio", Kind::Stall),
    ("memory.pressure.full", "cgroup.mem_pressure_full_ratio", Kind::Stall),
    ("io.pressure.some", "cgroup.io_pressure_some_ratio", Kind::Stall),
    ("io.pressure.full", "cgroup.io_pressure_full_ratio", Kind::Stall),
];

/// Shares and ratios of groups that collapse into one entity take the
/// largest value; amounts add up.
fn takes_max(signal: &str) -> bool {
    signal == THROTTLED
        || signal.ends_with("_pressure_some_ratio")
        || signal.ends_with("_pressure_full_ratio")
}

#[derive(Debug, Default, Clone)]
struct Snapshot {
    counters: HashMap<&'static str, u64>,
    mem_bytes: Option<u64>,
}

/// The entity name of a group: its path below the hierarchy root, with the
/// instance number dropped from scope names (`app-firefox-4242.scope`
/// becomes `app-firefox.scope`), so a restarted application continues the
/// same series instead of starting a new one.
pub(super) fn entity(rel: &str) -> String {
    rel.split('/').map(strip_instance).collect::<Vec<_>>().join("/")
}

fn strip_instance(name: &str) -> String {
    if let Some(stem) = name.strip_suffix(".scope")
        && let Some((base, number)) = stem.rsplit_once('-')
        && !base.is_empty()
        && !number.is_empty()
        && number.bytes().all(|b| b.is_ascii_digit())
    {
        return format!("{base}.scope");
    }
    name.to_owned()
}

pub struct Cgroup2 {
    root: PathBuf,
    prev: HashMap<String, (Instant, Snapshot)>,
    sent_mem: HashMap<String, f64>,
    last_full: Option<Instant>,
}

pub fn open_cgroup2(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let root = roots.cgroup.clone();
    // The unified hierarchy has this file at its root; cgroup v1 does not.
    read(&root.join("cgroup.controllers"))?;
    let mut source = Cgroup2 { root, prev: HashMap::new(), sent_mem: HashMap::new(), last_full: None };
    source.collect(now, &mut Collected::default())?;
    // The probe read primes the counters but is not sent, so the first real
    // round must again be a full snapshot.
    source.last_full = None;
    source.sent_mem.clear();
    Ok(Box::new(source))
}

impl Cgroup2 {
    /// Relative paths of the groups to report.
    fn groups(&self) -> io::Result<Vec<String>> {
        let mut found = Vec::new();
        for entry in std::fs::read_dir(&self.root)? {
            let entry = entry?;
            if entry.file_type()?.is_dir() {
                let name = entry.file_name().to_string_lossy().into_owned();
                found.push(name.clone());
                walk(&entry.path(), &name, &mut found);
            }
        }
        found.sort();
        found.dedup();
        Ok(found)
    }

    fn snapshot(&self, rel: &str) -> Snapshot {
        let dir = self.root.join(rel);
        let mut snap = Snapshot::default();
        let mut put = |key: &'static str, value: Option<u64>| {
            if let Some(v) = value {
                snap.counters.insert(key, v);
            }
        };
        // Controllers may be disabled for a subtree; their files are then
        // absent and the signals are simply not reported for that group.
        if let Ok(text) = read(&dir.join("cpu.stat")) {
            put("cpu.usage_usec", field(&text, "usage_usec"));
            put("cpu.nr_periods", field(&text, "nr_periods"));
            put("cpu.nr_throttled", field(&text, "nr_throttled"));
        }
        if let Ok(text) = read(&dir.join("memory.events")) {
            put("memory.high", field(&text, "high"));
            put("memory.max", field(&text, "max"));
            put("memory.oom_kill", field(&text, "oom_kill"));
        }
        if let Ok(text) = read(&dir.join("io.stat")) {
            let (r, w) = io_bytes(&text);
            put("io.rbytes", Some(r));
            put("io.wbytes", Some(w));
        }
        for (file, some, full) in [
            ("cpu.pressure", "cpu.pressure.some", None),
            ("memory.pressure", "memory.pressure.some", Some("memory.pressure.full")),
            ("io.pressure", "io.pressure.some", Some("io.pressure.full")),
        ] {
            let path = dir.join(file);
            if let Ok(totals) = read(&path).and_then(|text| pressure::parse(&path, &text)) {
                put(some, Some(totals.some));
                if let Some(full) = full {
                    put(full, totals.full);
                }
            }
        }
        snap.mem_bytes = read_trimmed(&dir.join("memory.current")).ok().and_then(|v| v.parse().ok());
        snap
    }
}

/// Adds the populated leaves below `dir`. In cgroup v2 processes live only
/// in leaves, so a populated leaf is a group that holds processes.
fn walk(dir: &Path, rel: &str, found: &mut Vec<String>) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let mut leaf = true;
    for entry in entries.flatten() {
        if entry.file_type().is_ok_and(|t| t.is_dir()) {
            leaf = false;
            let child = format!("{rel}/{}", entry.file_name().to_string_lossy());
            walk(&entry.path(), &child, found);
        }
    }
    if leaf && read(&dir.join("cgroup.events")).is_ok_and(|text| field(&text, "populated") == Some(1)) {
        found.push(rel.to_owned());
    }
}

/// A `key value` line of a flat-keyed cgroup file.
fn field(text: &str, key: &str) -> Option<u64> {
    text.lines().find_map(|line| {
        let (k, v) = line.split_once(' ')?;
        (k == key).then(|| v.trim().parse().ok()).flatten()
    })
}

/// Bytes read and written over all devices of an `io.stat` file.
fn io_bytes(text: &str) -> (u64, u64) {
    let (mut r, mut w) = (0, 0);
    for pair in text.split_ascii_whitespace() {
        if let Some((key, value)) = pair.split_once('=') {
            let value: u64 = value.parse().unwrap_or(0);
            match key {
                "rbytes" => r += value,
                "wbytes" => w += value,
                _ => {}
            }
        }
    }
    (r, w)
}

/// Signal values of one group between two snapshots.
fn values(prev: &Snapshot, cur: &Snapshot, seconds: f64) -> Vec<(&'static str, f64)> {
    let mut out = Vec::new();
    if seconds <= 0.0 {
        return out;
    }
    let delta = |key: &str| {
        let (p, c) = (prev.counters.get(key)?, cur.counters.get(key)?);
        (c >= p).then(|| c - p)
    };
    for &(key, signal, kind) in COUNTERS {
        let Some(d) = delta(key) else { continue };
        let value = match kind {
            Kind::PerSecond(scale) => d as f64 / scale / seconds,
            Kind::PerInterval => d as f64,
            Kind::Stall => (d as f64 / (seconds * 1e6)).min(1.0),
        };
        out.push((signal, value));
    }
    // Throttling is measured per enforcement period, which only runs when
    // the group has a CPU limit.
    if let (Some(periods), Some(throttled)) = (delta("cpu.nr_periods"), delta("cpu.nr_throttled"))
        && periods > 0
    {
        out.push((THROTTLED, throttled as f64 / periods as f64));
    }
    out
}

impl Source for Cgroup2 {
    fn origin(&self) -> String {
        self.root.display().to_string()
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let groups = self.groups()?;
        let mut merged: BTreeMap<(&'static str, String), f64> = BTreeMap::new();
        let mut merge = |signal: &'static str, entity: &str, value: f64| {
            let slot = merged.entry((signal, entity.to_owned())).or_insert(if takes_max(signal) {
                f64::MIN
            } else {
                0.0
            });
            *slot = if takes_max(signal) { slot.max(value) } else { *slot + value };
        };

        let mut current = HashMap::with_capacity(groups.len());
        for rel in groups {
            let snap = self.snapshot(&rel);
            let name = entity(&rel);
            if let Some(bytes) = snap.mem_bytes {
                merge(MEM, &name, bytes as f64);
            }
            if let Some((then, prev)) = self.prev.get(&rel) {
                for (signal, value) in values(prev, &snap, now.duration_since(*then).as_secs_f64()) {
                    merge(signal, &name, value);
                }
            }
            current.insert(rel, (now, snap));
        }
        self.prev = current;

        let full = self.last_full.is_none_or(|t| now.duration_since(t) >= FULL_EVERY);
        if full {
            self.last_full = Some(now);
            self.sent_mem.retain(|entity, _| merged.contains_key(&(MEM, entity.clone())));
        }
        for ((signal, entity), value) in merged {
            let send = if signal == MEM {
                let moved = self
                    .sent_mem
                    .get(&entity)
                    .is_none_or(|last| (value - last).abs() >= MEM_STEP_BYTES.max(MEM_STEP_SHARE * last));
                if full || moved {
                    self.sent_mem.insert(entity.clone(), value);
                }
                full || moved
            } else {
                full || value != 0.0
            };
            if send {
                out.readings.push(Reading { signal, entity, value });
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::{find, scratch_dir};

    fn group(root: &Path, rel: &str, usage: u64, wbytes: u64, mem: u64) {
        let dir = root.join(rel);
        std::fs::create_dir_all(&dir).unwrap();
        let w = |file: &str, text: String| std::fs::write(dir.join(file), text).unwrap();
        w("cgroup.events", "populated 1\nfrozen 0\n".into());
        w("cpu.stat", format!("usage_usec {usage}\nuser_usec 0\nsystem_usec 0\n"));
        w("io.stat", format!("259:0 rbytes=0 wbytes={wbytes} rios=0 wios=0 dbytes=0 dios=0\n"));
        w("memory.current", format!("{mem}\n"));
        w("memory.events", "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n".into());
    }

    fn source(root: &Path) -> Cgroup2 {
        Cgroup2 { root: root.to_owned(), prev: HashMap::new(), sent_mem: HashMap::new(), last_full: None }
    }

    #[test]
    fn scope_instance_numbers_are_dropped() {
        assert_eq!(
            entity("user.slice/app.slice/app-firefox-4242.scope"),
            "user.slice/app.slice/app-firefox.scope"
        );
        assert_eq!(entity("system.slice/session-3.scope"), "system.slice/session.scope");
        assert_eq!(entity("user.slice/user-1000.slice"), "user.slice/user-1000.slice");
        assert_eq!(entity("system.slice/docker-7d4c40.scope"), "system.slice/docker-7d4c40.scope");
        assert_eq!(entity("system.slice/nginx.service"), "system.slice/nginx.service");
    }

    #[test]
    fn instances_of_one_application_add_up() {
        let root = scratch_dir("cgroup-merge");
        group(&root, "user.slice/app-build-1.scope", 0, 0, 1 << 20);
        group(&root, "user.slice/app-build-2.scope", 0, 0, 1 << 20);
        let mut src = source(&root);
        let t0 = Instant::now();
        src.collect(t0, &mut Collected::default()).unwrap();

        group(&root, "user.slice/app-build-1.scope", 2_000_000, 0, 1 << 20);
        group(&root, "user.slice/app-build-2.scope", 3_000_000, 0, 1 << 20);
        let mut out = Collected::default();
        src.collect(t0 + Duration::from_secs(5), &mut out).unwrap();
        let cores = find(&out.readings, "cgroup.cpu_cores", "user.slice/app-build.scope").unwrap().value;
        assert_eq!(cores, 1.0);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn idle_groups_are_sent_sparsely() {
        let root = scratch_dir("cgroup-sparse");
        group(&root, "system.slice/idle.service", 100, 0, 50 << 20);
        group(&root, "system.slice/writer.service", 100, 0, 50 << 20);
        let mut src = source(&root);
        let t0 = Instant::now();
        let mut first = Collected::default();
        src.collect(t0, &mut first).unwrap();
        // The first round is a full snapshot: memory of every group.
        assert!(find(&first.readings, MEM, "system.slice/idle.service").is_some());

        group(&root, "system.slice/writer.service", 100, 10_000_000, 50 << 20);
        let mut out = Collected::default();
        src.collect(t0 + Duration::from_secs(5), &mut out).unwrap();
        let rate = find(&out.readings, "cgroup.write_bytes_per_s", "system.slice/writer.service").unwrap();
        assert_eq!(rate.value, 2_000_000.0);
        assert!(find(&out.readings, "cgroup.cpu_cores", "system.slice/idle.service").is_none());
        assert!(find(&out.readings, MEM, "system.slice/idle.service").is_none());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn top_level_slices_are_reported_with_their_leaves() {
        let root = scratch_dir("cgroup-walk");
        group(&root, "system.slice/a.service", 0, 0, 0);
        std::fs::write(root.join("system.slice/cgroup.events"), "populated 1\n").unwrap();
        group(&root, "system.slice/b.service", 0, 0, 0);
        std::fs::write(root.join("system.slice/b.service/cgroup.events"), "populated 0\n").unwrap();
        let groups = source(&root).groups().unwrap();
        assert_eq!(groups, vec!["system.slice", "system.slice/a.service"]);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn throttling_needs_enforcement_periods() {
        let mut prev = Snapshot::default();
        prev.counters.insert("cpu.nr_periods", 100);
        prev.counters.insert("cpu.nr_throttled", 10);
        let mut cur = prev.clone();
        cur.counters.insert("cpu.nr_periods", 150);
        cur.counters.insert("cpu.nr_throttled", 35);
        let values = values(&prev, &cur, 5.0);
        assert!(values.contains(&(THROTTLED, 0.5)));
    }
}
