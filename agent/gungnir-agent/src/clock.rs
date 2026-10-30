//! Wall-clock time of the host. Samples carry UTC; the host's offset from
//! UTC travels with each batch so that clients can show local time.

use std::time::{SystemTime, UNIX_EPOCH};

/// Milliseconds since the Unix epoch.
pub fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

/// The host's current offset from UTC in seconds, east positive, as the C
/// library resolves it from `TZ` or `/etc/localtime`.
pub fn utc_offset_s() -> i32 {
    // SAFETY: `time` with a null pointer only returns the time, and
    // `localtime_r` writes into the zeroed `tm` we own.
    unsafe {
        let now = libc::time(std::ptr::null_mut());
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&now, &mut tm).is_null() {
            return 0;
        }
        tm.tm_gmtoff as i32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offset_is_a_real_time_zone_offset() {
        let offset = utc_offset_s();
        assert!((-12 * 3600..=14 * 3600).contains(&offset));
        assert_eq!(offset % 900, 0, "time zones are whole quarter hours");
    }
}
