//! Delivery of sample batches to the core over HTTP/JSON. The agent always
//! opens the connection, so a host behind NAT or in a VM needs no inbound port.

use std::time::Duration;

use serde::Serialize;

use crate::buffer::Tick;
use crate::sources::{Event, ModuleStatus, Process};

/// A batch is a header plus a body. The body is either built from ticks in
/// memory or read back from the spool, which is why it is generic.
#[derive(Serialize)]
pub struct Batch<'a, B: Serialize> {
    pub agent: AgentInfo,
    /// Increases by one per batch; lets the core spot gaps in its log.
    pub seq: u64,
    /// Probe results, repeated in every batch so a restarted core learns
    /// them without a separate handshake.
    pub sources: &'a [ModuleStatus],
    /// Rows dropped on buffer or spool overflow since the agent started.
    pub dropped: u64,
    #[serde(flatten)]
    pub body: B,
}

/// The data part of a batch: four lists in long form, each row with its time.
#[derive(Serialize)]
pub struct Body<'a> {
    pub samples: Vec<SampleRow<'a>>,
    pub facts: Vec<FactRow<'a>>,
    pub processes: Vec<ProcessRow<'a>>,
    pub events: Vec<&'a Event>,
}

#[derive(Serialize)]
pub struct SampleRow<'a> {
    pub ts: i64,
    pub signal: &'a str,
    pub entity: &'a str,
    pub value: f64,
}

#[derive(Serialize)]
pub struct FactRow<'a> {
    pub ts: i64,
    pub name: &'a str,
    pub entity: &'a str,
    pub value: &'a str,
}

#[derive(Serialize)]
pub struct ProcessRow<'a> {
    pub ts: i64,
    #[serde(flatten)]
    pub process: &'a Process,
}

impl<'a> Body<'a> {
    pub fn from_ticks(ticks: &'a [Tick]) -> Self {
        let mut body =
            Body { samples: Vec::new(), facts: Vec::new(), processes: Vec::new(), events: Vec::new() };
        for tick in ticks {
            let ts = tick.ts;
            let d = &tick.data;
            body.samples.extend(d.readings.iter().map(|r| SampleRow {
                ts,
                signal: r.signal,
                entity: &r.entity,
                value: r.value,
            }));
            body.facts.extend(d.facts.iter().map(|f| FactRow {
                ts,
                name: f.name,
                entity: &f.entity,
                value: &f.value,
            }));
            body.processes.extend(d.processes.iter().map(|process| ProcessRow { ts, process }));
            body.events.extend(d.events.iter());
        }
        body
    }

    pub fn rows(&self) -> usize {
        self.samples.len() + self.facts.len() + self.processes.len() + self.events.len()
    }
}

/// A body read back from the spool.
pub type StoredBody = serde_json::Map<String, serde_json::Value>;

#[derive(Clone, Copy, Serialize)]
pub struct AgentInfo {
    pub version: &'static str,
    pub started_at: i64,
    /// Current offset of the host's local time from UTC, in seconds.
    pub utc_offset_s: i32,
}

pub enum Outcome {
    Accepted,
    /// The core refused the batch as malformed; resending it would only
    /// fail again, so the caller discards it.
    Rejected(String),
    /// Network trouble, a core error or a refused token; worth retrying.
    Failed(String),
}

pub struct Transport {
    http: ureq::Agent,
    url: String,
    authorization: String,
}

impl Transport {
    pub fn new(core_url: &str, token: &str, timeout: Duration) -> Self {
        let http = ureq::Agent::config_builder().timeout_global(Some(timeout)).build().into();
        Self {
            http,
            url: format!("{}/api/v1/ingest", core_url.trim_end_matches('/')),
            authorization: format!("Bearer {token}"),
        }
    }

    pub fn send<B: Serialize>(&self, batch: &Batch<B>) -> Outcome {
        let result = self.http.post(&self.url).header("Authorization", &self.authorization).send_json(batch);
        match result {
            Ok(_) => Outcome::Accepted,
            Err(ureq::Error::StatusCode(code @ (400 | 413 | 422))) => {
                Outcome::Rejected(format!("HTTP {code}"))
            }
            Err(ureq::Error::StatusCode(401)) => Outcome::Failed("HTTP 401: token not accepted".into()),
            Err(err) => Outcome::Failed(err.to_string()),
        }
    }
}
