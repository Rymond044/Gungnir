//! Delivery of sample batches to the core over HTTP/JSON. The agent always
//! opens the connection, so a host behind NAT or in a VM needs no inbound port.

use std::time::Duration;

use serde::Serialize;

use crate::buffer::Sample;
use crate::sources::ModuleStatus;

#[derive(Serialize)]
pub struct Batch<'a> {
    pub agent: AgentInfo,
    /// Increases by one per batch; lets the core spot gaps in its log.
    pub seq: u64,
    /// Probe results, repeated in every batch so a restarted core learns
    /// them without a separate handshake.
    pub sources: &'a [ModuleStatus],
    /// Samples dropped on buffer overflow since the agent started.
    pub dropped: u64,
    pub samples: &'a [Sample],
}

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

    pub fn send(&self, batch: &Batch) -> Outcome {
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
