//! Commands, the changes the actuator holds and their log (ADRs 0061, 0062).
//! Every change is undone by its timer unless kept; what the actuator holds
//! is on disk, so a restart finds it and undoes what a crash left.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use log::{info, warn};
use serde::{Deserialize, Serialize};

use crate::actions::{self, FileChange};
use crate::config::Config;
use crate::groups;

/// Log entries and handled ids kept for the agent to read back.
const KEEP: usize = 1000;

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "lowercase", deny_unknown_fields)]
pub enum Request {
    Apply {
        id: u64,
        action: String,
        groups: Vec<String>,
        #[serde(default)]
        value: Option<f64>,
        #[serde(default)]
        share: Option<f64>,
        revert_after_s: u64,
    },
    Revert {
        id: u64,
        reason: String,
    },
    Keep {
        id: u64,
    },
    /// Log entries numbered after `after`.
    Log {
        after: u64,
    },
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct Response {
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub entries: Option<Vec<Entry>>,
}

impl Response {
    fn ok() -> Self {
        Self { ok: true, error: None, entries: None }
    }

    fn refused(error: String) -> Self {
        Self { ok: false, error: Some(error), entries: None }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Event {
    Applied,
    Refused,
    Reverted,
    Kept,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Entry {
    pub seq: u64,
    pub id: u64,
    /// Milliseconds since the Unix epoch.
    pub at: i64,
    pub event: Event,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub files: Vec<FileChange>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Change {
    id: u64,
    action: String,
    files: Vec<FileChange>,
    applied_at: i64,
    /// When the timer undoes it; none once kept.
    deadline: Option<i64>,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct State {
    boot_id: String,
    seq: u64,
    changes: Vec<Change>,
    log: VecDeque<Entry>,
    /// Ids already handled, so that a repeated command changes nothing.
    seen: VecDeque<u64>,
}

pub struct Actuator {
    root: PathBuf,
    protected: Vec<String>,
    max_revert_after_s: u64,
    path: PathBuf,
    state: State,
}

impl Actuator {
    /// Loads what an earlier run held. After a reboot the groups are new and
    /// nothing is written; after a crash every change not kept is undone.
    pub fn open(config: &Config, state_dir: &Path, boot_id: &str) -> Result<Self, String> {
        let path = state_dir.join("state.json");
        let state = match std::fs::read(&path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", path.display()))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => State::default(),
            Err(e) => return Err(format!("{}: {e}", path.display())),
        };
        let mut a = Actuator {
            root: config.cgroup_root.clone(),
            protected: config.protected_units.clone(),
            max_revert_after_s: config.max_revert_after_secs,
            path,
            state,
        };
        if a.state.boot_id != boot_id {
            for c in std::mem::take(&mut a.state.changes) {
                a.log(c.id, Event::Reverted, Some("the host rebooted".into()), Vec::new());
            }
            a.state.boot_id = boot_id.to_owned();
        } else {
            let left: Vec<u64> =
                a.state.changes.iter().filter(|c| c.deadline.is_some()).map(|c| c.id).collect();
            for id in left {
                a.revert(id, "the actuator restarted");
            }
        }
        a.save()?;
        Ok(a)
    }

    pub fn handle(&mut self, request: Request, now: i64) -> Response {
        let response = match request {
            Request::Apply { id, action, groups, value, share, revert_after_s } => {
                self.apply(id, &action, &groups, value, share, revert_after_s, now)
            }
            Request::Revert { id, reason } => {
                if !self.revert(id, &reason) && !self.state.seen.contains(&id) {
                    // Nothing held under this id: say so once, so the server stops asking.
                    self.log(id, Event::Reverted, Some("nothing held under this id".into()), Vec::new());
                    self.seen(id);
                }
                Response::ok()
            }
            Request::Keep { id } => match self.state.changes.iter_mut().find(|c| c.id == id) {
                Some(c) => {
                    c.deadline = None;
                    info!("change {id} kept until its groups end or the host reboots");
                    self.log(id, Event::Kept, None, Vec::new());
                    Response::ok()
                }
                None => Response::refused(format!("no change {id} is held")),
            },
            Request::Log { after } => {
                let entries = self.state.log.iter().filter(|e| e.seq > after).cloned().collect();
                return Response { ok: true, error: None, entries: Some(entries) };
            }
        };
        if let Err(err) = self.save() {
            warn!("cannot save the state: {err}");
        }
        response
    }

    #[allow(clippy::too_many_arguments)]
    fn apply(
        &mut self,
        id: u64,
        action: &str,
        names: &[String],
        value: Option<f64>,
        share: Option<f64>,
        revert_after_s: u64,
        now: i64,
    ) -> Response {
        if self.state.seen.contains(&id) {
            return Response::ok();
        }
        match self.plan(action, names, value, share, revert_after_s) {
            Err(reason) => {
                warn!("change {id} refused: {action} on {}: {reason}", names.join(", "));
                self.refuse(id, reason)
            }
            Ok(files) => {
                for (i, f) in files.iter().enumerate() {
                    if let Err(err) = actions::apply(&self.root, f) {
                        for done in files[..i].iter().rev() {
                            let _ = actions::revert(&self.root, done);
                        }
                        warn!("change {id} failed and was undone: {err}");
                        return self.refuse(id, err);
                    }
                }
                for f in &files {
                    info!("change {id}: {action} {} {} {} -> {}", f.group, show(f), f.before, f.after);
                }
                let deadline = now + revert_after_s as i64 * 1000;
                self.state.changes.push(Change {
                    id,
                    action: action.to_owned(),
                    files: files.clone(),
                    applied_at: now,
                    deadline: Some(deadline),
                });
                self.log(id, Event::Applied, None, files);
                self.seen(id);
                Response::ok()
            }
        }
    }

    fn plan(
        &self,
        action: &str,
        names: &[String],
        value: Option<f64>,
        share: Option<f64>,
        revert_after_s: u64,
    ) -> Result<Vec<FileChange>, String> {
        let kind =
            actions::kind(action).ok_or_else(|| format!("'{action}' is not an action of the actuator"))?;
        if revert_after_s == 0 || revert_after_s > self.max_revert_after_s {
            return Err(format!("the timer must be 1..={} s", self.max_revert_after_s));
        }
        if names.is_empty() {
            return Err("no group of that name runs on the host".into());
        }
        let mut files = Vec::new();
        for name in names {
            let dir = groups::target(&self.root, name, &self.protected)?;
            files.extend(actions::plan(kind, name, &dir, value, share)?);
        }
        for f in &files {
            if let Some(c) =
                self.state.changes.iter().find(|c| {
                    c.files.iter().any(|g| g.group == f.group && g.file == f.file && g.key == f.key)
                })
            {
                return Err(format!("{} {} is already changed by change {}", f.group, show(f), c.id));
            }
        }
        Ok(files)
    }

    fn refuse(&mut self, id: u64, reason: String) -> Response {
        self.log(id, Event::Refused, Some(reason.clone()), Vec::new());
        self.seen(id);
        Response::refused(reason)
    }

    /// Undoes a change held under `id`; false when none is.
    fn revert(&mut self, id: u64, reason: &str) -> bool {
        let Some(at) = self.state.changes.iter().position(|c| c.id == id) else { return false };
        let change = self.state.changes.remove(at);
        let mut left = 0;
        for f in change.files.iter().rev() {
            if !actions::holds(&self.root, f) {
                left += 1;
                warn!("change {id}: {} {} was changed by someone else, left as it is", f.group, show(f));
                continue;
            }
            match actions::revert(&self.root, f) {
                Ok(()) => info!("change {id} reverted ({reason}): {} {} -> {}", f.group, show(f), f.before),
                Err(err) => warn!("change {id}: cannot revert {}: {err}", f.group),
            }
        }
        let reason = match left {
            0 => reason.to_owned(),
            n => format!("{reason}; {n} file(s) changed by someone else were left as they were"),
        };
        self.log(id, Event::Reverted, Some(reason), Vec::new());
        true
    }

    /// Runs the timers and forgets changes whose groups ended. Returns
    /// whether anything changed.
    pub fn tick(&mut self, now: i64) -> bool {
        let due: Vec<u64> = self
            .state
            .changes
            .iter()
            .filter(|c| c.deadline.is_some_and(|d| d <= now))
            .map(|c| c.id)
            .collect();
        let gone: Vec<u64> = self
            .state
            .changes
            .iter()
            .filter(|c| c.files.iter().all(|f| !self.root.join(&f.group).is_dir()))
            .map(|c| c.id)
            .collect();
        for &id in &due {
            self.revert(id, "the timer ran out");
        }
        for &id in &gone {
            if let Some(at) = self.state.changes.iter().position(|c| c.id == id) {
                self.state.changes.remove(at);
                info!("change {id}: its groups ended");
                self.log(id, Event::Reverted, Some("its groups ended".into()), Vec::new());
            }
        }
        let changed = !due.is_empty() || !gone.is_empty();
        if changed && let Err(err) = self.save() {
            warn!("cannot save the state: {err}");
        }
        changed
    }

    /// On a clean stop every change not kept is undone.
    pub fn stop(&mut self) {
        let left: Vec<u64> =
            self.state.changes.iter().filter(|c| c.deadline.is_some()).map(|c| c.id).collect();
        for id in left {
            self.revert(id, "the actuator stopped");
        }
        if let Err(err) = self.save() {
            warn!("cannot save the state: {err}");
        }
    }

    pub fn held(&self) -> usize {
        self.state.changes.len()
    }

    fn log(&mut self, id: u64, event: Event, reason: Option<String>, files: Vec<FileChange>) {
        self.state.seq += 1;
        self.state.log.push_back(Entry { seq: self.state.seq, id, at: now_ms(), event, reason, files });
        while self.state.log.len() > KEEP {
            self.state.log.pop_front();
        }
    }

    fn seen(&mut self, id: u64) {
        self.state.seen.push_back(id);
        while self.state.seen.len() > KEEP {
            self.state.seen.pop_front();
        }
    }

    fn save(&self) -> Result<(), String> {
        let tmp = self.path.with_extension("json.tmp");
        let bytes = serde_json::to_vec(&self.state).map_err(|e| e.to_string())?;
        std::fs::write(&tmp, bytes).map_err(|e| format!("{}: {e}", tmp.display()))?;
        std::fs::rename(&tmp, &self.path).map_err(|e| format!("{}: {e}", self.path.display()))
    }
}

fn show(f: &FileChange) -> String {
    match &f.key {
        Some(key) => format!("{} {key}", f.file),
        None => f.file.clone(),
    }
}

pub fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_millis() as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    const GROUP: &str = "user.slice/app.slice/app-cp.scope";

    fn setup(name: &str) -> (Config, PathBuf) {
        let dir = std::env::temp_dir().join(format!("gungnir-actuator-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let group = dir.join("cgroup").join(GROUP);
        std::fs::create_dir_all(&group).unwrap();
        std::fs::create_dir_all(dir.join("state")).unwrap();
        std::fs::write(group.join("io.stat"), "259:0 rbytes=0 wbytes=800000000 rios=0 wios=900\n").unwrap();
        std::fs::write(group.join("io.max"), "").unwrap();
        std::fs::write(group.join("memory.current"), "2147483648\n").unwrap();
        std::fs::write(group.join("memory.high"), "max\n").unwrap();
        let config = Config {
            cgroup_root: dir.join("cgroup"),
            state_dir: Some(dir.join("state")),
            ..Config::default()
        };
        (config, dir)
    }

    fn apply(id: u64, action: &str, value: Option<f64>) -> Request {
        Request::Apply {
            id,
            action: action.into(),
            groups: vec![GROUP.into()],
            value,
            share: None,
            revert_after_s: 1800,
        }
    }

    fn io_max(dir: &Path) -> String {
        std::fs::read_to_string(dir.join("cgroup").join(GROUP).join("io.max")).unwrap()
    }

    fn events(a: &mut Actuator) -> Vec<(u64, Event)> {
        let log = a.handle(Request::Log { after: 0 }, 0).entries.unwrap();
        log.iter().map(|e| (e.id, e.event)).collect()
    }

    #[test]
    fn a_change_is_applied_once_and_undone_by_its_timer() {
        let (config, dir) = setup("timer");
        let mut a = Actuator::open(&config, &dir.join("state"), "boot-1").unwrap();
        assert!(a.handle(apply(7, "io_max_wbps", Some(40e6)), 1_000).ok);
        assert_eq!(io_max(&dir), "259:0 wbps=40000000");
        // The server repeats the command until it hears back: nothing more happens.
        assert!(a.handle(apply(7, "io_max_wbps", Some(10e6)), 2_000).ok);
        assert_eq!(io_max(&dir), "259:0 wbps=40000000");
        assert!(!a.tick(1_000 + 1_799_000));
        assert!(a.tick(1_000 + 1_800_000));
        assert_eq!(io_max(&dir), "259:0 wbps=max");
        assert_eq!(events(&mut a), vec![(7, Event::Applied), (7, Event::Reverted)]);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn refusals_are_logged_and_nothing_is_written() {
        let (config, dir) = setup("refuse");
        let mut a = Actuator::open(&config, &dir.join("state"), "boot-1").unwrap();
        let r = a.handle(apply(1, "kill", None), 0);
        assert!(!r.ok && r.error.unwrap().contains("not an action"));
        let mut slice = apply(2, "io_max_wbps", Some(40e6));
        if let Request::Apply { groups, .. } = &mut slice {
            *groups = vec!["user.slice".into()];
        }
        assert!(!a.handle(slice, 0).ok);
        assert!(!a.handle(apply(3, "memory_high", Some(1e6)), 0).ok);
        assert_eq!(io_max(&dir), "");
        assert_eq!(a.held(), 0);
        assert_eq!(events(&mut a).iter().filter(|(_, e)| *e == Event::Refused).count(), 3);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_kept_change_survives_a_restart_and_the_rest_is_undone() {
        let (config, dir) = setup("restart");
        let state = dir.join("state");
        {
            let mut a = Actuator::open(&config, &state, "boot-1").unwrap();
            assert!(a.handle(apply(1, "io_max_wbps", Some(40e6)), 0).ok);
            assert!(a.handle(apply(2, "memory_high", None).with_share(1.0), 0).ok);
            assert!(a.handle(Request::Keep { id: 2 }, 0).ok);
            // A crash: no stop.
        }
        let mut a = Actuator::open(&config, &state, "boot-1").unwrap();
        assert_eq!(io_max(&dir), "259:0 wbps=max");
        assert_eq!(a.held(), 1);
        a.stop();
        assert_eq!(a.held(), 1);
        let mut after_reboot = Actuator::open(&config, &state, "boot-2").unwrap();
        assert_eq!(after_reboot.held(), 0);
        let last = events(&mut after_reboot).pop().unwrap();
        assert_eq!(last, (2, Event::Reverted));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_value_someone_else_changed_is_left_alone() {
        let (config, dir) = setup("other");
        let mut a = Actuator::open(&config, &dir.join("state"), "boot-1").unwrap();
        assert!(a.handle(apply(1, "io_max_wbps", Some(40e6)), 0).ok);
        std::fs::write(dir.join("cgroup").join(GROUP).join("io.max"), "259:0 wbps=5000000").unwrap();
        assert!(a.handle(Request::Revert { id: 1, reason: "operator".into() }, 0).ok);
        assert_eq!(io_max(&dir), "259:0 wbps=5000000");
        // An id the actuator never held is answered once, so the server stops asking.
        assert!(a.handle(Request::Revert { id: 9, reason: "operator".into() }, 0).ok);
        assert!(a.handle(Request::Revert { id: 9, reason: "operator".into() }, 0).ok);
        assert_eq!(events(&mut a).iter().filter(|(id, _)| *id == 9).count(), 1);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn one_file_is_changed_by_one_change_at_a_time() {
        let (config, dir) = setup("twice");
        let mut a = Actuator::open(&config, &dir.join("state"), "boot-1").unwrap();
        assert!(a.handle(apply(1, "io_max_wbps", Some(40e6)), 0).ok);
        let r = a.handle(apply(2, "io_max_wbps", Some(20e6)), 0);
        assert!(r.error.unwrap().contains("already changed by change 1"));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    impl Request {
        fn with_share(mut self, s: f64) -> Self {
            if let Request::Apply { share, .. } = &mut self {
                *share = Some(s);
            }
            self
        }
    }
}
