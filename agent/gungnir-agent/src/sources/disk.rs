//! Per-disk throughput, latency and utilisation from `/proc/diskstats`, or
//! from `/sys/block/<disk>/stat` when the former is unavailable.
//!
//! Only whole disks are reported: the entries of `/sys/block`, minus the
//! excluded prefixes (loop and RAM devices by default). Partitions would
//! repeat their disk's load.
//!
//! Each disk also yields facts that decide which remedies can work: whether
//! it is rotational, its I/O scheduler, and whether the iocost controller
//! weighs its I/O. Proportional I/O weights and I/O priorities need iocost
//! or BFQ; an absolute `io.max` limit works everywhere.

use std::collections::HashMap;
use std::io;
use std::path::PathBuf;
use std::time::Instant;

use super::{Collected, FactLog, Reading, Roots, Source, invalid, rate, read, read_trimmed};

/// Sectors in diskstats are always 512 bytes, whatever the device uses.
const SECTOR: f64 = 512.0;

/// The first eleven fields of a diskstats line after the device name.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
struct Stat {
    reads: u64,
    read_sectors: u64,
    read_ms: u64,
    writes: u64,
    write_sectors: u64,
    write_ms: u64,
    in_flight: u64,
    io_ms: u64,
    weighted_ms: u64,
}

fn parse_fields(fields: &[&str]) -> Option<Stat> {
    let n = |i: usize| fields.get(i)?.parse::<u64>().ok();
    Some(Stat {
        reads: n(0)?,
        read_sectors: n(2)?,
        read_ms: n(3)?,
        writes: n(4)?,
        write_sectors: n(6)?,
        write_ms: n(7)?,
        in_flight: n(8)?,
        io_ms: n(9)?,
        weighted_ms: n(10)?,
    })
}

fn parse_diskstats(text: &str) -> HashMap<String, Stat> {
    text.lines()
        .filter_map(|line| {
            let fields: Vec<&str> = line.split_ascii_whitespace().collect();
            let name = fields.get(2)?;
            Some((name.to_string(), parse_fields(fields.get(3..)?)?))
        })
        .collect()
}

fn whole_disks(roots: &Roots) -> io::Result<Vec<String>> {
    let dir = roots.sys.join("block");
    let entries = std::fs::read_dir(&dir)
        .map_err(|err| io::Error::new(err.kind(), format!("{}: {err}", dir.display())))?;
    let mut disks: Vec<String> = entries
        .filter_map(|e| e.ok()?.file_name().into_string().ok())
        .filter(|name| !roots.disk_exclude.iter().any(|p| name.starts_with(p.as_str())))
        .collect();
    disks.sort();
    Ok(disks)
}

enum Origin {
    Diskstats(PathBuf),
    SysBlock,
}

pub struct Disks {
    roots: Roots,
    origin: Origin,
    prev: Option<(Instant, HashMap<String, Stat>)>,
    facts: FactLog,
}

pub fn open_proc_diskstats(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let origin = Origin::Diskstats(roots.proc.join("diskstats"));
    open(roots, origin, now)
}

pub fn open_sys_block(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    open(roots, Origin::SysBlock, now)
}

fn open(roots: &Roots, origin: Origin, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = Disks { roots: roots.clone(), origin, prev: None, facts: FactLog::default() };
    source.collect(now, &mut Collected::default())?;
    source.facts.clear();
    Ok(Box::new(source))
}

impl Disks {
    /// Reads current counters of whole disks. The disk list is re-read each
    /// time so hot-plugged disks appear without a restart.
    fn snapshot(&self) -> io::Result<HashMap<String, Stat>> {
        let disks = whole_disks(&self.roots)?;
        match &self.origin {
            Origin::Diskstats(path) => {
                let mut all = parse_diskstats(&read(path)?);
                Ok(disks.into_iter().filter_map(|d| Some((d.clone(), all.remove(&d)?))).collect())
            }
            Origin::SysBlock => {
                let mut stats = HashMap::new();
                for disk in disks {
                    let path = self.roots.sys.join("block").join(&disk).join("stat");
                    let text = read(&path)?;
                    let fields: Vec<&str> = text.split_ascii_whitespace().collect();
                    let stat = parse_fields(&fields)
                        .ok_or_else(|| invalid(format!("{}: malformed", path.display())))?;
                    stats.insert(disk, stat);
                }
                Ok(stats)
            }
        }
    }
}

impl Source for Disks {
    fn origin(&self) -> String {
        match &self.origin {
            Origin::Diskstats(path) => path.display().to_string(),
            Origin::SysBlock => self.roots.sys.join("block/*/stat").display().to_string(),
        }
    }

    fn collect(&mut self, now: Instant, out: &mut Collected) -> io::Result<()> {
        let cur = self.snapshot()?;
        if let Some((then, prev)) = &self.prev {
            let seconds = now.duration_since(*then).as_secs_f64();
            for (disk, b) in &cur {
                out.readings.push(reading("disk.in_flight", disk, b.in_flight as f64));
                if let Some(a) = prev.get(disk) {
                    push_deltas(disk, a, b, seconds, out);
                }
            }
        }
        self.push_facts(now, cur.keys(), out);
        self.prev = Some((now, cur));
        Ok(())
    }
}

impl Disks {
    /// Facts of each disk. A file that cannot be read gives no fact rather
    /// than a wrong one.
    fn push_facts<'a>(&mut self, now: Instant, disks: impl Iterator<Item = &'a String>, out: &mut Collected) {
        let iocost =
            read(&self.roots.cgroup.join("io.cost.qos")).map(|t| iocost_devices(&t)).unwrap_or_default();
        for disk in disks {
            let dir = self.roots.sys.join("block").join(disk);
            if let Ok(flag) = read_trimmed(&dir.join("queue/rotational")) {
                let value = if flag == "1" { "yes" } else { "no" };
                self.facts.push(now, "disk.rotational", disk, value.to_owned(), out);
            }
            if let Ok(text) = read_trimmed(&dir.join("queue/scheduler")) {
                self.facts.push(now, "disk.scheduler", disk, active_scheduler(&text).to_owned(), out);
            }
            if let Ok(dev) = read_trimmed(&dir.join("dev")) {
                let value = if iocost.contains(&dev) { "on" } else { "off" };
                self.facts.push(now, "disk.iocost", disk, value.to_owned(), out);
            }
        }
    }
}

/// The scheduler in use: the bracketed name of `queue/scheduler`, or the
/// only name when the device offers no choice.
fn active_scheduler(text: &str) -> &str {
    text.split_ascii_whitespace()
        .find_map(|name| name.strip_prefix('[')?.strip_suffix(']'))
        .unwrap_or(text.trim())
}

/// Devices (`major:minor`) on which iocost is enabled, from the root
/// group's `io.cost.qos`. The file is empty when no device has it.
fn iocost_devices(text: &str) -> Vec<String> {
    text.lines()
        .filter(|line| line.split_ascii_whitespace().any(|pair| pair == "enable=1"))
        .filter_map(|line| line.split_ascii_whitespace().next().map(str::to_owned))
        .collect()
}

fn reading(signal: &'static str, disk: &str, value: f64) -> Reading {
    Reading { signal, entity: disk.to_owned(), value }
}

fn push_deltas(disk: &str, a: &Stat, b: &Stat, seconds: f64, out: &mut Collected) {
    let mut push = |signal, value: Option<f64>| {
        if let Some(v) = value {
            out.readings.push(reading(signal, disk, v));
        }
    };
    push("disk.read_bytes_per_s", rate(a.read_sectors, b.read_sectors, seconds).map(|v| v * SECTOR));
    push("disk.write_bytes_per_s", rate(a.write_sectors, b.write_sectors, seconds).map(|v| v * SECTOR));
    push("disk.read_ops_per_s", rate(a.reads, b.reads, seconds));
    push("disk.write_ops_per_s", rate(a.writes, b.writes, seconds));
    // Average time per request, queueing included; 0 when idle, as iostat does.
    push("disk.read_await_ms", await_ms(a.read_ms, b.read_ms, a.reads, b.reads));
    push("disk.write_await_ms", await_ms(a.write_ms, b.write_ms, a.writes, b.writes));
    // Average request size after merging, as iostat's areq-sz; small
    // requests mark random I/O. No requests, no size.
    push("disk.read_request_bytes", per_request(a.read_sectors, b.read_sectors, a.reads, b.reads));
    push("disk.write_request_bytes", per_request(a.write_sectors, b.write_sectors, a.writes, b.writes));
    let ms = seconds * 1000.0;
    // Share of wall time with at least one request in flight. Misleading for
    // parallel devices such as NVMe, where queue depth says more.
    push("disk.util_ratio", rate(a.io_ms, b.io_ms, ms).map(|v| v.min(1.0)));
    push("disk.queue_depth", rate(a.weighted_ms, b.weighted_ms, ms));
}

fn per_request(sectors_a: u64, sectors_b: u64, ops_a: u64, ops_b: u64) -> Option<f64> {
    if sectors_b < sectors_a || ops_b <= ops_a {
        return None;
    }
    Some((sectors_b - sectors_a) as f64 * SECTOR / (ops_b - ops_a) as f64)
}

fn await_ms(ms_a: u64, ms_b: u64, ops_a: u64, ops_b: u64) -> Option<f64> {
    if ms_b < ms_a || ops_b < ops_a {
        return None;
    }
    let ops = ops_b - ops_a;
    Some(if ops == 0 { 0.0 } else { (ms_b - ms_a) as f64 / ops as f64 })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::{find, fixture_roots, scratch_dir};

    fn roots_without_loop() -> Roots {
        Roots { disk_exclude: vec!["loop".into()], ..fixture_roots() }
    }

    #[test]
    fn parses_diskstats_fields() {
        let text = std::fs::read_to_string(fixture_roots().proc.join("diskstats")).unwrap();
        let stats = parse_diskstats(&text);
        assert_eq!(stats["sda"].reads, 1000);
        assert_eq!(stats["sda"].write_sectors, 40000);
        assert_eq!(stats["sda"].weighted_ms, 1400);
    }

    #[test]
    fn whole_disks_skip_excluded_prefixes() {
        assert_eq!(whole_disks(&roots_without_loop()).unwrap(), vec!["dm-0", "nvme0n1", "sda"]);
    }

    #[test]
    fn derives_throughput_latency_and_queue() {
        let a = Stat {
            reads: 100,
            read_sectors: 1000,
            read_ms: 50,
            writes: 10,
            write_ms: 40,
            ..Default::default()
        };
        let b = Stat {
            reads: 200,
            read_sectors: 3000,
            read_ms: 250,
            writes: 10,
            write_ms: 40,
            io_ms: 1500,
            weighted_ms: 4000,
            ..Default::default()
        };
        let mut out = Collected::default();
        push_deltas("sda", &a, &b, 2.0, &mut out);
        let v = |s| find(&out.readings, s, "sda").unwrap().value;
        assert_eq!(v("disk.read_bytes_per_s"), 1000.0 * 512.0);
        assert_eq!(v("disk.read_ops_per_s"), 50.0);
        assert_eq!(v("disk.read_await_ms"), 2.0);
        assert_eq!(v("disk.write_await_ms"), 0.0);
        assert_eq!(v("disk.util_ratio"), 0.75);
        assert_eq!(v("disk.queue_depth"), 2.0);
        assert_eq!(v("disk.read_request_bytes"), 20.0 * 512.0);
        assert!(find(&out.readings, "disk.write_request_bytes", "sda").is_none());
    }

    #[test]
    fn facts_say_which_remedies_can_work() {
        let root = scratch_dir("disk-facts");
        let queue = root.join("sys/block/nvme0n1/queue");
        std::fs::create_dir_all(&queue).unwrap();
        std::fs::write(queue.join("rotational"), "0\n").unwrap();
        std::fs::write(queue.join("scheduler"), "[none] mq-deadline kyber bfq\n").unwrap();
        std::fs::write(root.join("sys/block/nvme0n1/dev"), "259:0\n").unwrap();
        std::fs::create_dir_all(root.join("cgroup")).unwrap();
        std::fs::write(root.join("cgroup/io.cost.qos"), "8:0 enable=1 ctrl=auto\n259:0 enable=0 ctrl=auto\n")
            .unwrap();
        let roots = Roots { sys: root.join("sys"), cgroup: root.join("cgroup"), ..fixture_roots() };
        let mut disks = Disks { roots, origin: Origin::SysBlock, prev: None, facts: FactLog::default() };
        let mut out = Collected::default();
        let t0 = Instant::now();
        disks.push_facts(t0, ["nvme0n1".to_owned()].iter(), &mut out);
        disks.push_facts(t0, ["nvme0n1".to_owned()].iter(), &mut out);
        let facts: Vec<_> = out.facts.iter().map(|f| (f.name, f.value.as_str())).collect();
        assert_eq!(facts, [("disk.rotational", "no"), ("disk.scheduler", "none"), ("disk.iocost", "off")]);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn scheduler_without_choice_is_its_only_name() {
        assert_eq!(active_scheduler("mq-deadline kyber [bfq] none"), "bfq");
        assert_eq!(active_scheduler("none"), "none");
    }

    #[test]
    fn both_origins_see_whole_disks_only_and_agree() {
        let preferred = Disks {
            roots: roots_without_loop(),
            origin: Origin::Diskstats(fixture_roots().proc.join("diskstats")),
            prev: None,
            facts: FactLog::default(),
        };
        let fallback = Disks {
            roots: roots_without_loop(),
            origin: Origin::SysBlock,
            prev: None,
            facts: FactLog::default(),
        };
        let a = preferred.snapshot().unwrap();
        let b = fallback.snapshot().unwrap();
        let mut disks: Vec<_> = a.keys().cloned().collect();
        disks.sort();
        assert_eq!(disks, vec!["dm-0", "nvme0n1", "sda"]);
        assert_eq!(a, b);
    }
}
