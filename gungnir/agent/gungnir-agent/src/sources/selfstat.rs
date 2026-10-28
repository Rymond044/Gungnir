//! The agent's own CPU and memory use, so that its overhead (NFR-1) is a
//! signal like any other. Children such as the journal reader are included.
//!
//! Memory is the private part of resident memory. The journal reader maps
//! journal files, and those pages are page cache shared with the system
//! and reclaimable; counting them would report tens of megabytes that the
//! agent does not own.

use std::io;
use std::path::{Path, PathBuf};
use std::time::Instant;

use super::processes::parse_stat;
use super::{Collected, Reading, Roots, Source, clock_ticks, invalid, page_bytes, read};

pub struct ProcSelf {
    dir: PathBuf,
    ticks_per_s: f64,
    page_bytes: u64,
    prev: Option<(Instant, u64)>,
}

pub fn open_proc_self(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = ProcSelf {
        dir: roots.proc.join("self"),
        ticks_per_s: clock_ticks(),
        page_bytes: page_bytes(),
        prev: None,
    };
    source.collect(now, &mut Collected::default())?;
    Ok(Box::new(source))
}

impl ProcSelf {
    /// CPU ticks and private resident pages of the agent and its direct
    /// children.
    fn usage(&self) -> io::Result<(u64, u64)> {
        let path = self.dir.join("stat");
        let own =
            parse_stat(&read(&path)?).ok_or_else(|| invalid(format!("{}: unparsable", path.display())))?;
        let (mut ticks, mut pages) = (own.cpu_ticks, private_pages(&self.dir).unwrap_or(0));
        let proc = self.dir.parent().unwrap_or(&self.dir);
        for child in self.children() {
            let dir = proc.join(child.to_string());
            if let Some(stat) = read(&dir.join("stat")).ok().and_then(|t| parse_stat(&t)) {
                ticks += stat.cpu_ticks;
                pages += private_pages(&dir).unwrap_or(0);
            }
        }
        Ok((ticks, pages))
    }

    fn children(&self) -> Vec<u32> {
        let Ok(tasks) = std::fs::read_dir(self.dir.join("task")) else { return Vec::new() };
        tasks
            .flatten()
            .filter_map(|task| std::fs::read_to_string(task.path().join("children")).ok())
            .flat_map(|text| text.split_ascii_whitespace().filter_map(|p| p.parse().ok()).collect::<Vec<_>>())
            .collect()
    }
}

/// Resident pages not backed by a file: `resident - shared` from
/// `/proc/<pid>/statm`, the same quantity as `RssAnon` in `status`.
fn private_pages(dir: &Path) -> Option<u64> {
    let text = read(&dir.join("statm")).ok()?;
    let mut fields = text.split_ascii_whitespace().skip(1).map(|f| f.parse::<u64>().ok());
    let (resident, shared) = (fields.next()??, fields.next()??);
    Some(resident.saturating_sub(shared))
}

impl Source for ProcSelf {
    fn origin(&self) -> String {
        self.dir.join("stat").display().to_string()
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let (ticks, pages) = self.usage()?;
        out.readings.push(Reading::host("agent.anon_bytes", (pages * self.page_bytes) as f64));
        if let Some((then, prev)) = self.prev {
            let seconds = now.duration_since(then).as_secs_f64();
            // A child that exited takes its ticks with it; skip that round.
            if seconds > 0.0 && ticks >= prev {
                out.readings.push(Reading::host(
                    "agent.cpu_cores",
                    (ticks - prev) as f64 / self.ticks_per_s / seconds,
                ));
            }
        }
        self.prev = Some((now, ticks));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::find;

    #[test]
    fn reports_own_private_memory() {
        let roots = Roots { proc: PathBuf::from("/proc"), ..crate::sources::missing_roots() };
        let mut source = ProcSelf {
            dir: roots.proc.join("self"),
            ticks_per_s: 100.0,
            page_bytes: page_bytes(),
            prev: None,
        };
        let mut out = Collected::default();
        source.collect(Instant::now(), &mut out).unwrap();
        assert!(find(&out.readings, "agent.anon_bytes", "host").unwrap().value > 0.0);
    }
}
