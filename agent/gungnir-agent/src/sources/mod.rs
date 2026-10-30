//! Signal sources and their probing.
//!
//! Each module (cpu, memory, ...) lists its candidate sources in order of
//! preference. At start the agent opens the first candidate that works; the
//! rest are fallbacks. A module with no working candidate is disabled and
//! reported, and the agent carries on without it.

mod cgroup;
mod cpu;
mod disk;
mod fs;
mod journal;
mod memory;
mod power;
mod pressure;
mod processes;
mod selfstat;
mod vmstat;

pub use journal::Event;
pub use processes::Process;

use std::collections::HashMap;
use std::io;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde::Serialize;

/// One value of one signal, before it gets a timestamp.
#[derive(Debug, Clone, PartialEq)]
pub struct Reading {
    pub signal: &'static str,
    pub entity: String,
    pub value: f64,
}

impl Reading {
    pub fn host(signal: &'static str, value: f64) -> Self {
        Self { signal, entity: "host".to_owned(), value }
    }
}

/// A text-valued fact about the host, such as its power profile. Facts are
/// context for diagnosis, not series to detect anomalies in.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Fact {
    pub name: &'static str,
    pub entity: String,
    pub value: String,
}

/// A fact is repeated this often even when unchanged, so the core learns it
/// after its own restart.
const FACT_EVERY: Duration = Duration::from_secs(300);

/// The facts a source has sent: a fact goes out when it changes and every
/// five minutes otherwise.
#[derive(Debug, Default)]
pub(crate) struct FactLog {
    sent: HashMap<(&'static str, String), (Instant, String)>,
}

impl FactLog {
    pub(crate) fn push(
        &mut self,
        now: Instant,
        name: &'static str,
        entity: &str,
        value: String,
        out: &mut Collected,
    ) {
        let key = (name, entity.to_owned());
        let due = match self.sent.get(&key) {
            Some((at, last)) => *last != value || now.duration_since(*at) >= FACT_EVERY,
            None => true,
        };
        if due {
            out.facts.push(Fact { name, entity: key.1.clone(), value: value.clone() });
            self.sent.insert(key, (now, value));
        }
    }

    /// Forgets what was sent, so the probe read of a source does not count.
    pub(crate) fn clear(&mut self) {
        self.sent.clear();
    }
}

/// What one round of collection yields.
#[derive(Debug, Default)]
pub struct Collected {
    pub readings: Vec<Reading>,
    pub facts: Vec<Fact>,
    pub processes: Vec<Process>,
    pub events: Vec<Event>,
}

impl Collected {
    pub fn is_empty(&self) -> bool {
        self.readings.is_empty()
            && self.facts.is_empty()
            && self.processes.is_empty()
            && self.events.is_empty()
    }
}

pub trait Source {
    /// The facility in use, e.g. `/proc/stat` or `sysinfo(2)`.
    fn origin(&self) -> String;

    /// Appends the current readings and context. Counter-based signals need
    /// two calls before they appear, because a rate needs a previous value.
    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()>;
}

/// Where the kernel interfaces are mounted. Tests point these at fixtures.
#[derive(Debug, Clone)]
pub struct Roots {
    pub proc: PathBuf,
    pub sys: PathBuf,
    /// The cgroup v2 hierarchy, normally `/sys/fs/cgroup`.
    pub cgroup: PathBuf,
    pub disk_exclude: Vec<String>,
    /// The agent's own state directory, where the journal position is kept
    /// across restarts. None when the agent runs without one.
    pub state: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum State {
    Preferred,
    Fallback,
    Disabled,
}

/// The outcome of probing one module, reported to the core.
#[derive(Debug, Clone, Serialize)]
pub struct ModuleStatus {
    pub module: &'static str,
    pub state: State,
    pub origin: Option<String>,
    pub reason: Option<String>,
}

type Opener = fn(&Roots, Instant) -> io::Result<Box<dyn Source>>;

struct Module {
    name: &'static str,
    candidates: &'static [Opener],
}

const MODULES: &[Module] = &[
    Module { name: "cpu", candidates: &[cpu::open_proc_stat] },
    Module { name: "memory", candidates: &[memory::open_meminfo, memory::open_sysinfo] },
    Module { name: "vmstat", candidates: &[vmstat::open_proc_vmstat] },
    Module { name: "disk", candidates: &[disk::open_proc_diskstats, disk::open_sys_block] },
    Module { name: "fs", candidates: &[fs::open_mounts] },
    Module { name: "pressure", candidates: &[pressure::open_proc_pressure] },
    Module { name: "cgroup", candidates: &[cgroup::open_cgroup2] },
    Module { name: "processes", candidates: &[processes::open_proc_pids] },
    Module { name: "power_supply", candidates: &[power::open_power_supply] },
    Module { name: "power_profile", candidates: &[power::open_platform_profile, power::open_cpufreq_epp] },
    Module { name: "journal", candidates: &[journal::open_journalctl, journal::open_kmsg] },
    Module { name: "self", candidates: &[selfstat::open_proc_self] },
];

pub struct Probed {
    pub sources: Vec<Box<dyn Source>>,
    pub statuses: Vec<ModuleStatus>,
}

/// Opens every module's best working source. Opening performs a first read,
/// so a source that is present but unreadable counts as missing.
pub fn probe(roots: &Roots) -> Probed {
    let now = Instant::now();
    let mut sources = Vec::new();
    let mut statuses = Vec::new();

    for module in MODULES {
        let mut failures = Vec::new();
        let mut chosen = None;
        for (rank, open) in module.candidates.iter().enumerate() {
            match open(roots, now) {
                Ok(source) => {
                    chosen = Some((rank, source));
                    break;
                }
                Err(err) => failures.push(err.to_string()),
            }
        }

        // Candidates often share a prerequisite and fail with the same error.
        failures.dedup();
        let reason = (!failures.is_empty()).then(|| failures.join("; "));
        let status = match chosen {
            Some((rank, source)) => {
                let status = ModuleStatus {
                    module: module.name,
                    state: if rank == 0 { State::Preferred } else { State::Fallback },
                    origin: Some(source.origin()),
                    reason,
                };
                sources.push(source);
                status
            }
            None => ModuleStatus { module: module.name, state: State::Disabled, origin: None, reason },
        };
        statuses.push(status);
    }

    Probed { sources, statuses }
}

/// Reads a kernel file, naming the path in the error.
fn read(path: &std::path::Path) -> io::Result<String> {
    std::fs::read_to_string(path)
        .map_err(|err| io::Error::new(err.kind(), format!("{}: {err}", path.display())))
}

fn invalid(what: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, what.into())
}

/// Reads a kernel file and trims the trailing newline.
fn read_trimmed(path: &std::path::Path) -> io::Result<String> {
    read(path).map(|text| text.trim_end().to_owned())
}

/// Clock ticks per second, the unit of CPU times in `/proc/<pid>/stat`.
fn clock_ticks() -> f64 {
    // SAFETY: sysconf only reads a system value.
    let n = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if n > 0 { n as f64 } else { 100.0 }
}

/// Size of a memory page, the unit of resident memory in `/proc/<pid>/stat`.
fn page_bytes() -> u64 {
    // SAFETY: sysconf only reads a system value.
    let n = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if n > 0 { n as u64 } else { 4096 }
}

/// Per-second rate of a monotonic counter. A counter that went backwards
/// (wrap or reset) gives no rate rather than a huge one.
fn rate(prev: u64, cur: u64, seconds: f64) -> Option<f64> {
    (cur >= prev && seconds > 0.0).then(|| (cur - prev) as f64 / seconds)
}

#[cfg(test)]
pub(crate) fn fixture_roots() -> Roots {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    Roots {
        proc: base.join("proc"),
        sys: base.join("sys"),
        cgroup: base.join("cgroup"),
        disk_exclude: vec![],
        state: None,
    }
}

#[cfg(test)]
pub(crate) fn missing_roots() -> Roots {
    Roots {
        proc: PathBuf::from("/nonexistent/proc"),
        sys: PathBuf::from("/nonexistent/sys"),
        cgroup: PathBuf::from("/nonexistent/cgroup"),
        disk_exclude: vec![],
        state: None,
    }
}

/// A fresh scratch directory for a test, removed by the caller.
#[cfg(test)]
pub(crate) fn scratch_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("gungnir-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[cfg(test)]
pub(crate) fn find<'a>(readings: &'a [Reading], signal: &str, entity: &str) -> Option<&'a Reading> {
    readings.iter().find(|r| r.signal == signal && r.entity == entity)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_source_disables_module_without_failing() {
        let roots = missing_roots();
        let probed = probe(&roots);
        let cpu = probed.statuses.iter().find(|s| s.module == "cpu").unwrap();
        assert_eq!(cpu.state, State::Disabled);
        assert!(cpu.reason.as_deref().unwrap().contains("/nonexistent/proc/stat"));
    }

    #[test]
    fn memory_falls_back_to_sysinfo() {
        let roots = missing_roots();
        let probed = probe(&roots);
        let memory = probed.statuses.iter().find(|s| s.module == "memory").unwrap();
        assert_eq!(memory.state, State::Fallback);
        assert_eq!(memory.origin.as_deref(), Some("sysinfo(2)"));
    }

    #[test]
    fn rate_ignores_counter_going_backwards() {
        assert_eq!(rate(10, 30, 2.0), Some(10.0));
        assert_eq!(rate(30, 10, 2.0), None);
    }
}
