use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Deserialize;

use crate::sources::Roots;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Config {
    /// Base URL of the core, e.g. `http://127.0.0.1:8080`.
    pub core_url: String,
    /// File holding the agent's token. Defaults to the systemd credential
    /// `token` (see `LoadCredential=` in the unit).
    pub token_file: Option<PathBuf>,
    pub sample_interval_secs: u64,
    pub send_interval_secs: u64,
    /// Rows kept in memory while the core is unreachable and the spool is
    /// unavailable; the oldest go first.
    pub buffer_capacity: usize,
    /// Rows per batch: samples, facts, process rows and events together.
    pub max_batch: usize,
    /// Where the spool and the journal position live. Defaults to the
    /// directory systemd provides through `StateDirectory=`; without one the
    /// agent buffers in memory only.
    pub state_dir: Option<PathBuf>,
    /// Size limit of the spool on disk.
    pub spool_max_bytes: u64,
    pub request_timeout_secs: u64,
    pub proc_root: PathBuf,
    pub sys_root: PathBuf,
    pub cgroup_root: PathBuf,
    /// Name prefixes of block devices that are never reported.
    pub disk_exclude: Vec<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            core_url: "http://127.0.0.1:8080".into(),
            token_file: None,
            sample_interval_secs: 5,
            send_interval_secs: 10,
            buffer_capacity: 50_000,
            max_batch: 5_000,
            state_dir: None,
            spool_max_bytes: 100 * 1024 * 1024,
            request_timeout_secs: 5,
            proc_root: "/proc".into(),
            sys_root: "/sys".into(),
            cgroup_root: "/sys/fs/cgroup".into(),
            disk_exclude: vec!["loop".into(), "ram".into()],
        }
    }
}

impl Config {
    pub fn load(path: Option<&Path>) -> Result<Self, String> {
        let mut config: Config = match path {
            None => Config::default(),
            Some(path) => {
                let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
                toml::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?
            }
        };
        if config.state_dir.is_none() {
            config.state_dir = std::env::var_os("STATE_DIRECTORY").map(PathBuf::from);
        }
        if config.sample_interval_secs == 0 || config.send_interval_secs == 0 {
            return Err("intervals must be at least one second".into());
        }
        if config.max_batch == 0 || config.buffer_capacity < config.max_batch {
            return Err("need 0 < max_batch <= buffer_capacity".into());
        }
        Ok(config)
    }

    pub fn sample_interval(&self) -> Duration {
        Duration::from_secs(self.sample_interval_secs)
    }

    pub fn send_interval(&self) -> Duration {
        Duration::from_secs(self.send_interval_secs)
    }

    pub fn roots(&self) -> Roots {
        Roots {
            proc: self.proc_root.clone(),
            sys: self.sys_root.clone(),
            cgroup: self.cgroup_root.clone(),
            disk_exclude: self.disk_exclude.clone(),
            state: self.state_dir.clone(),
        }
    }

    pub fn read_token(&self) -> Result<String, String> {
        let path = match &self.token_file {
            Some(path) => path.clone(),
            None => std::env::var_os("CREDENTIALS_DIRECTORY")
                .map(|dir| PathBuf::from(dir).join("token"))
                .ok_or("no token_file in the config and no systemd credential 'token'")?,
        };
        let token = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let token = token.trim();
        if token.is_empty() {
            return Err(format!("{}: empty token", path.display()));
        }
        Ok(token.to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn partial_file_keeps_defaults() {
        let config: Config = toml::from_str("core_url = \"http://core:8080\"").unwrap();
        assert_eq!(config.core_url, "http://core:8080");
        assert_eq!(config.sample_interval_secs, 5);
    }

    #[test]
    fn unknown_keys_are_rejected() {
        assert!(toml::from_str::<Config>("sample_intreval_secs = 1").is_err());
    }
}
