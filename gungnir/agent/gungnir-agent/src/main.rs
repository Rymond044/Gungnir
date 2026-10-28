//! gungnIR agent: probes signal sources, samples them at a fixed interval,
//! buffers the samples and delivers them to the core.

mod buffer;
mod clock;
mod config;
mod sources;
mod spool;
mod transport;

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use log::{error, info, warn};

use buffer::{Buffer, Tick};
use config::Config;
use serde::Serialize;
use sources::{Collected, ModuleStatus, Source, State};
use spool::Spool;
use transport::{AgentInfo, Batch, Body, Outcome, StoredBody, Transport};

const VERSION: &str = env!("CARGO_PKG_VERSION");
const MAX_BACKOFF: Duration = Duration::from_secs(60);
/// Longest uninterrupted sleep, so a stop signal is noticed promptly.
const TICK: Duration = Duration::from_millis(250);

const USAGE: &str = "\
usage: gungnir-agent [--config PATH] [--probe]

  --config PATH  configuration file (TOML); built-in defaults otherwise
  --probe        report signal sources and one round of readings as JSON, then exit
  --version      print the version";

struct Args {
    config: Option<PathBuf>,
    probe: bool,
}

fn parse_args() -> Result<Args, String> {
    let mut args = Args { config: None, probe: false };
    let mut it = std::env::args().skip(1);
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--config" => args.config = Some(it.next().ok_or("--config needs a path")?.into()),
            "--probe" => args.probe = true,
            "--version" => {
                println!("gungnir-agent {VERSION}");
                std::process::exit(0);
            }
            "-h" | "--help" => {
                println!("{USAGE}");
                std::process::exit(0);
            }
            other => return Err(format!("unknown argument '{other}'\n{USAGE}")),
        }
    }
    Ok(args)
}

fn main() -> ExitCode {
    init_logging();
    match parse_args().and_then(run) {
        Ok(()) => ExitCode::SUCCESS,
        Err(msg) => {
            error!("{msg}");
            ExitCode::FAILURE
        }
    }
}

fn init_logging() {
    let mut builder = env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"));
    // journald stamps every line itself.
    if std::env::var_os("JOURNAL_STREAM").is_some() {
        builder.format_timestamp(None);
    }
    builder.init();
}

fn run(args: Args) -> Result<(), String> {
    let config = Config::load(args.config.as_deref())?;
    let mut probed = sources::probe(&config.roots());
    for status in &probed.statuses {
        log_status(status);
    }
    if args.probe {
        return print_probe(&mut probed.sources, &probed.statuses);
    }

    let token = config.read_token()?;
    let transport =
        Transport::new(&config.core_url, &token, Duration::from_secs(config.request_timeout_secs));
    let stop = Arc::new(AtomicBool::new(false));
    for signal in [signal_hook::consts::SIGTERM, signal_hook::consts::SIGINT] {
        signal_hook::flag::register(signal, Arc::clone(&stop)).map_err(|e| format!("signal handler: {e}"))?;
    }

    let spool = config.state_dir.as_ref().and_then(|dir| {
        match Spool::open(&dir.join("spool"), config.spool_max_bytes) {
            Ok(spool) => {
                info!("spool {}: {} rows waiting", spool.dir().display(), spool.rows());
                Some(spool)
            }
            Err(err) => {
                warn!("no spool, buffering in memory only: {}: {err}", dir.display());
                None
            }
        }
    });
    if config.state_dir.is_none() {
        warn!("no state directory, buffering in memory only");
    }

    let mut agent = Agent {
        sources: probed.sources,
        failing: Vec::new(),
        statuses: probed.statuses,
        buffer: Buffer::new(config.buffer_capacity),
        spool,
        transport,
        info: AgentInfo {
            version: VERSION,
            started_at: clock::now_ms(),
            utc_offset_s: clock::utc_offset_s(),
        },
        seq: 0,
        max_batch: config.max_batch,
    };
    agent.failing = vec![false; agent.sources.len()];
    info!("sending to {} every {} s", config.core_url, config.send_interval_secs);

    // Probing primed the counters, so the first rates come one interval later.
    let mut next_sample = Instant::now() + config.sample_interval();
    let mut next_send = next_sample + config.send_interval();
    let mut backoff = config.send_interval();
    while !stop.load(Ordering::Relaxed) {
        let now = Instant::now();
        if now >= next_sample {
            agent.sample(now);
            next_sample += config.sample_interval();
            // After a suspend or a stall, skip the missed ticks.
            if next_sample <= now {
                next_sample = now + config.sample_interval();
            }
        }
        if now >= next_send {
            next_send = match agent.send() {
                Delivery::Failed => {
                    let at = now + backoff;
                    backoff = (backoff * 2).min(MAX_BACKOFF);
                    at
                }
                Delivery::Idle => {
                    backoff = config.send_interval();
                    now + config.send_interval()
                }
                Delivery::MorePending => {
                    backoff = config.send_interval();
                    now
                }
            };
        }
        let wake = next_sample.min(next_send);
        thread::sleep(wake.saturating_duration_since(Instant::now()).min(TICK));
    }

    info!("stopping; {} rows in memory", agent.buffer.rows());
    if !agent.buffer.is_empty() && matches!(agent.send(), Delivery::Failed) {
        agent.spill(0);
    }
    Ok(())
}

struct Agent {
    sources: Vec<Box<dyn Source>>,
    /// Whether each source failed on its last read, to log changes only.
    failing: Vec<bool>,
    statuses: Vec<ModuleStatus>,
    buffer: Buffer,
    spool: Option<Spool>,
    transport: Transport,
    info: AgentInfo,
    seq: u64,
    max_batch: usize,
}

enum Delivery {
    /// Nothing left to send.
    Idle,
    MorePending,
    Failed,
}

impl Agent {
    fn sample(&mut self, now: Instant) {
        let ts = clock::now_ms();
        let mut data = Collected::default();
        for (source, failing) in self.sources.iter_mut().zip(self.failing.iter_mut()) {
            match source.collect(now, &mut data) {
                Ok(()) => {
                    if *failing {
                        info!("{} readable again", source.origin());
                        *failing = false;
                    }
                }
                Err(err) => {
                    if !*failing {
                        warn!("{} unreadable, skipping until it recovers: {err}", source.origin());
                        *failing = true;
                    }
                }
            }
        }
        data.readings.retain(|r| r.value.is_finite());
        if !data.is_empty() {
            self.buffer.push(Tick { ts, data });
        }
    }

    /// Sends the oldest data: first what waits in the spool, then memory.
    fn send(&mut self) -> Delivery {
        if let Some(spool) = &mut self.spool
            && let Some(stored) = spool.oldest()
        {
            let body = match stored
                .map_err(|e| e.to_string())
                .and_then(|bytes| serde_json::from_slice::<StoredBody>(&bytes).map_err(|e| e.to_string()))
            {
                Ok(body) => body,
                Err(err) => {
                    error!("unreadable spool file, discarding it: {err}");
                    self.discard_spooled();
                    return Delivery::MorePending;
                }
            };
            return match self.post(body) {
                Outcome::Failed(reason) => self.failed(&reason),
                _ => {
                    self.discard_spooled();
                    Delivery::MorePending
                }
            };
        }

        if self.buffer.is_empty() {
            return Delivery::Idle;
        }
        self.seq += 1;
        let dropped = self.buffer.dropped();
        let head = self.buffer.head(self.max_batch);
        let ticks = head.len();
        let body = Body::from_ticks(head);
        let rows = body.rows();
        let outcome = self.transport.send(&batch(&self.info, self.seq, &self.statuses, dropped, body));
        match outcome {
            Outcome::Accepted => self.buffer.consume(ticks),
            Outcome::Rejected(reason) => {
                error!("core rejected batch {} of {rows} rows, discarding it: {reason}", self.seq);
                self.buffer.consume(ticks);
            }
            Outcome::Failed(reason) => return self.failed(&reason),
        }
        if self.buffer.is_empty() { Delivery::Idle } else { Delivery::MorePending }
    }

    fn post(&mut self, body: StoredBody) -> Outcome {
        self.seq += 1;
        let dropped = self.buffer.dropped();
        let outcome = self.transport.send(&batch(&self.info, self.seq, &self.statuses, dropped, body));
        if let Outcome::Rejected(reason) = &outcome {
            error!("core rejected a spooled batch, discarding it: {reason}");
        }
        outcome
    }

    fn discard_spooled(&mut self) {
        if let Some(spool) = &mut self.spool
            && let Err(err) = spool.remove_oldest()
        {
            error!("cannot remove spool file: {err}");
        }
    }

    /// After a failed delivery, moves all but one batch of memory to the
    /// spool, so the agent's memory stays flat however long the outage.
    fn failed(&mut self, reason: &str) -> Delivery {
        self.spill(self.max_batch);
        let spooled = self.spool.as_ref().map_or(0, Spool::rows);
        warn!("delivery failed, {} rows in memory, {spooled} spooled: {reason}", self.buffer.rows());
        Delivery::Failed
    }

    /// Writes the oldest ticks to the spool until at most `keep` rows remain
    /// in memory.
    fn spill(&mut self, keep: usize) {
        let Some(spool) = &mut self.spool else { return };
        while self.buffer.rows() > keep {
            let ticks = self.buffer.head(self.max_batch);
            let n = ticks.len();
            let body = Body::from_ticks(ticks);
            let rows = body.rows() as u64;
            let written = serde_json::to_vec(&body)
                .map_err(std::io::Error::other)
                .and_then(|bytes| spool.put(&bytes, rows));
            match written {
                Ok(dropped) => {
                    self.buffer.consume(n);
                    self.buffer.add_dropped(dropped);
                }
                Err(err) => {
                    error!("cannot write to the spool, keeping data in memory: {err}");
                    return;
                }
            }
        }
    }
}

fn batch<'a, B: Serialize>(
    info: &AgentInfo,
    seq: u64,
    sources: &'a [ModuleStatus],
    dropped: u64,
    body: B,
) -> Batch<'a, B> {
    Batch { agent: AgentInfo { utc_offset_s: clock::utc_offset_s(), ..*info }, seq, sources, dropped, body }
}

fn log_status(status: &ModuleStatus) {
    let origin = status.origin.as_deref().unwrap_or("-");
    let reason = status.reason.as_deref().unwrap_or("");
    match status.state {
        State::Preferred => info!("source {}: {origin}", status.module),
        State::Fallback => warn!("source {}: fallback {origin} ({reason})", status.module),
        State::Disabled => warn!("source {}: disabled ({reason})", status.module),
    }
}

/// One round of collection one second after probing, so rates are present.
fn print_probe(sources: &mut [Box<dyn Source>], statuses: &[ModuleStatus]) -> Result<(), String> {
    thread::sleep(Duration::from_secs(1));
    let now = Instant::now();
    let mut data = Collected::default();
    for source in sources.iter_mut() {
        if let Err(err) = source.collect(now, &mut data) {
            warn!("{}: {err}", source.origin());
        }
    }
    let tick = [Tick { ts: clock::now_ms(), data }];
    let report = serde_json::json!({ "sources": statuses, "collected": Body::from_ticks(&tick) });
    println!("{}", serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?);
    Ok(())
}
