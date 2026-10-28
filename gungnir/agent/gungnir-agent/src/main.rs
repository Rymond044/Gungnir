//! gungnIR agent: probes signal sources, samples them at a fixed interval,
//! buffers the samples and delivers them to the core.

mod buffer;
mod clock;
mod config;
mod sources;
mod transport;

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use log::{error, info, warn};

use buffer::{Buffer, Sample};
use config::Config;
use sources::{ModuleStatus, Reading, Source, State};
use transport::{AgentInfo, Batch, Outcome, Transport};

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

    let mut agent = Agent {
        sources: probed.sources,
        failing: Vec::new(),
        statuses: probed.statuses,
        buffer: Buffer::new(config.buffer_capacity),
        transport,
        info: AgentInfo { version: VERSION, started_at: clock::now_ms(), utc_offset_s: clock::utc_offset_s() },
        seq: 0,
        max_batch: config.max_batch,
    };
    agent.failing = vec![false; agent.sources.len()];
    info!("sending to {} every {} s", config.core_url, config.send_interval_secs);

    // Probing primed the counters, so the first rates come one interval later.
    let mut next_sample = Instant::now() + config.sample_interval();
    let mut next_send = next_sample + config.send_interval();
    let mut backoff = config.send_interval();
    let mut readings = Vec::new();

    while !stop.load(Ordering::Relaxed) {
        let now = Instant::now();
        if now >= next_sample {
            agent.sample(now, &mut readings);
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

    info!("stopping; {} samples still buffered", agent.buffer.len());
    if !agent.buffer.is_empty() {
        agent.send();
    }
    Ok(())
}

struct Agent {
    sources: Vec<Box<dyn Source>>,
    /// Whether each source failed on its last read, to log changes only.
    failing: Vec<bool>,
    statuses: Vec<ModuleStatus>,
    buffer: Buffer,
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
    fn sample(&mut self, now: Instant, readings: &mut Vec<Reading>) {
        let ts = clock::now_ms();
        for (source, failing) in self.sources.iter_mut().zip(self.failing.iter_mut()) {
            readings.clear();
            match source.collect(now, readings) {
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
                    continue;
                }
            }
            for r in readings.drain(..).filter(|r| r.value.is_finite()) {
                self.buffer.push(Sample { ts, signal: r.signal, entity: r.entity, value: r.value });
            }
        }
    }

    fn send(&mut self) -> Delivery {
        if self.buffer.is_empty() {
            return Delivery::Idle;
        }
        self.seq += 1;
        let dropped = self.buffer.dropped();
        let samples = self.buffer.head(self.max_batch);
        let count = samples.len();
        let batch = Batch {
            agent: AgentInfo { utc_offset_s: clock::utc_offset_s(), ..self.info },
            seq: self.seq,
            sources: &self.statuses,
            dropped,
            samples,
        };
        match self.transport.send(&batch) {
            Outcome::Accepted => {
                self.buffer.consume(count);
            }
            Outcome::Rejected(reason) => {
                error!("core rejected batch {} of {count} samples, discarding it: {reason}", self.seq);
                self.buffer.consume(count);
            }
            Outcome::Failed(reason) => {
                warn!("delivery failed, {} samples buffered: {reason}", self.buffer.len());
                return Delivery::Failed;
            }
        }
        if self.buffer.is_empty() { Delivery::Idle } else { Delivery::MorePending }
    }
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

/// One round of readings one second after probing, so rates are present.
fn print_probe(sources: &mut [Box<dyn Source>], statuses: &[ModuleStatus]) -> Result<(), String> {
    thread::sleep(Duration::from_secs(1));
    let now = Instant::now();
    let mut readings = Vec::new();
    for source in sources.iter_mut() {
        if let Err(err) = source.collect(now, &mut readings) {
            warn!("{}: {err}", source.origin());
        }
    }
    let readings: Vec<_> = readings
        .iter()
        .map(|r| serde_json::json!({ "signal": r.signal, "entity": r.entity, "value": r.value }))
        .collect();
    let report = serde_json::json!({ "sources": statuses, "readings": readings });
    println!("{}", serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?);
    Ok(())
}
