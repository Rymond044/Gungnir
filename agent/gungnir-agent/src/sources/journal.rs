//! Kernel and service events: OOM kills, hung tasks, I/O and filesystem
//! errors, units that failed or were killed for memory.
//!
//! Preferred is the systemd journal, read through `journalctl`, which also
//! carries the kernel's messages. Its JSON output is a documented format,
//! and running it as a child keeps C bindings out of the agent. The agent
//! remembers the cursor of the last event it took, so a restart resumes
//! where it stopped. The fallback is `/dev/kmsg`, kernel messages only.
//!
//! Only events worth diagnosing leave the host: kernel messages of error
//! priority or worse, warnings of the storage stack, and a few systemd
//! messages identified by their message id. Turning them into event types is
//! knowledge and happens in the core.

use std::fs::File;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::thread;
use std::time::Instant;

use log::warn;
use serde::Serialize;

use super::{Collected, Roots, Source, invalid, read_trimmed};
use crate::clock;

/// Least severe syslog priority still reported for kernel messages
/// (3 = error). Warnings would bring the call trace that the kernel prints
/// with every OOM kill, dozens of lines per event.
const MAX_KERNEL_PRIORITY: u8 = 3;
/// Warnings (4) of the storage stack are kept too: disk drivers report
/// command timeouts and controller resets at this priority, e.g. NVMe's
/// `nvme nvme0: I/O tag 12 … timeout, aborting`. Matched on the start of
/// the message, which names the driver or the filesystem.
const STORAGE_WARNING: u8 = 4;
const STORAGE_PREFIXES: [&str; 8] = ["nvme", "sd ", "scsi", "mmc", "BTRFS", "EXT4-fs", "XFS", "F2FS-fs"];
const MAX_MESSAGE_BYTES: usize = 1000;
/// Events waiting between the reader thread and the sampling loop.
const QUEUE: usize = 1024;

/// systemd message ids from `sd-messages.h`.
const MESSAGE_IDS: [&str; 3] = [
    // SD_MESSAGE_UNIT_FAILURE_RESULT: a unit failed, with the reason.
    "d9b373ed55a64feb8242e02dbe79a49c",
    // SD_MESSAGE_UNIT_OUT_OF_MEMORY: the kernel OOM killer hit a unit.
    "fe6faa94e7774663a0da52717891d8ef",
    // SD_MESSAGE_UNIT_OOMD_KILL: systemd-oomd killed a unit.
    "d989611b15e44c9dbf31e3c81256e4ed",
];

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Event {
    /// Milliseconds since the Unix epoch.
    pub ts: i64,
    /// `kernel` or `systemd`.
    pub source: &'static str,
    /// Syslog priority, 0 (emergency) to 7 (debug).
    pub priority: u8,
    pub unit: Option<String>,
    pub message_id: Option<String>,
    pub message: String,
    /// Unique per event on the host, so a resent batch adds nothing.
    pub key: String,
}

fn keep_kernel(priority: u8, message: &str) -> bool {
    priority <= MAX_KERNEL_PRIORITY || (priority == STORAGE_WARNING && from_storage(message))
}

fn from_storage(message: &str) -> bool {
    // libata names ports `ata1`, `ata1.00`.
    let ata = message.strip_prefix("ata").is_some_and(|rest| rest.starts_with(|c: char| c.is_ascii_digit()));
    ata || STORAGE_PREFIXES.iter().any(|prefix| message.starts_with(prefix))
}

fn truncate(mut text: String) -> String {
    if text.len() > MAX_MESSAGE_BYTES {
        let mut end = MAX_MESSAGE_BYTES;
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text.truncate(end);
    }
    text
}

/// An event from one line of `journalctl --output=json`, if it is one we
/// report. Returns the entry's cursor either way, to track the position.
fn parse_entry(line: &str) -> Option<(String, Option<Event>)> {
    let entry: serde_json::Value = serde_json::from_str(line).ok()?;
    let text = |key: &str| entry.get(key).and_then(|v| v.as_str()).map(str::to_owned);
    let cursor = text("__CURSOR")?;
    let ts_us: i64 = text("__REALTIME_TIMESTAMP")?.parse().ok()?;
    let priority: u8 = text("PRIORITY").and_then(|p| p.parse().ok()).unwrap_or(6);
    let message_id = text("MESSAGE_ID");
    // Messages that are not valid UTF-8 arrive as arrays of bytes.
    let message = match entry.get("MESSAGE") {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(serde_json::Value::Array(bytes)) => String::from_utf8_lossy(
            &bytes.iter().filter_map(|b| b.as_u64().map(|b| b as u8)).collect::<Vec<_>>(),
        )
        .into_owned(),
        _ => String::new(),
    };

    let source = if text("_TRANSPORT").as_deref() == Some("kernel") {
        if !keep_kernel(priority, &message) {
            return Some((cursor, None));
        }
        "kernel"
    } else if message_id.as_deref().is_some_and(|id| MESSAGE_IDS.contains(&id)) {
        "systemd"
    } else {
        return Some((cursor, None));
    };
    let event = Event {
        ts: ts_us / 1000,
        source,
        priority,
        unit: text("UNIT").or_else(|| text("USER_UNIT")),
        message_id,
        message: truncate(message),
        key: cursor.clone(),
    };
    Some((cursor, Some(event)))
}

fn journalctl() -> Command {
    let mut cmd = Command::new("journalctl");
    // Kernel and service manager messages are in the system journal; user
    // journals would only add files to map.
    cmd.args(["--system", "--output=json", "--quiet", "--no-pager"])
        .stdin(Stdio::null())
        .stderr(Stdio::null());
    cmd
}

/// Journal matches: kernel messages, or any of the message ids. Matches on
/// different fields are joined by `+` (or); repeated fields are or-ed too.
fn matches() -> Vec<String> {
    let mut args = vec!["_TRANSPORT=kernel".to_owned(), "+".to_owned()];
    args.extend(MESSAGE_IDS.iter().map(|id| format!("MESSAGE_ID={id}")));
    args
}

type Item = (String, Option<Event>);

pub struct Journalctl {
    cursor_file: Option<PathBuf>,
    cursor: Option<String>,
    child: Child,
    rx: Receiver<Item>,
}

pub fn open_journalctl(roots: &Roots, _now: Instant) -> io::Result<Box<dyn Source>> {
    // Every boot logs kernel messages, so seeing none means the journal is
    // closed to us (the agent needs the `systemd-journal` group).
    let probe = journalctl().args(["--lines=1", "_TRANSPORT=kernel"]).output()?;
    if !probe.status.success() || probe.stdout.is_empty() {
        return Err(invalid("journalctl shows no kernel messages; is the agent in group systemd-journal?"));
    }
    let cursor_file = roots.state.as_ref().map(|dir| dir.join("journal.cursor"));
    let cursor = cursor_file.as_deref().and_then(|f| read_trimmed(f).ok()).filter(|c| !c.is_empty());
    let (child, rx) = spawn(cursor.as_deref())?;
    Ok(Box::new(Journalctl { cursor_file, cursor, child, rx }))
}

/// Starts following the journal after `cursor`, or from now on.
fn spawn(cursor: Option<&str>) -> io::Result<(Child, Receiver<Item>)> {
    let mut cmd = journalctl();
    cmd.arg("--follow").stdout(Stdio::piped());
    match cursor {
        Some(c) => cmd.arg(format!("--after-cursor={c}")),
        None => cmd.arg("--lines=0"),
    };
    let mut child = cmd.args(matches()).spawn()?;
    let stdout = child.stdout.take().ok_or_else(|| invalid("journalctl: no stdout"))?;
    let (tx, rx) = mpsc::sync_channel(QUEUE);
    thread::Builder::new().name("journal".into()).spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            if let Some(item) = parse_entry(&line)
                && tx.send(item).is_err()
            {
                break;
            }
        }
    })?;
    Ok((child, rx))
}

impl Journalctl {
    fn save_cursor(&self) {
        let (Some(file), Some(cursor)) = (&self.cursor_file, &self.cursor) else { return };
        let tmp = file.with_extension("tmp");
        if let Err(err) = std::fs::write(&tmp, cursor).and_then(|()| std::fs::rename(&tmp, file)) {
            warn!("{}: cannot save journal position: {err}", file.display());
        }
    }
}

impl Source for Journalctl {
    fn origin(&self) -> String {
        "journalctl".to_owned()
    }

    fn collect(&mut self, _now: Instant, out: &mut Collected) -> io::Result<()> {
        let before = self.cursor.clone();
        loop {
            match self.rx.try_recv() {
                Ok((cursor, event)) => {
                    self.cursor = Some(cursor);
                    out.events.extend(event);
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    // journalctl ended; restart it where we stopped and
                    // report this round as failed so the outage is logged.
                    let status = self.child.wait().map(|s| s.to_string()).unwrap_or_default();
                    (self.child, self.rx) = spawn(self.cursor.as_deref())?;
                    self.save_cursor();
                    return Err(invalid(format!("journalctl exited ({status}), restarted")));
                }
            }
        }
        if self.cursor != before {
            self.save_cursor();
        }
        Ok(())
    }
}

impl Drop for Journalctl {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// A kernel message from one `/dev/kmsg` record: `prio,seq,usec,flags;text`.
fn parse_kmsg(record: &str, boot_id: &str, ts: i64) -> Option<Event> {
    let (header, text) = record.split_once(';')?;
    let mut fields = header.split(',');
    let priority = (fields.next()?.parse::<u32>().ok()? & 7) as u8;
    let seq = fields.next()?;
    // Continuation lines carry key=value metadata, not the message.
    let message = text.lines().next().unwrap_or_default().to_owned();
    if !keep_kernel(priority, &message) {
        return None;
    }
    Some(Event {
        ts,
        source: "kernel",
        priority,
        unit: None,
        message_id: None,
        message: truncate(message),
        key: format!("kmsg:{boot_id}:{seq}"),
    })
}

pub struct Kmsg {
    file: File,
    boot_id: String,
}

const KMSG: &str = "/dev/kmsg";

pub fn open_kmsg(roots: &Roots, _now: Instant) -> io::Result<Box<dyn Source>> {
    let mut file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(KMSG)
        .map_err(|err| io::Error::new(err.kind(), format!("{KMSG}: {err}")))?;
    // Only messages from now on; older ones are history, not news.
    file.seek(SeekFrom::End(0))?;
    let boot_id = read_trimmed(&roots.proc.join("sys/kernel/random/boot_id")).unwrap_or_default();
    Ok(Box::new(Kmsg { file, boot_id }))
}

impl Source for Kmsg {
    fn origin(&self) -> String {
        KMSG.to_owned()
    }

    fn collect(&mut self, _now: Instant, out: &mut Collected) -> io::Result<()> {
        let mut buf = vec![0u8; 8192];
        loop {
            // Each read returns exactly one record.
            match self.file.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let record = String::from_utf8_lossy(&buf[..n]);
                    out.events.extend(parse_kmsg(&record, &self.boot_id, clock::now_ms()));
                }
                Err(err) if err.kind() == io::ErrorKind::WouldBlock => break,
                // Records were overwritten before we read them; carry on.
                Err(err) if err.raw_os_error() == Some(libc::EPIPE) => continue,
                Err(err) => return Err(err),
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kernel_warnings_are_kept_and_chatter_dropped() {
        let oom = r#"{"__CURSOR":"s=1;i=2","__REALTIME_TIMESTAMP":"1761670000123456","PRIORITY":"3",
            "_TRANSPORT":"kernel","MESSAGE":"Out of memory: Killed process 4242 (stress-ng)"}"#;
        let (cursor, event) = parse_entry(&oom.replace('\n', "")).unwrap();
        let event = event.unwrap();
        assert_eq!(cursor, "s=1;i=2");
        assert_eq!((event.ts, event.source, event.priority), (1761670000123, "kernel", 3));
        assert!(event.message.starts_with("Out of memory"));

        let info = r#"{"__CURSOR":"c","__REALTIME_TIMESTAMP":"1","PRIORITY":"6","_TRANSPORT":"kernel","MESSAGE":"usb 3-1: new device"}"#;
        assert_eq!(parse_entry(info).unwrap().1, None);
        let trace = r#"{"__CURSOR":"c","__REALTIME_TIMESTAMP":"1","PRIORITY":"4","_TRANSPORT":"kernel","MESSAGE":" dump_stack_lvl+0x5d/0x80"}"#;
        assert_eq!(parse_entry(trace).unwrap().1, None);
    }

    #[test]
    fn storage_warnings_are_kept() {
        let nvme = r#"{"__CURSOR":"c","__REALTIME_TIMESTAMP":"1","PRIORITY":"4","_TRANSPORT":"kernel","MESSAGE":"nvme nvme0: I/O tag 12 (100c) opcode 0x2 (Read) QID 3 timeout, aborting"}"#;
        assert_eq!(parse_entry(nvme).unwrap().1.unwrap().priority, 4);
        assert!(parse_kmsg("4,9,1,-;ata1.00: failed command: READ FPDMA QUEUED", "b", 5).is_some());
        assert!(parse_kmsg("4,9,1,-;BTRFS warning (device nvme0n1p3): csum failed", "b", 5).is_some());
        assert!(parse_kmsg("4,9,1,-;atkbd serio0: Unknown key pressed", "b", 5).is_none());
        assert!(parse_kmsg("5,9,1,-;nvme nvme0: 16/0/0 default/read/poll queues", "b", 5).is_none());
    }

    #[test]
    fn unit_failures_carry_the_unit() {
        let line = r#"{"__CURSOR":"c","__REALTIME_TIMESTAMP":"1000","PRIORITY":"5","_TRANSPORT":"journal",
            "MESSAGE_ID":"d9b373ed55a64feb8242e02dbe79a49c","UNIT":"backup.service",
            "MESSAGE":"backup.service: Failed with result 'oom-kill'."}"#;
        let event = parse_entry(&line.replace('\n', "")).unwrap().1.unwrap();
        assert_eq!(event.source, "systemd");
        assert_eq!(event.unit.as_deref(), Some("backup.service"));
    }

    #[test]
    fn binary_messages_and_long_messages_are_handled() {
        let line = r#"{"__CURSOR":"c","__REALTIME_TIMESTAMP":"1000","PRIORITY":"3","_TRANSPORT":"kernel","MESSAGE":[104,105,255]}"#;
        assert_eq!(parse_entry(line).unwrap().1.unwrap().message, "hi\u{fffd}");
        assert_eq!(truncate("я".repeat(600)).len(), 1000);
    }

    #[test]
    fn kmsg_records_are_filtered_by_priority() {
        let event = parse_kmsg("3,812,9000000,-;nvme0n1: I/O error\n SUBSYSTEM=block\n", "b", 5).unwrap();
        assert_eq!(
            (event.priority, event.message.as_str(), event.key.as_str()),
            (3, "nvme0n1: I/O error", "kmsg:b:812")
        );
        assert!(parse_kmsg("6,813,9000001,-;usb 1-1: new device", "b", 5).is_none());
        assert!(parse_kmsg("4,814,9000002,-; dump_stack_lvl+0x5d/0x80", "b", 5).is_none());
    }
}
