use std::path::{Path, PathBuf};

use serde::Deserialize;

/// Units no action may target, whatever the server asks: the ontology's
/// protected entities (ADR 0035), kept here too so that the actuator does not
/// depend on the server for its own safety (ADR 0062).
const PROTECTED: &[&str] = &[
    "init.scope",
    "systemd-journald.service",
    "dbus.service",
    "dbus-broker.service",
    "systemd-logind.service",
    "user@*.service",
    "gungnir-agent.service",
    "gungnir-actuator.service",
];

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Config {
    pub socket: PathBuf,
    /// Group owning the socket; the agent's unit is a member.
    pub socket_group: String,
    /// The unit whose processes may send commands; empty accepts any process
    /// of the actuator's own user, for trying it without privileges.
    pub client_unit: String,
    /// Changes and their log. Defaults to systemd's `StateDirectory=`.
    pub state_dir: Option<PathBuf>,
    pub cgroup_root: PathBuf,
    pub proc_root: PathBuf,
    /// The longest timer a change may have.
    pub max_revert_after_secs: u64,
    pub protected_units: Vec<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            socket: "/run/gungnir-actuator/actuator.sock".into(),
            socket_group: "gungnir".into(),
            client_unit: "gungnir-agent.service".into(),
            state_dir: None,
            cgroup_root: "/sys/fs/cgroup".into(),
            proc_root: "/proc".into(),
            max_revert_after_secs: 30 * 60,
            protected_units: PROTECTED.iter().map(|s| (*s).to_owned()).collect(),
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
        if config.state_dir.is_none() {
            return Err("no state_dir in the config and no STATE_DIRECTORY from systemd".into());
        }
        if config.max_revert_after_secs == 0 || config.max_revert_after_secs > 30 * 60 {
            return Err("max_revert_after_secs must be 1..=1800 (ADR 0062)".into());
        }
        Ok(config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_protected_list_cannot_be_misspelt() {
        let config: Config = toml::from_str("socket = \"/tmp/a.sock\"").unwrap();
        assert!(config.protected_units.iter().any(|u| u == "gungnir-agent.service"));
        assert!(toml::from_str::<Config>("protected = []").is_err());
    }
}
