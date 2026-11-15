//! Which control groups the actuator may touch (ADR 0062): a service or a
//! scope below the hierarchy root, never a slice, never a protected unit.

use std::path::{Path, PathBuf};

/// The directory of a group given by its path below `root`, or why it may
/// not be a target.
pub fn target(root: &Path, rel: &str, protected: &[String]) -> Result<PathBuf, String> {
    let parts: Vec<&str> = rel.split('/').collect();
    if rel.is_empty() || parts.iter().any(|p| p.is_empty() || *p == "." || *p == "..") {
        return Err(format!("'{rel}' is not a group path"));
    }
    let unit = parts[parts.len() - 1];
    if !(unit.ends_with(".service") || unit.ends_with(".scope")) {
        return Err(format!("{rel}: only a service or a scope is a target, never a slice"));
    }
    if let Some(pattern) = protected.iter().find(|p| glob(p, unit)) {
        return Err(format!("{rel}: {unit} is protected ({pattern})"));
    }
    let dir = root.join(rel);
    let real = dir.canonicalize().map_err(|e| format!("{rel}: {e}"))?;
    let real_root = root.canonicalize().map_err(|e| format!("{}: {e}", root.display()))?;
    if !real.starts_with(&real_root) || real == real_root || !real.is_dir() {
        return Err(format!("{rel}: not a group below {}", root.display()));
    }
    Ok(real)
}

/// Shell-style match with `*` only, the form of the ontology's patterns.
pub fn glob(pattern: &str, name: &str) -> bool {
    let (p, n) = (pattern.as_bytes(), name.as_bytes());
    let (mut i, mut j) = (0, 0);
    let mut star: Option<(usize, usize)> = None;
    while j < n.len() {
        if i < p.len() && p[i] == b'*' {
            star = Some((i, j));
            i += 1;
        } else if i < p.len() && p[i] == n[j] {
            i += 1;
            j += 1;
        } else if let Some((si, sj)) = star {
            i = si + 1;
            j = sj + 1;
            star = Some((si, sj + 1));
        } else {
            return false;
        }
    }
    p[i..].iter().all(|&b| b == b'*')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn protected() -> Vec<String> {
        vec!["user@*.service".into(), "init.scope".into()]
    }

    #[test]
    fn glob_matches_like_the_ontology() {
        assert!(glob("user@*.service", "user@1000.service"));
        assert!(glob("gungnir-actuato*", "gungnir-actuator"));
        assert!(glob("dbus-broker*", "dbus-broker"));
        assert!(!glob("user@*.service", "user-1000.slice"));
        assert!(!glob("init.scope", "init.scopes"));
    }

    #[test]
    fn only_services_and_scopes_that_exist_are_targets() {
        let root = std::env::temp_dir().join(format!("gungnir-actuator-groups-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("user.slice/user@1000.service/app.slice/app-cp.scope")).unwrap();
        std::fs::create_dir_all(root.join("init.scope")).unwrap();
        let p = protected();
        assert!(target(&root, "user.slice/user@1000.service/app.slice/app-cp.scope", &p).is_ok());
        assert!(target(&root, "user.slice", &p).unwrap_err().contains("never a slice"));
        assert!(target(&root, "user.slice/user@1000.service", &p).unwrap_err().contains("protected"));
        assert!(target(&root, "init.scope", &p).unwrap_err().contains("protected"));
        assert!(target(&root, "user.slice/../init.scope", &p).is_err());
        assert!(target(&root, "/etc/x.service", &p).is_err());
        assert!(target(&root, "system.slice/gone.service", &p).is_err());
        std::fs::remove_dir_all(&root).unwrap();
    }
}
