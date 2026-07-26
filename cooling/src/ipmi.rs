//! IPMI primitives. Everything here shells out to ipmitool through timeout(1).

use std::collections::HashMap;
use std::process::Command;

use crate::config::Config;
use crate::util::{err, log, Error, Result};

// Supermicro OEM raw commands.
const MODE_GET: &[&str] = &["raw", "0x30", "0x45", "0x00"];
const MODE_SET: &[&str] = &["raw", "0x30", "0x45", "0x01"];
pub const MODE_STANDARD: &str = "0x00";
pub const MODE_FULL: &str = "0x01"; // "Full" means full *manual*, not full speed.
const DUTY_SET: &[&str] = &["raw", "0x30", "0x70", "0x66", "0x01"];
const DUTY_GET: &[&str] = &["raw", "0x30", "0x70", "0x66", "0x00"];

/// Run ipmitool, wrapped in `timeout(1)`.
///
/// The wrapper matters: `prometheus-node-exporter-ipmitool-sensor` also pokes
/// `/dev/ipmi0` on a timer, and the kernel serialises access. A blocked call
/// must not wedge the control loop forever.
pub fn ipmi(cfg: &Config, args: &[&str]) -> Result<String> {
    let mut cmd = Command::new(&cfg.timeout_bin);
    cmd.arg(cfg.call_timeout.to_string())
        .arg(&cfg.ipmitool)
        .args(["-I", "open"])
        .args(args);

    let out = cmd
        .output()
        .map_err(|e| Error(format!("spawn {}: {e}", cfg.timeout_bin)))?;

    if !out.status.success() {
        return Err(Error(format!(
            "ipmitool {} failed ({}): {}",
            args.join(" "),
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        )));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Parse `ipmitool sdr type Temperature` into {sensor name: celsius}.
///
/// The status column is one of {ok, ns, nc, cr, nr}. Only `ns` ("no sensor")
/// means there is no reading; `nc`/`cr`/`nr` mean a non-critical / critical /
/// non-recoverable threshold has been crossed - in other words the sensor is
/// HOT and its reading matters more than ever. Filtering on `== "ok"` would
/// drop a sensor at exactly the moment it starts to overheat, taking the
/// emergency override down with it. The `degrees C` suffix test below is what
/// actually excludes unreadable sensors.
pub fn read_temperatures(cfg: &Config) -> Result<HashMap<String, f64>> {
    parse_temperatures(&ipmi(cfg, &["sdr", "type", "Temperature"])?)
}

pub fn parse_temperatures(out: &str) -> Result<HashMap<String, f64>> {
    let mut temps = HashMap::new();

    for line in out.lines() {
        let fields: Vec<&str> = line.split('|').collect();
        if fields.len() < 5 {
            continue;
        }
        let value = fields[4].trim();
        if !value.ends_with("degrees C") {
            continue;
        }
        if let Some(Ok(c)) = value.split_whitespace().next().map(str::parse::<f64>) {
            if c.is_finite() {
                temps.insert(fields[0].trim().to_string(), c);
            }
        }
    }

    if temps.is_empty() {
        return Err(Error("no readable temperature sensors".into()));
    }
    Ok(temps)
}

/// Parse `ipmitool sdr type Fan` into {fan name: rpm}.
pub fn read_fans(cfg: &Config) -> Result<HashMap<String, f64>> {
    let out = ipmi(cfg, &["sdr", "type", "Fan"])?;
    let mut fans = HashMap::new();
    for line in out.lines() {
        let fields: Vec<&str> = line.split('|').collect();
        if fields.len() < 5 {
            continue;
        }
        let value = fields[4].trim();
        if !value.ends_with("RPM") {
            continue;
        }
        if let Some(Ok(rpm)) = value.split_whitespace().next().map(str::parse::<f64>) {
            if rpm.is_finite() {
                fans.insert(fields[0].trim().to_string(), rpm);
            }
        }
    }
    Ok(fans)
}

fn parse_hex_byte(s: &str) -> Result<u8> {
    let t = s.trim();
    u8::from_str_radix(t.trim_start_matches("0x"), 16)
        .map_err(|_| Error(format!("unparseable ipmitool response: {t:?}")))
}

pub fn get_mode(cfg: &Config) -> Result<u8> {
    parse_hex_byte(&ipmi(cfg, MODE_GET)?)
}

pub fn set_mode(cfg: &Config, mode: &str) -> Result<()> {
    let mut args: Vec<&str> = MODE_SET.to_vec();
    args.push(mode);
    ipmi(cfg, &args).map(|_| ())
}

pub fn set_duty(cfg: &Config, zone: u8, duty: u8) -> Result<()> {
    let zone_arg = format!("0x{zone:02x}");
    let duty_arg = format!("0x{duty:02x}");
    let mut args: Vec<&str> = DUTY_SET.to_vec();
    args.push(&zone_arg);
    args.push(&duty_arg);
    ipmi(cfg, &args).map(|_| ())
}

pub fn get_duty(cfg: &Config, zone: u8) -> Result<u8> {
    let zone_arg = format!("0x{zone:02x}");
    let mut args: Vec<&str> = DUTY_GET.to_vec();
    args.push(&zone_arg);
    parse_hex_byte(&ipmi(cfg, &args)?)
}

fn read_lower_critical(cfg: &Config, fan: &str) -> Result<f64> {
    let out = ipmi(cfg, &["sensor", "get", fan])?;
    out.lines()
        .find_map(|l| {
            let (k, v) = l.split_once(':')?;
            if k.trim().eq_ignore_ascii_case("Lower Critical") {
                v.trim().parse::<f64>().ok()
            } else {
                None
            }
        })
        .ok_or_else(|| Error(format!("no lower-critical reported for {fan}")))
}

/// Re-assert the fan-failure thresholds this daemon's low duty cycles depend on.
///
/// A BMC firmware update resets these to the factory 420 RPM, at which point
/// the rear fans idling at 420 look like failures and the BMC starts the
/// full-speed ramp cycle again. The write is read back because these BMCs
/// quantise to fixed RPM steps and will silently round or ignore a value.
///
/// Returns false if any threshold could not be confirmed. The caller MUST act
/// on that: idling the fans at `min_duty` under an unlowered threshold recreates
/// exactly the bug this daemon exists to prevent, so a failure here has to
/// change behaviour rather than just appear in the log.
#[must_use]
pub fn assert_fan_thresholds(cfg: &Config) -> bool {
    let mut all_ok = true;

    for (fan, want) in &cfg.fan_thresholds {
        let current = match read_lower_critical(cfg, fan) {
            Ok(c) => c,
            Err(e) => {
                // Cannot read means cannot confirm. On a board whose `sensor
                // get` output differs this would otherwise silently no-op.
                err(&format!("could not read {fan} threshold: {e}"));
                all_ok = false;
                continue;
            }
        };
        if (current - *want as f64).abs() < 1.0 {
            continue;
        }

        log(&format!(
            "{fan} lower-critical is {current}, expected {want} - re-asserting"
        ));
        let w = want.to_string();
        if let Err(e) = ipmi(cfg, &["sensor", "thresh", fan, "lcr", &w]) {
            err(&format!("could not set {fan} threshold: {e}"));
            all_ok = false;
            continue;
        }
        match read_lower_critical(cfg, fan) {
            Ok(after) if (after - *want as f64).abs() < 1.0 => {
                log(&format!("{fan} lower-critical now {after}"))
            }
            Ok(after) => {
                err(&format!(
                    "{fan} threshold did not take: wrote {want}, reads {after} \
                     - the BMC quantised or rejected it"
                ));
                all_ok = false;
            }
            Err(e) => {
                err(&format!("could not verify {fan} threshold: {e}"));
                all_ok = false;
            }
        }
    }
    all_ok
}

/// Give the fans back to the BMC's own curve.
pub fn restore_bmc(cfg: &Config, reason: &str) -> bool {
    match set_mode(cfg, MODE_STANDARD) {
        Ok(()) => {
            log(&format!("restored BMC Standard fan mode ({reason})"));
            true
        }
        Err(e) => {
            err(&format!("FAILED to restore BMC fan mode ({reason}): {e}"));
            false
        }
    }
}
