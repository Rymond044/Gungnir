//! Passing the server's commands to the actuator and its log back to the
//! server (ADR 0061). Commands come in the answer to a batch; the actuator's
//! log entries go out in the header of the next one, and the agent moves past
//! them only once the server accepted that batch.

use std::collections::HashSet;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

use log::warn;
use serde::Deserialize;
use serde_json::{Value, json};

use crate::clock;
use crate::collectors::groups_named;

const TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Op {
    Apply,
    Revert,
    Keep,
}

#[derive(Debug, Deserialize)]
pub struct Command {
    pub id: u64,
    pub op: Op,
    #[serde(default)]
    pub action: Option<String>,
    /// The culprit as the series name it (a folded group name).
    #[serde(default)]
    pub target: Option<String>,
    #[serde(default)]
    pub value: Option<f64>,
    #[serde(default)]
    pub share: Option<f64>,
    #[serde(default)]
    pub revert_after_s: Option<u64>,
    #[serde(default)]
    pub reason: Option<String>,
}

pub struct Actuation {
    socket: Option<PathBuf>,
    cgroup_root: PathBuf,
    /// The last log number the server accepted.
    delivered: u64,
    /// Refusals the agent makes itself when the actuator cannot be reached.
    own: Vec<Value>,
    refused: HashSet<u64>,
    unreachable: bool,
}

impl Actuation {
    pub fn new(socket: Option<PathBuf>, cgroup_root: PathBuf) -> Self {
        Self {
            socket,
            cgroup_root,
            delivered: 0,
            own: Vec::new(),
            refused: HashSet::new(),
            unreachable: false,
        }
    }

    /// What the server has not heard yet: the agent's own refusals and the
    /// actuator's log past the last delivered number.
    pub fn pending(&mut self) -> Vec<Value> {
        let mut out = self.own.clone();
        if self.socket.is_some()
            && let Ok(answer) = self.request(&json!({ "op": "log", "after": self.delivered }))
            && let Some(Value::Array(entries)) = answer.get("entries")
        {
            out.extend(entries.iter().cloned());
        }
        out
    }

    /// The server accepted a batch carrying `sent`.
    pub fn delivered(&mut self, sent: &[Value]) {
        if let Some(seq) = sent.iter().filter_map(|v| v.get("seq").and_then(Value::as_u64)).max() {
            self.delivered = self.delivered.max(seq);
        }
        self.own.retain(|v| !sent.contains(v));
    }

    pub fn run(&mut self, commands: Vec<Command>) {
        for c in commands {
            let request = match c.op {
                Op::Apply => {
                    let target = c.target.as_deref().unwrap_or_default();
                    json!({
                        "op": "apply",
                        "id": c.id,
                        "action": c.action,
                        "groups": groups_named(&self.cgroup_root, target),
                        "value": c.value,
                        "share": c.share,
                        "revert_after_s": c.revert_after_s.unwrap_or(0),
                    })
                }
                Op::Revert => json!({ "op": "revert", "id": c.id, "reason": c.reason.unwrap_or_default() }),
                Op::Keep => json!({ "op": "keep", "id": c.id }),
            };
            match self.request(&request) {
                Ok(answer) => {
                    if answer.get("ok") != Some(&Value::Bool(true)) {
                        warn!("actuator: command {} ({:?}) not carried out: {}", c.id, c.op, answer["error"]);
                    }
                }
                Err(reason) => {
                    // A revert or keep is asked again; the actuator's own timer
                    // runs meanwhile. An apply that cannot be delivered is refused.
                    if c.op == Op::Apply && self.refused.insert(c.id) {
                        self.own.push(json!({
                            "id": c.id,
                            "at": clock::now_ms(),
                            "event": "refused",
                            "reason": format!("the agent cannot reach the actuator: {reason}"),
                        }));
                    }
                }
            }
        }
    }

    fn request(&mut self, request: &Value) -> Result<Value, String> {
        let socket = self.socket.as_ref().ok_or("no actuator socket configured")?;
        let result = exchange(socket, request);
        match &result {
            Err(reason) if !self.unreachable => {
                warn!("actuator at {} unreachable: {reason}", socket.display());
                self.unreachable = true;
            }
            Ok(_) => self.unreachable = false,
            Err(_) => {}
        }
        result
    }
}

fn exchange(socket: &PathBuf, request: &Value) -> Result<Value, String> {
    let mut stream = UnixStream::connect(socket).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(TIMEOUT)).map_err(|e| e.to_string())?;
    stream.set_write_timeout(Some(TIMEOUT)).map_err(|e| e.to_string())?;
    let mut line = serde_json::to_vec(request).map_err(|e| e.to_string())?;
    line.push(b'\n');
    stream.write_all(&line).map_err(|e| e.to_string())?;
    let mut answer = String::new();
    BufReader::new(&stream).read_line(&mut answer).map_err(|e| e.to_string())?;
    serde_json::from_str(&answer).map_err(|e| format!("not an answer: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_apply_the_actuator_cannot_get_is_refused_once_until_delivered() {
        let mut a = Actuation::new(Some("/nonexistent/actuator.sock".into()), "/nonexistent".into());
        let apply = || Command {
            id: 5,
            op: Op::Apply,
            action: Some("io_max_wbps".into()),
            target: Some("system.slice/cp.service".into()),
            value: Some(4e7),
            share: None,
            revert_after_s: Some(1800),
            reason: None,
        };
        a.run(vec![apply(), apply()]);
        let sent = a.pending();
        assert_eq!(sent.len(), 1);
        assert_eq!(sent[0]["event"], "refused");
        a.delivered(&sent);
        assert!(a.pending().is_empty());
    }

    #[test]
    fn commands_of_the_server_parse() {
        let c: Command =
            serde_json::from_str(r#"{"id": 3, "op": "revert", "reason": "verification"}"#).unwrap();
        assert_eq!((c.id, c.op, c.reason.as_deref()), (3, Op::Revert, Some("verification")));
    }
}
