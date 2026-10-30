//! The processes that use the host most, as context for diagnosis.
//!
//! Processes come and go and their ids are reused, so they make poor time
//! series. Each round instead yields a snapshot of the top consumers with
//! their control group and unit; the series live at the group level. Only
//! world-readable files are used (`stat`, `cgroup`), which is also how `top`
//! and `htop` see other users' processes without privileges.

use std::collections::HashMap;
use std::io;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::Serialize;

use super::{Collected, Reading, Roots, Source, cgroup, clock_ticks, invalid, page_bytes, read};

/// Processes kept per criterion: CPU, resident memory, waiting on I/O.
const TOP: usize = 10;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Process {
    pub pid: u32,
    pub ppid: u32,
    pub comm: String,
    /// Scheduler state letter: R running, S sleeping, D waiting on I/O, ...
    pub state: String,
    pub threads: u32,
    /// CPU time per second; 1.0 is one fully busy CPU.
    pub cpu_cores: f64,
    pub rss_bytes: u64,
    /// The control group as a cgroup entity name; None for kernel threads.
    pub cgroup: Option<String>,
    /// The systemd unit that owns the process, e.g. `nginx.service`.
    pub unit: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct Stat {
    pub pid: u32,
    pub comm: String,
    pub state: char,
    pub ppid: u32,
    /// User plus system time in clock ticks.
    pub cpu_ticks: u64,
    pub threads: u32,
    pub start_ticks: u64,
    pub rss_pages: u64,
}

pub(super) fn parse_stat(text: &str) -> Option<Stat> {
    // The command name may itself contain spaces and parentheses, so the
    // fields after it are found from the last ')'.
    let open = text.find('(')?;
    let close = text.rfind(')')?;
    let pid = text[..open].trim().parse().ok()?;
    let comm = text[open + 1..close].to_owned();
    let rest: Vec<&str> = text[close + 1..].split_ascii_whitespace().collect();
    // `rest[0]` is field 3 of proc_pid_stat(5).
    let num = |field: usize| rest.get(field - 3)?.parse::<u64>().ok();
    Some(Stat {
        pid,
        comm,
        state: rest.first()?.chars().next()?,
        ppid: num(4)? as u32,
        cpu_ticks: num(14)? + num(15)?,
        threads: num(20)? as u32,
        start_ticks: num(22)?,
        rss_pages: num(24)?,
    })
}

/// The cgroup v2 path of a process from `/proc/<pid>/cgroup`, without the
/// leading slash; None for the root group.
fn parse_cgroup(text: &str) -> Option<String> {
    let path = text.lines().find_map(|line| line.strip_prefix("0::"))?.trim_start_matches('/');
    (!path.is_empty()).then(|| path.to_owned())
}

/// The innermost systemd unit on a cgroup entity path.
fn unit_of(entity: &str) -> Option<String> {
    entity.rsplit('/').find(|c| c.ends_with(".service") || c.ends_with(".scope")).map(str::to_owned)
}

/// CPU ticks by pid and start time.
type Ticks = HashMap<(u32, u64), u64>;

pub struct ProcPids {
    proc: PathBuf,
    ticks_per_s: f64,
    page_bytes: u64,
    /// CPU ticks of each process at the previous round, keyed by pid and
    /// start time so a reused pid is not mistaken for the old process.
    prev: Option<(Instant, Ticks)>,
}

pub fn open_proc_pids(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = ProcPids {
        proc: roots.proc.clone(),
        ticks_per_s: clock_ticks(),
        page_bytes: page_bytes(),
        prev: None,
    };
    let mut out = Collected::default();
    source.collect(now, &mut out)?;
    if out.readings.iter().all(|r| r.value == 0.0) {
        return Err(invalid(format!("{}: no readable process", source.proc.display())));
    }
    Ok(Box::new(source))
}

impl ProcPids {
    fn stats(&self) -> io::Result<Vec<Stat>> {
        let mut stats = Vec::new();
        for entry in std::fs::read_dir(&self.proc)? {
            let entry = entry?;
            let name = entry.file_name();
            if !name.to_string_lossy().bytes().all(|b| b.is_ascii_digit()) {
                continue;
            }
            // A process may exit between listing and reading; skip it.
            if let Ok(text) = std::fs::read_to_string(entry.path().join("stat"))
                && let Some(stat) = parse_stat(&text)
            {
                stats.push(stat);
            }
        }
        Ok(stats)
    }

    fn describe(&self, stat: &Stat, cpu_cores: f64) -> Process {
        let cgroup = read(&self.proc.join(stat.pid.to_string()).join("cgroup"))
            .ok()
            .and_then(|text| parse_cgroup(&text))
            .map(|path| cgroup::entity(&path));
        Process {
            pid: stat.pid,
            ppid: stat.ppid,
            comm: stat.comm.clone(),
            state: stat.state.to_string(),
            threads: stat.threads,
            cpu_cores,
            rss_bytes: stat.rss_pages * self.page_bytes,
            unit: cgroup.as_deref().and_then(unit_of),
            cgroup,
        }
    }
}

/// Indices of the processes worth reporting: the top consumers of CPU and
/// of memory, and those waiting on I/O.
fn select(stats: &[Stat], cpu: &[f64]) -> Vec<usize> {
    let mut by_cpu: Vec<usize> = (0..stats.len()).filter(|&i| cpu[i] > 0.0).collect();
    by_cpu.sort_by(|&a, &b| cpu[b].total_cmp(&cpu[a]));
    let mut by_rss: Vec<usize> = (0..stats.len()).filter(|&i| stats[i].rss_pages > 0).collect();
    by_rss.sort_by_key(|&i| std::cmp::Reverse(stats[i].rss_pages));
    let waiting = (0..stats.len()).filter(|&i| stats[i].state == 'D');

    let mut chosen: Vec<usize> = by_cpu.into_iter().take(TOP).collect();
    chosen.extend(by_rss.into_iter().take(TOP));
    chosen.extend(waiting.take(TOP));
    chosen.sort_unstable();
    chosen.dedup();
    chosen
}

impl Source for ProcPids {
    fn origin(&self) -> String {
        Path::new(&self.proc).join("<pid>/stat").display().to_string()
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let stats = self.stats()?;
        out.readings.push(Reading::host("proc.count", stats.len() as f64));
        out.readings.push(Reading::host("proc.threads", stats.iter().map(|s| s.threads as f64).sum()));

        let ticks: Ticks = stats.iter().map(|s| ((s.pid, s.start_ticks), s.cpu_ticks)).collect();
        if let Some((then, prev)) = &self.prev {
            let seconds = now.duration_since(*then).as_secs_f64();
            let cpu: Vec<f64> = stats
                .iter()
                .map(|s| match prev.get(&(s.pid, s.start_ticks)) {
                    Some(&p) if seconds > 0.0 && s.cpu_ticks >= p => {
                        (s.cpu_ticks - p) as f64 / self.ticks_per_s / seconds
                    }
                    _ => 0.0,
                })
                .collect();
            for i in select(&stats, &cpu) {
                out.processes.push(self.describe(&stats[i], cpu[i]));
            }
        }
        self.prev = Some((now, ticks));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::sources::scratch_dir;

    fn stat_line(pid: u32, comm: &str, state: char, ticks: u64, rss: u64) -> String {
        format!(
            "{pid} ({comm}) {state} 1 {pid} {pid} 0 -1 4194304 0 0 0 0 {ticks} 0 0 0 20 0 3 0 777 1000000 {rss} \
             18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 3 0 0 0 0 0"
        )
    }

    #[test]
    fn parses_names_with_spaces_and_parentheses() {
        let stat = parse_stat(&stat_line(42, "tmux: server (1)", 'S', 500, 100)).unwrap();
        assert_eq!(stat.comm, "tmux: server (1)");
        assert_eq!(stat.state, 'S');
        assert_eq!(stat.cpu_ticks, 500);
        assert_eq!(stat.threads, 3);
        assert_eq!(stat.start_ticks, 777);
        assert_eq!(stat.rss_pages, 100);
    }

    #[test]
    fn cgroup_path_gives_entity_and_unit() {
        let path =
            parse_cgroup("0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-code-77.scope\n");
        let entity = cgroup::entity(&path.unwrap());
        assert_eq!(entity, "user.slice/user-1000.slice/user@1000.service/app.slice/app-code.scope");
        assert_eq!(unit_of(&entity).as_deref(), Some("app-code.scope"));
        assert_eq!(parse_cgroup("0::/\n"), None);
    }

    #[test]
    fn snapshot_holds_top_consumers_with_their_cpu() {
        let proc = scratch_dir("procs");
        let write = |pid: u32, comm: &str, state: char, ticks: u64| {
            let dir = proc.join(pid.to_string());
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(dir.join("stat"), stat_line(pid, comm, state, ticks, 10)).unwrap();
            std::fs::write(dir.join("cgroup"), "0::/system.slice/build.service\n").unwrap();
        };
        write(10, "cc1", 'R', 0);
        write(11, "idle", 'S', 0);
        let mut source = ProcPids { proc: proc.clone(), ticks_per_s: 100.0, page_bytes: 4096, prev: None };
        let t0 = Instant::now();
        source.collect(t0, &mut Collected::default()).unwrap();

        write(10, "cc1", 'R', 200);
        write(11, "idle", 'D', 0);
        let mut out = Collected::default();
        source.collect(t0 + Duration::from_secs(2), &mut out).unwrap();
        let cc1 = out.processes.iter().find(|p| p.comm == "cc1").unwrap();
        assert_eq!(cc1.cpu_cores, 1.0);
        assert_eq!(cc1.unit.as_deref(), Some("build.service"));
        assert_eq!(cc1.rss_bytes, 40960);
        assert!(out.processes.iter().any(|p| p.comm == "idle" && p.state == "D"));
        std::fs::remove_dir_all(proc).unwrap();
    }
}
