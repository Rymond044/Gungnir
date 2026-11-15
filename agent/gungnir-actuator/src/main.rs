//! gungnIR actuator: the only privileged part on a host (ADR 0007). It takes
//! typed commands from the agent over a local socket, checks each one on its
//! own, changes control group limits and undoes every change by its timer
//! unless kept (ADR 0062).

mod actions;
mod actuator;
mod config;
mod groups;
mod peer;

use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use log::{error, info, warn};

use actuator::{Actuator, Request, Response};
use config::Config;

const VERSION: &str = env!("CARGO_PKG_VERSION");
/// Longest uninterrupted sleep, so timers and a stop signal are noticed promptly.
const TICK: Duration = Duration::from_millis(200);
const MAX_REQUEST: u64 = 64 * 1024;

const USAGE: &str = "\
usage: gungnir-actuator [--config PATH]

  --config PATH  configuration file (TOML); built-in defaults otherwise
  --version      print the version";

fn main() -> ExitCode {
    let mut builder = env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"));
    if std::env::var_os("JOURNAL_STREAM").is_some() {
        builder.format_timestamp(None);
    }
    builder.init();
    match parse_args().and_then(run) {
        Ok(()) => ExitCode::SUCCESS,
        Err(msg) => {
            error!("{msg}");
            ExitCode::FAILURE
        }
    }
}

fn parse_args() -> Result<Option<PathBuf>, String> {
    let mut config = None;
    let mut it = std::env::args().skip(1);
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--config" => config = Some(it.next().ok_or("--config needs a path")?.into()),
            "--version" => {
                println!("gungnir-actuator {VERSION}");
                std::process::exit(0);
            }
            "-h" | "--help" => {
                println!("{USAGE}");
                std::process::exit(0);
            }
            other => return Err(format!("unknown argument '{other}'\n{USAGE}")),
        }
    }
    Ok(config)
}

fn run(config_path: Option<PathBuf>) -> Result<(), String> {
    let config = Config::load(config_path.as_deref())?;
    let state_dir = config.state_dir.clone().expect("checked by Config::load");
    let boot_id = std::fs::read_to_string(config.proc_root.join("sys/kernel/random/boot_id"))
        .map_err(|e| format!("boot id: {e}"))?;
    let mut actuator = Actuator::open(&config, &state_dir, boot_id.trim())?;
    let listener = listen(&config.socket, &config.socket_group)?;
    let stop = Arc::new(AtomicBool::new(false));
    for signal in [signal_hook::consts::SIGTERM, signal_hook::consts::SIGINT] {
        signal_hook::flag::register(signal, Arc::clone(&stop)).map_err(|e| format!("signal handler: {e}"))?;
    }
    info!("listening on {}; {} change(s) held", config.socket.display(), actuator.held());

    while !stop.load(Ordering::Relaxed) {
        actuator.tick(actuator::now_ms());
        match listener.accept() {
            Ok((stream, _)) => serve(&mut actuator, stream, &config),
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(TICK),
            Err(e) => {
                warn!("accept: {e}");
                std::thread::sleep(TICK);
            }
        }
    }
    info!("stopping");
    actuator.stop();
    let _ = std::fs::remove_file(&config.socket);
    Ok(())
}

/// The socket, readable and writable by root and the group of the agent.
fn listen(path: &Path, group: &str) -> Result<UnixListener, String> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    match std::fs::remove_file(path) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => return Err(format!("{}: {e}", path.display())),
        _ => {}
    }
    let listener = UnixListener::bind(path).map_err(|e| format!("{}: {e}", path.display()))?;
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    let mode = match group_id(group) {
        Some(gid) => {
            let c = std::ffi::CString::new(path.as_os_str().as_encoded_bytes()).map_err(|e| e.to_string())?;
            // SAFETY: `c` is a valid NUL-terminated path; -1 keeps the owner.
            if unsafe { libc::chown(c.as_ptr(), u32::MAX, gid) } != 0 {
                return Err(format!("chown {}: {}", path.display(), std::io::Error::last_os_error()));
            }
            0o660
        }
        None => {
            warn!("no group '{group}': the socket is for its owner only");
            0o600
        }
    };
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode)).map_err(|e| e.to_string())?;
    Ok(listener)
}

fn group_id(name: &str) -> Option<libc::gid_t> {
    let c = std::ffi::CString::new(name).ok()?;
    // SAFETY: getgrnam returns null or a pointer to static storage read at once.
    let group = unsafe { libc::getgrnam(c.as_ptr()) };
    (!group.is_null()).then(|| unsafe { (*group).gr_gid })
}

/// One request and one answer per connection, each a line of JSON.
fn serve(actuator: &mut Actuator, stream: UnixStream, config: &Config) {
    let _ = stream.set_nonblocking(false);
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    if let Err(reason) = peer::check(&stream, &config.client_unit, &config.proc_root) {
        warn!("connection refused: {reason}");
        return;
    }
    let mut line = String::new();
    let mut reader = BufReader::new((&stream).take(MAX_REQUEST));
    let response = match reader.read_line(&mut line) {
        Ok(_) => match serde_json::from_str::<Request>(&line) {
            Ok(request) => actuator.handle(request, actuator::now_ms()),
            Err(e) => Response { ok: false, error: Some(format!("not a command: {e}")), entries: None },
        },
        Err(e) => Response { ok: false, error: Some(format!("read: {e}")), entries: None },
    };
    let mut out = serde_json::to_vec(&response).unwrap_or_default();
    out.push(b'\n');
    if let Err(e) = (&stream).write_all(&out) {
        warn!("answer not delivered: {e}");
    }
}
