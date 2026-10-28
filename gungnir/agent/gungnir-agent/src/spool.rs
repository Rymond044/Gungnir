//! Spool on disk for data the core could not take. When delivery fails, the
//! oldest data leaves memory for a file here, so a long outage of the core
//! costs disk space instead of the agent's memory, and a restart of the
//! agent loses nothing. Files are sent back oldest first once the core
//! answers again.
//!
//! Each file is one batch body in JSON, named `<sequence>-<rows>.json` so
//! that the spool can be accounted for without reading it. A file is
//! written under a temporary name and renamed, so a crash never leaves half
//! a batch behind. Beyond the size limit the oldest files are removed.

use std::collections::VecDeque;
use std::io;
use std::path::{Path, PathBuf};

struct Entry {
    path: PathBuf,
    bytes: u64,
    rows: u64,
}

pub struct Spool {
    dir: PathBuf,
    max_bytes: u64,
    entries: VecDeque<Entry>,
    bytes: u64,
    next: u64,
}

fn parse_name(name: &str) -> Option<(u64, u64)> {
    let (seq, rows) = name.strip_suffix(".json")?.split_once('-')?;
    Some((seq.parse().ok()?, rows.parse().ok()?))
}

impl Spool {
    /// Opens the spool directory, creating it, and takes stock of files
    /// left by an earlier run.
    pub fn open(dir: &Path, max_bytes: u64) -> io::Result<Self> {
        std::fs::create_dir_all(dir)?;
        let mut found = Vec::new();
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            match parse_name(&name) {
                Some((seq, rows)) => found.push((seq, rows, entry.path(), entry.metadata()?.len())),
                // Leftovers of an interrupted write.
                None if name.ends_with(".tmp") => std::fs::remove_file(entry.path())?,
                None => {}
            }
        }
        found.sort_by_key(|f| f.0);
        let next = found.last().map_or(1, |f| f.0 + 1);
        let entries: VecDeque<Entry> =
            found.into_iter().map(|(_, rows, path, bytes)| Entry { path, bytes, rows }).collect();
        let bytes = entries.iter().map(|e| e.bytes).sum();
        Ok(Self { dir: dir.to_owned(), max_bytes, entries, bytes, next })
    }

    /// Stores one batch body. Returns the rows of old files removed to stay
    /// within the size limit.
    pub fn put(&mut self, body: &[u8], rows: u64) -> io::Result<u64> {
        let path = self.dir.join(format!("{:012}-{rows}.json", self.next));
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, body)?;
        std::fs::rename(&tmp, &path)?;
        self.next += 1;
        self.bytes += body.len() as u64;
        self.entries.push_back(Entry { path, bytes: body.len() as u64, rows });

        let mut dropped = 0;
        while self.bytes > self.max_bytes && self.entries.len() > 1 {
            dropped += self.remove_oldest()?;
        }
        Ok(dropped)
    }

    /// The oldest stored body.
    pub fn oldest(&self) -> Option<io::Result<Vec<u8>>> {
        self.entries.front().map(|e| std::fs::read(&e.path))
    }

    /// Removes the oldest file; returns its rows.
    pub fn remove_oldest(&mut self) -> io::Result<u64> {
        let Some(entry) = self.entries.pop_front() else { return Ok(0) };
        self.bytes -= entry.bytes;
        std::fs::remove_file(&entry.path)?;
        Ok(entry.rows)
    }

    pub fn rows(&self) -> u64 {
        self.entries.iter().map(|e| e.rows).sum()
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("gungnir-spool-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn survives_a_restart_in_order() {
        let dir = dir("restart");
        let mut spool = Spool::open(&dir, 1 << 20).unwrap();
        spool.put(b"{\"a\":1}", 3).unwrap();
        spool.put(b"{\"b\":2}", 4).unwrap();
        std::fs::write(dir.join("000000000009-1.tmp"), b"half").unwrap();

        let mut spool = Spool::open(&dir, 1 << 20).unwrap();
        assert_eq!(spool.rows(), 7);
        assert_eq!(spool.oldest().unwrap().unwrap(), b"{\"a\":1}");
        assert_eq!(spool.remove_oldest().unwrap(), 3);
        spool.put(b"{\"c\":3}", 1).unwrap();
        assert_eq!(spool.oldest().unwrap().unwrap(), b"{\"b\":2}");
        assert!(!dir.join("000000000009-1.tmp").exists());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn size_limit_removes_oldest_files() {
        let dir = dir("limit");
        let mut spool = Spool::open(&dir, 25).unwrap();
        assert_eq!(spool.put(&[b'x'; 10], 5).unwrap(), 0);
        assert_eq!(spool.put(&[b'y'; 10], 6).unwrap(), 0);
        assert_eq!(spool.put(&[b'z'; 10], 7).unwrap(), 5);
        assert_eq!(spool.rows(), 13);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
