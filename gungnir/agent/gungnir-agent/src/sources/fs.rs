//! Filesystem fullness via `statvfs(3)` for each block-device mount.

use std::collections::HashSet;
use std::io;
use std::path::PathBuf;
use std::time::Instant;

use super::{Reading, Roots, Source, read};

/// Read-only image formats are always full and say nothing about the host.
const SKIP_TYPES: &[&str] = &["squashfs", "iso9660", "udf", "erofs"];

/// Mount points of block-device filesystems, one per device. Btrfs
/// subvolumes mount one device several times; the first mount stands for all.
fn mount_points(text: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut points = Vec::new();
    for line in text.lines() {
        let mut fields = line.split_ascii_whitespace();
        let (Some(device), Some(point), Some(fstype)) = (fields.next(), fields.next(), fields.next()) else {
            continue;
        };
        if device.starts_with("/dev/") && !SKIP_TYPES.contains(&fstype) && seen.insert(device) {
            points.push(unescape(point));
        }
    }
    points
}

/// The kernel writes space, tab, newline and backslash in mount points as
/// three-digit octal escapes.
fn unescape(field: &str) -> String {
    let bytes = field.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let octal = i + 3 < bytes.len()
            && (b'0'..=b'3').contains(&bytes[i + 1])
            && bytes[i + 2..i + 4].iter().all(|b| (b'0'..=b'7').contains(b));
        if bytes[i] == b'\\' && octal {
            out.push((bytes[i + 1] - b'0') * 64 + (bytes[i + 2] - b'0') * 8 + (bytes[i + 3] - b'0'));
            i += 4;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

pub struct Mounts {
    path: PathBuf,
}

pub fn open_mounts(roots: &Roots, now: Instant) -> io::Result<Box<dyn Source>> {
    let mut source = Mounts { path: roots.proc.join("self/mounts") };
    source.collect(now, &mut Vec::new())?;
    Ok(Box::new(source))
}

impl Source for Mounts {
    fn origin(&self) -> String {
        format!("{} + statvfs(3)", self.path.display())
    }

    fn collect(&mut self, _now: Instant, out: &mut Vec<Reading>) -> io::Result<()> {
        for point in mount_points(&read(&self.path)?) {
            // A mount that vanished or is hidden from this process is skipped,
            // not treated as a failure of the whole source.
            let Ok(st) = rustix::fs::statvfs(point.as_str()) else { continue };
            let used = st.f_blocks.saturating_sub(st.f_bfree);
            // As df: the share of space usable by ordinary users that is taken.
            let usable = used + st.f_bavail;
            let push = |signal, value| Reading { signal, entity: point.clone(), value };
            if usable > 0 {
                out.push(push("fs.used_ratio", used as f64 / usable as f64));
            }
            out.push(push("fs.available_bytes", (st.f_bavail * st.f_frsize) as f64));
            // Btrfs and some others have no fixed inode table and report zero.
            if st.f_files > 0 {
                let inodes_used = st.f_files.saturating_sub(st.f_ffree);
                out.push(push("fs.inodes_used_ratio", inodes_used as f64 / st.f_files as f64));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_mount_per_device_and_no_pseudo_filesystems() {
        let text = "\
/dev/sda2 / btrfs rw,subvol=/ 0 0
proc /proc proc rw 0 0
/dev/sda2 /home btrfs rw,subvol=/home 0 0
/dev/sda1 /boot vfat rw 0 0
/dev/loop0 /snap/core/1 squashfs ro 0 0
/dev/sdb1 /media/my\\040disk ext4 rw 0 0
";
        assert_eq!(mount_points(text), vec!["/", "/boot", "/media/my disk"]);
    }

    #[test]
    fn reports_root_filesystem() {
        let mut source = Mounts { path: PathBuf::from("/proc/self/mounts") };
        let mut out = Vec::new();
        source.collect(Instant::now(), &mut out).unwrap();
        let root = out.iter().find(|r| r.signal == "fs.used_ratio" && r.entity == "/").unwrap();
        assert!((0.0..=1.0).contains(&root.value));
    }
}
