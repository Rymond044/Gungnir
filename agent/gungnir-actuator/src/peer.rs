//! Who is on the other end of the socket (ADR 0062): a process of the
//! agent's unit, told by the kernel, not by anything the peer says.

use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::path::Path;

pub fn check(stream: &UnixStream, client_unit: &str, proc_root: &Path) -> Result<(), String> {
    let cred = credentials(stream)?;
    if client_unit.is_empty() {
        // SAFETY: geteuid has no preconditions.
        let me = unsafe { libc::geteuid() };
        return if cred.uid == me {
            Ok(())
        } else {
            Err(format!("uid {} is not the actuator's own user {me}", cred.uid))
        };
    }
    let path = proc_root.join(cred.pid.to_string()).join("cgroup");
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    if in_unit(&text, client_unit) {
        Ok(())
    } else {
        Err(format!("pid {} is not a process of {client_unit}", cred.pid))
    }
}

/// Whether a `/proc/<pid>/cgroup` of the unified hierarchy places the
/// process in the unit or below it.
fn in_unit(text: &str, unit: &str) -> bool {
    text.lines().filter_map(|l| l.strip_prefix("0::")).any(|path| path.split('/').any(|p| p == unit))
}

fn credentials(stream: &UnixStream) -> Result<libc::ucred, String> {
    let mut cred = libc::ucred { pid: 0, uid: 0, gid: 0 };
    let mut len = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: the buffer and its length describe `cred`, which outlives the call.
    let rc = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&mut cred as *mut libc::ucred).cast(),
            &mut len,
        )
    };
    if rc != 0 {
        return Err(format!("SO_PEERCRED: {}", std::io::Error::last_os_error()));
    }
    Ok(cred)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_processes_of_the_unit_pass() {
        assert!(in_unit("0::/system.slice/gungnir-agent.service\n", "gungnir-agent.service"));
        assert!(!in_unit("0::/system.slice/gungnir-agent.service.evil\n", "gungnir-agent.service"));
        assert!(!in_unit("0::/user.slice/user-1000.slice/session-2.scope\n", "gungnir-agent.service"));
    }

    #[test]
    fn the_own_user_passes_without_a_unit() {
        let (a, _b) = UnixStream::pair().unwrap();
        assert!(check(&a, "", Path::new("/proc")).is_ok());
    }
}
