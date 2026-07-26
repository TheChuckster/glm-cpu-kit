//! Shared plumbing: errors, logging, environment parsing, atomic writes.

use std::env;
use std::fmt;
use std::fs;
use std::io::Write as _;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// Construct an `Error` with `format!` syntax.
#[macro_export]
macro_rules! bail {
    ($($arg:tt)*) => {
        return Err($crate::util::Error(format!($($arg)*)))
    };
}

// systemd parses these level prefixes off stdout, so `journalctl -p err` finds
// real problems instead of returning nothing after a thermal incident.
pub fn log(msg: &str) {
    println!("<6>{msg}");
    let _ = std::io::stdout().flush();
}

pub fn warn(msg: &str) {
    println!("<4>{msg}");
    let _ = std::io::stdout().flush();
}

pub fn err(msg: &str) {
    println!("<3>{msg}");
    let _ = std::io::stdout().flush();
}

/// Plain output with no journal level prefix, for `--explain` and
/// `--calibrate` where a human is reading directly.
pub fn out(msg: &str) {
    println!("{msg}");
    let _ = std::io::stdout().flush();
}

/// Escape a Prometheus label value. Sensor names come from the BMC and are
/// interpolated into labels; a stray quote would corrupt the whole exposition.
pub fn esc(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n")
}

pub fn env_str(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

/// Parse a float from the environment, rejecting non-finite values.
///
/// `"nan".parse::<f64>()` succeeds. A NaN here is not a crash but something
/// worse: it wins every `total_cmp` comparison, fails every `>=` threshold
/// test, survives `clamp` untouched, and lands as 0% duty with no emergency
/// and no alert. Refuse it at the boundary.
pub fn env_f64(key: &str, default: f64) -> Result<f64> {
    match env::var(key) {
        Err(_) => Ok(default),
        Ok(raw) => {
            let v: f64 = raw
                .trim()
                .parse()
                .map_err(|_| Error(format!("{key}={raw:?} is not a number")))?;
            if !v.is_finite() {
                return Err(Error(format!("{key}={raw:?} is not a finite number")));
            }
            Ok(v)
        }
    }
}

pub fn env_u32(key: &str, default: u32) -> Result<u32> {
    match env::var(key) {
        Err(_) => Ok(default),
        Ok(raw) => raw
            .trim()
            .parse()
            .map_err(|_| Error(format!("{key}={raw:?} is not a non-negative integer"))),
    }
}

pub fn split_list(s: &str) -> Vec<String> {
    s.split(',')
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect()
}

pub fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Atomic write: temp file in the same directory, then rename. Prometheus'
/// textfile collector can read at any moment and must never see a partial file.
pub fn write_atomic(path: &Path, contents: &str) -> Result<()> {
    let dir = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(dir)?;
    let tmp = path.with_extension("tmp");
    {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(contents.as_bytes())?;
        // One fsync, on the data. The rename below is atomic within the
        // directory either way, and this file is regenerated every tick.
        f.sync_data()?;
    }
    fs::rename(&tmp, path)?;
    Ok(())
}

/// Least-squares slope of y against x. Returns (slope, r_squared).
///
/// Used to turn a calibration sweep into a gain in degrees per percent duty.
/// Returns None when x has no spread, which would make the slope undefined.
pub fn linear_fit(points: &[(f64, f64)]) -> Option<(f64, f64)> {
    let n = points.len() as f64;
    if points.len() < 2 {
        return None;
    }
    let mean_x = points.iter().map(|p| p.0).sum::<f64>() / n;
    let mean_y = points.iter().map(|p| p.1).sum::<f64>() / n;

    let mut sxx = 0.0;
    let mut sxy = 0.0;
    let mut syy = 0.0;
    for (x, y) in points {
        sxx += (x - mean_x) * (x - mean_x);
        sxy += (x - mean_x) * (y - mean_y);
        syy += (y - mean_y) * (y - mean_y);
    }
    if sxx <= f64::EPSILON {
        return None;
    }

    let slope = sxy / sxx;
    // With no variance in y the fit is perfect but meaningless; report r2 = 0
    // so confidence stays low rather than claiming certainty about a flat line.
    let r2 = if syy <= f64::EPSILON {
        0.0
    } else {
        (sxy * sxy) / (sxx * syy)
    };
    Some((slope, r2.clamp(0.0, 1.0)))
}
