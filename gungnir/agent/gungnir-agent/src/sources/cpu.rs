//! CPU time shares and scheduler activity from `/proc/stat`.

use std::io;
use std::path::PathBuf;
use std::time::Instant;

use super::{Reading, Roots, Source, invalid, rate, read};

/// Aggregate CPU times in clock ticks. `user` and `nice` already include
/// guest time, so guest fields are not added again.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
struct Times {
    user: u64,
    nice: u64,
    system: u64,
    idle: u64,
    iowait: u64,
    irq: u64,
    softirq: u64,
    steal: u64,
}

impl Times {
    fn total(&self) -> u64 {
        self.user + self.nice + self.system + self.idle + self.iowait + self.irq + self.softirq + self.steal
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
struct Stat {
    times: Times,
    context_switches: u64,
    forks: u64,
    running: u64,
    blocked: u64,
}

fn parse(text: &str) -> io::Result<Stat> {
    let mut stat = Stat::default();
    let mut have_cpu = false;
    for line in text.lines() {
        let mut fields = line.split_ascii_whitespace();
        let Some(key) = fields.next() else { continue };
        let mut next = || -> io::Result<u64> {
            fields
                .next()
                .ok_or_else(|| invalid(format!("/proc/stat: short line '{key}'")))?
                .parse()
                .map_err(|_| invalid(format!("/proc/stat: bad number in '{key}'")))
        };
        match key {
            "cpu" => {
                stat.times = Times {
                    user: next()?,
                    nice: next()?,
                    system: next()?,
                    idle: next()?,
                    iowait: next()?,
                    irq: next()?,
                    softirq: next()?,
                    steal: next()?,
                };
                have_cpu = true;
            }
            "ctxt" => stat.context_switches = next()?,
            "processes" => stat.forks = next()?,
            "procs_running" => stat.running = next()?,
            "procs_blocked" => stat.blocked = next()?,
            _ => {}
        }
    }
    if !have_cpu {
        return Err(invalid("/proc/stat: no aggregate 'cpu' line"));
    }
    Ok(stat)
}

pub struct ProcStat {
    path: PathBuf,
    prev: Option<(Instant, Stat)>,
}

pub fn open_proc_stat(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = ProcStat { path: roots.proc.join("stat"), prev: None };
    source.collect(now, &mut Vec::new())?;
    Ok(Box::new(source))
}

impl Source for ProcStat {
    fn origin(&self) -> String {
        self.path.display().to_string()
    }

    fn collect(&mut self, now: Instant, out: &mut Vec<Reading>) -> io::Result<()> {
        let cur = parse(&read(&self.path)?)?;
        out.push(Reading::host("cpu.runnable_tasks", cur.running as f64));
        out.push(Reading::host("cpu.blocked_tasks", cur.blocked as f64));

        if let Some((then, prev)) = self.prev {
            let seconds = now.duration_since(then).as_secs_f64();
            // The iowait counter may step backwards on some kernels, so each
            // share uses a saturating difference.
            let (a, b) = (prev.times, cur.times);
            let total = b.total().saturating_sub(a.total());
            if total > 0 {
                let share = |p: u64, c: u64| c.saturating_sub(p) as f64 / total as f64;
                out.push(Reading::host("cpu.user_ratio", share(a.user, b.user)));
                out.push(Reading::host("cpu.nice_ratio", share(a.nice, b.nice)));
                out.push(Reading::host("cpu.system_ratio", share(a.system, b.system)));
                out.push(Reading::host("cpu.iowait_ratio", share(a.iowait, b.iowait)));
                out.push(Reading::host("cpu.irq_ratio", share(a.irq + a.softirq, b.irq + b.softirq)));
                out.push(Reading::host("cpu.steal_ratio", share(a.steal, b.steal)));
                out.push(Reading::host("cpu.idle_ratio", share(a.idle, b.idle)));
            }
            if let Some(v) = rate(prev.context_switches, cur.context_switches, seconds) {
                out.push(Reading::host("cpu.context_switches_per_s", v));
            }
            if let Some(v) = rate(prev.forks, cur.forks, seconds) {
                out.push(Reading::host("cpu.forks_per_s", v));
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
    use crate::sources::{find, fixture_roots};

    #[test]
    fn parses_real_proc_stat() {
        let text = std::fs::read_to_string(fixture_roots().proc.join("stat")).unwrap();
        let stat = parse(&text).unwrap();
        assert!(stat.times.total() > 0);
        assert!(stat.context_switches > 0);
    }

    #[test]
    fn computes_shares_and_rates_between_reads() {
        let dir = std::env::temp_dir().join(format!("gungnir-cpu-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("stat");
        let write = |cpu: &str, ctxt: u64| {
            std::fs::write(
                &path,
                format!("cpu {cpu}\nctxt {ctxt}\nprocesses 100\nprocs_running 3\nprocs_blocked 1\n"),
            )
            .unwrap();
        };

        write("100 0 100 700 100 0 0 0 0 0", 1000);
        let mut source = ProcStat { path: path.clone(), prev: None };
        let t0 = Instant::now();
        let mut out = Vec::new();
        source.collect(t0, &mut out).unwrap();
        assert!(find(&out, "cpu.iowait_ratio", "host").is_none());

        write("150 0 150 750 150 0 0 0 0 0", 3000);
        out.clear();
        source.collect(t0 + Duration::from_secs(2), &mut out).unwrap();
        assert_eq!(find(&out, "cpu.iowait_ratio", "host").unwrap().value, 0.25);
        assert_eq!(find(&out, "cpu.context_switches_per_s", "host").unwrap().value, 1000.0);
        assert_eq!(find(&out, "cpu.blocked_tasks", "host").unwrap().value, 1.0);

        std::fs::remove_dir_all(dir).unwrap();
    }
}
