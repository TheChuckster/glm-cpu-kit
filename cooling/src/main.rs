//! smc-fand - temperature-driven fan control for Supermicro H13/X13 BMCs.
//!
//! Puts the BMC into manual ("Full") fan mode and drives each fan zone's duty
//! cycle from IPMI temperature sensors with a PI controller. The BMC's own
//! automatic curve is retained as the fallback layer, not the primary
//! controller.
//!
//! # Zones and sensor groups
//!
//! Each zone has a primary sensor group and an optional auxiliary group, each
//! with its own setpoint. The zone's control error is the *worst* demand across
//! its groups - `max(temp - setpoint)` - so one PI loop per zone still handles
//! components with very different thermal limits. Emergency is evaluated across
//! *every* group independently, not just the one driving the loop.
//!
//! This matters on this box: measurement showed FAN1-FAN4 (zone 0) have far
//! more authority over DIMM temperature than the rear exhaust (zone 1) does.
//! Raising zone 0 from 56% to 98% pulled DIMMA~F down 6C and let zone 1 come
//! off saturation entirely. So the DIMM sensors are wired into *both* zones,
//! with a tighter setpoint than the CPU group.
//!
//! # Safety model
//!
//! The governing rule is: *if this daemon is not running, the BMC controls the
//! fans.* That is enforced by the unit's `ExecStopPost`, which invokes
//! `smc-fand --restore` on every exit path - clean stop, panic, `kill -9`, or
//! reboot. We deliberately install no signal handlers: letting SIGTERM take its
//! default action and relying on `ExecStopPost` covers strictly more cases than
//! a handler could, including SIGKILL, and keeps this binary free of `unsafe`.
//!
//! Further layers:
//!
//!   * Configuration is validated *before* manual mode is engaged. A bad value
//!     means we exit having never taken the fans away from the BMC.
//!   * Any group at or above its emergency limit forces 100% duty, bypassing
//!     both the PI output and the slew limiter.
//!   * If a zone saturates while still above setpoint, the other zones get a
//!     duty floor - unused headroom gets spent on cooling rather than sitting
//!     idle while something cooks.
//!   * *Any* consecutive IPMI failure - sensor read, duty write, readback or
//!     mode check - counts toward `MAX_FAILURES`, after which we hand back to
//!     the BMC and exit non-zero so systemd restarts us.
//!   * Duty writes are read back. A mismatch means something overrode us
//!     (chassis intrusion ramps do this), logged and exported as
//!     `smc_fand_control_lost` rather than passing silently.
//!   * Fan-failure thresholds are re-asserted at startup and read back, because
//!     a BMC firmware update silently reverts them and resurrects the low-RPM
//!     fan-fail ramp this daemon exists to avoid.
//!   * The heartbeat is written only when every zone was actually commanded.
//!     An independent systemd timer watches its age and forces BMC control if
//!     we wedge in a way systemd cannot see.

use std::collections::HashMap;
use std::env;
use std::fmt;
use std::fs;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread::sleep;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

// Supermicro OEM raw commands.
const MODE_GET: &[&str] = &["raw", "0x30", "0x45", "0x00"];
const MODE_SET: &[&str] = &["raw", "0x30", "0x45", "0x01"];
const MODE_STANDARD: &str = "0x00";
const MODE_FULL: &str = "0x01"; // "Full" means full *manual*, not full speed.
const DUTY_SET: &[&str] = &["raw", "0x30", "0x70", "0x66", "0x01"];
const DUTY_GET: &[&str] = &["raw", "0x30", "0x70", "0x66", "0x00"];

/// The BMC quantises duty, so a readback rarely equals what we wrote exactly
/// (writing 0x20 reads back 0x1f). Anything within this margin is a match;
/// beyond it, we have lost control of the zone.
const READBACK_TOLERANCE: i32 = 4;

#[derive(Debug)]
struct Error(String);

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

type Result<T> = std::result::Result<T, Error>;

// systemd parses these level prefixes off stdout, so `journalctl -p err` finds
// real problems instead of returning nothing after a thermal incident.
fn log(msg: &str) {
    println!("<6>{msg}");
    let _ = std::io::stdout().flush();
}

fn warn(msg: &str) {
    println!("<4>{msg}");
    let _ = std::io::stdout().flush();
}

fn err(msg: &str) {
    println!("<3>{msg}");
    let _ = std::io::stdout().flush();
}

/// Escape a Prometheus label value. Sensor names come from the BMC and are
/// interpolated into labels; a stray quote would corrupt the whole exposition.
fn esc(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n")
}

fn env_str(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

/// Parse a float from the environment, rejecting non-finite values.
///
/// `"nan".parse::<f64>()` succeeds. A NaN here is not a crash but something
/// worse: it wins every `total_cmp` comparison, fails every `>=` threshold
/// test, survives `clamp` untouched, and lands as 0% duty with no emergency
/// and no alert. Refuse it at the boundary.
fn env_f64(key: &str, default: f64) -> Result<f64> {
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

fn env_u32(key: &str, default: u32) -> Result<u32> {
    match env::var(key) {
        Err(_) => Ok(default),
        Ok(raw) => raw
            .trim()
            .parse()
            .map_err(|_| Error(format!("{key}={raw:?} is not a non-negative integer"))),
    }
}

fn split_list(s: &str) -> Vec<String> {
    s.split(',')
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect()
}

/// Static description of a zone. Every leaf is overridable from the
/// environment; this table only supplies defaults and the env-var prefix.
struct ZoneSpec {
    id: u8,
    name: &'static str,
    prefix: &'static str,
    sensors: &'static str,
    setpoint: f64,
    emergency: f64,
    aux_sensors: &'static str,
    aux_setpoint: f64,
    aux_emergency: f64,
    kp: f64,
    ki: f64,
}

// Zone 0 drives FAN1-FAN4, zone 1 drives FANA/FANB - verified by raising one
// zone at a time and watching which fans responded. The DIMM sensors appear in
// both zones because zone 0 has the most authority over memory temperature.
const ZONE_SPECS: &[ZoneSpec] = &[
    ZoneSpec {
        id: 0,
        name: "cpu",
        prefix: "SMC_ZONE0",
        sensors: "CPU Temp,CPU_VRM0 Temp,CPU_VRM1 Temp,SOC_VRM Temp,VDDIO_VRM Temp",
        setpoint: 70.0,
        emergency: 90.0,
        aux_sensors: "DIMMA~F Temp,DIMMG~L Temp",
        aux_setpoint: 62.0,
        aux_emergency: 82.0,
        kp: 2.5,
        ki: 0.025,
    },
    ZoneSpec {
        id: 1,
        name: "periph",
        prefix: "SMC_ZONE1",
        sensors: "System Temp,Peripheral Temp,NIC Temp",
        setpoint: 60.0,
        emergency: 80.0,
        aux_sensors: "DIMMA~F Temp,DIMMG~L Temp",
        aux_setpoint: 62.0,
        aux_emergency: 82.0,
        kp: 2.5,
        ki: 0.025,
    },
];

/// A set of sensors sharing a thermal limit.
struct SensorGroup {
    label: String,
    sensors: Vec<String>,
    setpoint: f64,
    emergency: f64,
}

/// Demand from one group: how far the hottest member is above its setpoint.
struct Demand {
    error: f64,
    temp: f64,
    sensor: String,
    group: String,
    setpoint: f64,
    emergency: bool,
}

impl SensorGroup {
    fn demand(&self, temps: &HashMap<String, f64>) -> Option<Demand> {
        let (sensor, temp) = self
            .sensors
            .iter()
            .filter_map(|n| temps.get(n).map(|t| (n.clone(), *t)))
            .max_by(|a, b| a.1.total_cmp(&b.1))?;
        Some(Demand {
            error: temp - self.setpoint,
            temp,
            sensor,
            group: self.label.clone(),
            setpoint: self.setpoint,
            emergency: temp >= self.emergency,
        })
    }
}

struct Zone {
    id: u8,
    name: String,
    groups: Vec<SensorGroup>,
    kp: f64,
    ki: f64,
    integral: f64,
    /// Last duty the BMC is believed to have *accepted*. Only updated after a
    /// successful write, so a failed write cannot anchor the slew limiter to a
    /// duty the fan never reached.
    applied: Option<u8>,
}

/// Outcome of one control step for a zone.
struct Decision {
    duty: u8,
    temp: f64,
    sensor: String,
    group: String,
    setpoint: f64,
    emergency: bool,
    /// PI wanted at least max_duty: the zone has no headroom left.
    saturated: bool,
    /// Worst error across groups, used to decide whether assist is warranted.
    error: f64,
}

impl Zone {
    /// One PI step. `floor` is a duty floor contributed by cross-zone assist.
    fn step(
        &mut self,
        temps: &HashMap<String, f64>,
        cfg: &Config,
        dt: f64,
        floor: f64,
    ) -> Result<Decision> {
        let demands: Vec<Demand> = self.groups.iter().filter_map(|g| g.demand(temps)).collect();
        if demands.is_empty() {
            return Err(Error(format!(
                "no configured sensors readable for zone '{}'",
                self.name
            )));
        }

        // Emergency is a property of ANY group, not just the one driving the
        // loop. Checking only the max-error group masks a group that is at its
        // limit whenever a peer happens to have a larger raw error - which is
        // easy to arrange once setpoint/emergency spans differ per group.
        if let Some(e) = demands.iter().find(|d| d.emergency) {
            self.integral = 0.0;
            let duty = cfg.max_duty as u8;
            // Deliberately NOT setting self.applied here: only a successful
            // write may anchor the slew limiter. An emergency whose duty write
            // fails must not leave the limiter believing the fans reached 100%,
            // or the next taper starts from a speed they never got to.
            return Ok(Decision {
                duty,
                temp: e.temp,
                sensor: e.sensor.clone(),
                group: e.group.clone(),
                setpoint: e.setpoint,
                emergency: true,
                saturated: true,
                error: e.error,
            });
        }

        // Worst demand drives the loop: a zone is only as cool as its most
        // stressed component.
        let worst = demands
            .iter()
            .max_by(|a, b| a.error.total_cmp(&b.error))
            .expect("non-empty");

        let proportional = self.kp * worst.error;

        // Anti-windup: only accumulate while the output is off the rails.
        // Without this the integral keeps growing at 100% and the fans stay
        // maxed long after the box has cooled.
        let candidate = cfg.idle_duty + proportional + self.ki * (self.integral + worst.error * dt);
        if candidate > cfg.min_duty && candidate < cfg.max_duty {
            self.integral += worst.error * dt;
        }

        let raw = cfg.idle_duty + proportional + self.ki * self.integral;
        let saturated = raw >= cfg.max_duty;

        // Assist floor is applied BEFORE the slew limiter so the approach is
        // still rate-limited - a floor should raise the target, not teleport
        // the fans and then take two minutes to taper back.
        let mut duty = raw.max(floor).clamp(cfg.min_duty, cfg.max_duty);

        // Asymmetric slew: react fast, taper slowly. Heat arrives faster than
        // it leaves, and the error directions have different costs - ramping up
        // late risks throttling, ramping down late costs only noise.
        if let Some(prev) = self.applied {
            let prev = prev as f64;
            duty = duty.clamp(prev - cfg.slew_down, prev + cfg.slew_up);
            duty = duty.clamp(cfg.min_duty, cfg.max_duty);
        }

        Ok(Decision {
            duty: duty.round() as u8,
            temp: worst.temp,
            sensor: worst.sensor.clone(),
            group: worst.group.clone(),
            setpoint: worst.setpoint,
            emergency: false,
            saturated,
            error: worst.error,
        })
    }
}

struct Config {
    ipmitool: String,
    timeout_bin: String,
    call_timeout: u64,
    tick: Duration,
    min_duty: f64,
    max_duty: f64,
    idle_duty: f64,
    slew_up: f64,
    slew_down: f64,
    assist_duty: f64,
    assist_enable: bool,
    max_failures: u32,
    verify_every: u32,
    mode_check_every: u32,
    fan_thresholds: Vec<(String, u32)>,
    metrics_path: PathBuf,
    heartbeat_path: PathBuf,
}

impl Config {
    /// Build and validate. Every failure here happens before manual mode is
    /// engaged, so a misconfigured daemon exits with the BMC still in charge
    /// rather than aborting mid-loop with the fans latched.
    fn from_env() -> Result<Self> {
        let fan_thresholds = {
            let raw = env_str("SMC_FAN_THRESHOLDS", "FAN4:140,FANB:140");
            let mut out = Vec::new();
            for entry in raw.split(',').map(str::trim).filter(|e| !e.is_empty()) {
                let (name, value) = entry
                    .split_once(':')
                    .ok_or_else(|| Error(format!("SMC_FAN_THRESHOLDS entry {entry:?} is not NAME:RPM")))?;
                let rpm: u32 = value
                    .trim()
                    .parse()
                    .map_err(|_| Error(format!("SMC_FAN_THRESHOLDS entry {entry:?} has a bad RPM")))?;
                out.push((name.trim().to_string(), rpm));
            }
            out
        };

        let cfg = Config {
            ipmitool: env_str("SMC_IPMITOOL", "/usr/bin/ipmitool"),
            timeout_bin: env_str("SMC_TIMEOUT_BIN", "/usr/bin/timeout"),
            call_timeout: env_u32("SMC_CALL_TIMEOUT", 10)? as u64,
            tick: Duration::from_secs(env_u32("SMC_TICK_SECONDS", 5)?.max(1) as u64),
            min_duty: env_f64("SMC_MIN_DUTY", 25.0)?,
            max_duty: env_f64("SMC_MAX_DUTY", 100.0)?,
            idle_duty: env_f64("SMC_IDLE_DUTY", 30.0)?,
            // Defaults match smc-fand.env, which the env file asserts.
            slew_up: env_f64("SMC_SLEW_UP", 30.0)?,
            slew_down: env_f64("SMC_SLEW_DOWN", 3.0)?,
            assist_duty: env_f64("SMC_ASSIST_DUTY", 85.0)?,
            assist_enable: env_u32("SMC_ASSIST_ENABLE", 1)? != 0,
            max_failures: env_u32("SMC_MAX_FAILURES", 3)?,
            verify_every: env_u32("SMC_VERIFY_EVERY", 12)?,
            mode_check_every: env_u32("SMC_MODE_CHECK_EVERY", 6)?,
            fan_thresholds,
            metrics_path: PathBuf::from(env_str(
                "SMC_METRICS_PATH",
                "/var/lib/prometheus/node-exporter/smc_fand.prom",
            )),
            heartbeat_path: PathBuf::from(env_str("SMC_HEARTBEAT_PATH", "/run/smc-fand/heartbeat")),
        };

        // These two are used as `%` divisors; 0 is the intuitive way to try to
        // disable a periodic check and would abort the process on tick 1.
        if cfg.verify_every == 0 {
            return Err(Error("SMC_VERIFY_EVERY must be >= 1".into()));
        }
        if cfg.mode_check_every == 0 {
            return Err(Error("SMC_MODE_CHECK_EVERY must be >= 1".into()));
        }
        if cfg.max_failures == 0 {
            return Err(Error("SMC_MAX_FAILURES must be >= 1".into()));
        }
        // f64::clamp panics when min > max, so catch the inversion here.
        if cfg.min_duty > cfg.max_duty {
            return Err(Error(format!(
                "SMC_MIN_DUTY ({}) exceeds SMC_MAX_DUTY ({})",
                cfg.min_duty, cfg.max_duty
            )));
        }
        if !(0.0..=100.0).contains(&cfg.min_duty) || !(0.0..=100.0).contains(&cfg.max_duty) {
            return Err(Error("duty bounds must lie within 0..=100".into()));
        }
        // Negative slew inverts the clamp bounds and panics on the first tick.
        // "-3" is a natural thing to write, given the banner renders "+30/-3".
        if cfg.slew_up <= 0.0 || cfg.slew_down <= 0.0 {
            return Err(Error(
                "SMC_SLEW_UP and SMC_SLEW_DOWN must be positive (the sign is implied)".into(),
            ));
        }
        if cfg.assist_duty > cfg.max_duty {
            return Err(Error(format!(
                "SMC_ASSIST_DUTY ({}) exceeds SMC_MAX_DUTY ({})",
                cfg.assist_duty, cfg.max_duty
            )));
        }
        Ok(cfg)
    }

    fn zones(&self) -> Result<Vec<Zone>> {
        let mut zones = Vec::new();
        for spec in ZONE_SPECS {
            let mut groups = vec![SensorGroup {
                label: format!("{}/primary", spec.name),
                sensors: split_list(&env_str(&format!("{}_SENSORS", spec.prefix), spec.sensors)),
                setpoint: env_f64(&format!("{}_SETPOINT", spec.prefix), spec.setpoint)?,
                emergency: env_f64(&format!("{}_EMERGENCY", spec.prefix), spec.emergency)?,
            }];

            let aux = split_list(&env_str(
                &format!("{}_AUX_SENSORS", spec.prefix),
                spec.aux_sensors,
            ));
            if !aux.is_empty() {
                groups.push(SensorGroup {
                    label: format!("{}/aux", spec.name),
                    sensors: aux,
                    setpoint: env_f64(&format!("{}_AUX_SETPOINT", spec.prefix), spec.aux_setpoint)?,
                    emergency: env_f64(
                        &format!("{}_AUX_EMERGENCY", spec.prefix),
                        spec.aux_emergency,
                    )?,
                });
            }

            for g in &groups {
                if g.emergency <= g.setpoint {
                    return Err(Error(format!(
                        "group {}: emergency ({}) must exceed setpoint ({})",
                        g.label, g.emergency, g.setpoint
                    )));
                }
            }

            zones.push(Zone {
                id: spec.id,
                name: spec.name.to_string(),
                groups,
                kp: env_f64(&format!("{}_KP", spec.prefix), spec.kp)?,
                ki: env_f64(&format!("{}_KI", spec.prefix), spec.ki)?,
                integral: 0.0,
                applied: None,
            });
        }
        Ok(zones)
    }
}

/// Run ipmitool, wrapped in `timeout(1)`.
///
/// The wrapper matters: `prometheus-node-exporter-ipmitool-sensor` also pokes
/// `/dev/ipmi0` on a timer, and the kernel serialises access. A blocked call
/// must not wedge the control loop forever.
fn ipmi(cfg: &Config, args: &[&str]) -> Result<String> {
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
fn read_temperatures(cfg: &Config) -> Result<HashMap<String, f64>> {
    let out = ipmi(cfg, &["sdr", "type", "Temperature"])?;
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

fn parse_hex_byte(s: &str) -> Result<u8> {
    let t = s.trim();
    u8::from_str_radix(t.trim_start_matches("0x"), 16)
        .map_err(|_| Error(format!("unparseable ipmitool response: {t:?}")))
}

fn get_mode(cfg: &Config) -> Result<u8> {
    parse_hex_byte(&ipmi(cfg, MODE_GET)?)
}

fn set_mode(cfg: &Config, mode: &str) -> Result<()> {
    let mut args: Vec<&str> = MODE_SET.to_vec();
    args.push(mode);
    ipmi(cfg, &args).map(|_| ())
}

fn set_duty(cfg: &Config, zone: u8, duty: u8) -> Result<()> {
    let zone_arg = format!("0x{zone:02x}");
    let duty_arg = format!("0x{duty:02x}");
    let mut args: Vec<&str> = DUTY_SET.to_vec();
    args.push(&zone_arg);
    args.push(&duty_arg);
    ipmi(cfg, &args).map(|_| ())
}

fn get_duty(cfg: &Config, zone: u8) -> Result<u8> {
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
fn assert_fan_thresholds(cfg: &Config) {
    for (fan, want) in &cfg.fan_thresholds {
        let current = match read_lower_critical(cfg, fan) {
            Ok(c) => c,
            Err(e) => {
                warn(&format!("could not read {fan} threshold: {e}"));
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
            continue;
        }
        match read_lower_critical(cfg, fan) {
            Ok(after) if (after - *want as f64).abs() < 1.0 => {
                log(&format!("{fan} lower-critical now {after}"))
            }
            Ok(after) => err(&format!(
                "{fan} threshold did not take: wrote {want}, reads {after} \
                 - the BMC quantised or rejected it"
            )),
            Err(e) => warn(&format!("could not verify {fan} threshold: {e}")),
        }
    }
}

/// Give the fans back to the BMC's own curve.
fn restore_bmc(cfg: &Config, reason: &str) -> bool {
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

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Atomic write: temp file in the same directory, then rename. Prometheus'
/// textfile collector can read at any moment and must never see a partial file.
fn write_atomic(path: &Path, contents: &str) -> Result<()> {
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

struct Report {
    zone: String,
    decision: Decision,
    readback: Option<u8>,
    control_lost: bool,
    assisting: bool,
    write_failed: bool,
}

fn write_metrics(cfg: &Config, reports: &[Report]) {
    let mut s = String::new();

    macro_rules! gauge {
        ($name:expr, $help:expr, $val:expr) => {{
            s.push_str(&format!(
                "# HELP {} {}\n# TYPE {} gauge\n",
                $name, $help, $name
            ));
            for r in reports {
                s.push_str(&format!(
                    "{}{{zone=\"{}\"}} {}\n",
                    $name,
                    esc(&r.zone),
                    $val(r)
                ));
            }
        }};
    }

    // Presence of this series is what distinguishes "stopped cleanly" (file
    // removed on exit) from "wedged". Alerts key off it.
    s.push_str("# HELP smc_fand_up smc-fand is running and in control.\n");
    s.push_str("# TYPE smc_fand_up gauge\n");
    s.push_str("smc_fand_up 1\n");

    gauge!(
        "smc_fand_duty_percent",
        "Commanded fan duty cycle.",
        |r: &Report| r.decision.duty
    );
    gauge!(
        "smc_fand_temperature_celsius",
        "Temperature of the sensor currently driving the zone.",
        |r: &Report| r.decision.temp
    );
    gauge!(
        "smc_fand_setpoint_celsius",
        "Setpoint of the group currently driving the zone.",
        |r: &Report| r.decision.setpoint
    );
    gauge!(
        "smc_fand_error_celsius",
        "Degrees above setpoint for the group driving the zone.",
        |r: &Report| r.decision.error
    );
    gauge!(
        "smc_fand_saturated",
        "Zone is at maximum duty with no headroom left. Leading indicator of insufficient cooling.",
        |r: &Report| if r.decision.saturated { 1 } else { 0 }
    );
    gauge!(
        "smc_fand_emergency",
        "A sensor group in this zone is above its emergency threshold.",
        |r: &Report| if r.decision.emergency { 1 } else { 0 }
    );
    gauge!(
        "smc_fand_control_lost",
        "Duty readback disagreed with the commanded value.",
        |r: &Report| if r.control_lost { 1 } else { 0 }
    );
    gauge!(
        "smc_fand_write_failed",
        "The last duty write to this zone returned an error.",
        |r: &Report| if r.write_failed { 1 } else { 0 }
    );
    gauge!(
        "smc_fand_assisting",
        "Zone duty was raised to lend cooling to a saturated peer zone.",
        |r: &Report| if r.assisting { 1 } else { 0 }
    );
    // Emitted unconditionally: a metric that appears only on verify ticks looks
    // like a staleness gap 11 ticks out of 12. -1 means "not sampled".
    gauge!(
        "smc_fand_duty_readback",
        "Duty reported back by the BMC, or -1 when not sampled this tick.",
        |r: &Report| r.readback.map(|v| v as i32).unwrap_or(-1)
    );

    s.push_str("# HELP smc_fand_driver Sensor and group currently driving the zone.\n");
    s.push_str("# TYPE smc_fand_driver gauge\n");
    for r in reports {
        s.push_str(&format!(
            "smc_fand_driver{{zone=\"{}\",sensor=\"{}\",group=\"{}\"}} 1\n",
            esc(&r.zone),
            esc(&r.decision.sensor),
            esc(&r.decision.group)
        ));
    }

    s.push_str("# HELP smc_fand_heartbeat_timestamp_seconds Last fully successful control tick.\n");
    s.push_str("# TYPE smc_fand_heartbeat_timestamp_seconds gauge\n");
    s.push_str(&format!(
        "smc_fand_heartbeat_timestamp_seconds {}\n",
        now_secs()
    ));

    if let Err(e) = write_atomic(&cfg.metrics_path, &s) {
        // Losing metrics is not a reason to stop cooling the box.
        warn(&format!("metrics write failed: {e}"));
    }
}

/// Remove the metrics file so `absent(smc_fand_up)` distinguishes a clean stop
/// from a wedged daemon. Without this the last values are scraped forever.
fn clear_metrics(cfg: &Config) {
    match fs::remove_file(&cfg.metrics_path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => warn(&format!("could not remove metrics file: {e}")),
    }
}

fn control_loop(cfg: &Config, zones: &mut [Zone]) -> i32 {
    let mut failures: u32 = 0;
    let mut tick: u32 = 0;
    // Last duty actually written per zone, so a steady state costs no IPMI calls.
    let mut written: HashMap<u8, u8> = HashMap::new();
    // Assist floors decided at the end of the previous tick.
    let mut floors: HashMap<u8, f64> = HashMap::new();
    let mut last_instant = Instant::now();

    macro_rules! bail_if_exhausted {
        ($failures:expr, $what:expr) => {
            if $failures >= cfg.max_failures {
                err(&format!(
                    "{} consecutive IPMI failures ({}); handing control back to the BMC",
                    $failures, $what
                ));
                restore_bmc(cfg, "IPMI failures exhausted");
                return 1;
            }
        };
    }

    loop {
        tick = tick.wrapping_add(1);

        // Real elapsed time, not the nominal tick: under IPMI contention a tick
        // can take far longer than SMC_TICK_SECONDS, which would silently
        // re-tune the integral gain.
        let now = Instant::now();
        let dt = now.duration_since(last_instant).as_secs_f64().max(0.001);
        last_instant = now;

        let temps = match read_temperatures(cfg) {
            Ok(t) => t,
            Err(e) => {
                failures += 1;
                err(&format!(
                    "sensor read failed ({failures}/{}): {e}",
                    cfg.max_failures
                ));
                if failures >= cfg.max_failures {
                    // Hand straight back to the BMC. Writing a "safe" duty here
                    // would be pointless - restoring Standard mode makes the BMC
                    // recompute duty from its own curve within milliseconds, and
                    // each dead write can burn a full call timeout against an
                    // interface that has already failed repeatedly.
                    restore_bmc(cfg, "sensor reads failing");
                    return 1;
                }
                sleep(cfg.tick);
                continue;
            }
        };

        // The BMC can drop out of manual mode on its own (a cold reset does
        // this). Checked periodically rather than every tick - it costs a fork
        // and the failure mode is slow-moving.
        if tick.is_multiple_of(cfg.mode_check_every) {
            match get_mode(cfg) {
                Ok(0x01) => failures = 0,
                Ok(m) => {
                    failures = 0;
                    warn(&format!("BMC left manual mode (now 0x{m:02x}); re-engaging"));
                    if let Err(e) = set_mode(cfg, MODE_FULL) {
                        failures += 1;
                        err(&format!("could not re-engage manual mode: {e}"));
                    }
                    written.clear(); // force duty rewrite after a mode change
                }
                Err(e) => {
                    failures += 1;
                    err(&format!(
                        "mode check failed ({failures}/{}): {e}",
                        cfg.max_failures
                    ));
                    // A BMC whose mode-get is failing may also have silently
                    // dropped to Standard, so re-assert rather than assume.
                    if let Err(e) = set_mode(cfg, MODE_FULL) {
                        err(&format!("could not re-assert manual mode: {e}"));
                    }
                }
            }
            bail_if_exhausted!(failures, "mode check");
        }

        // Decide each zone, applying any assist floor from the previous tick.
        let mut decisions = Vec::new();
        let mut zone_failed = false;
        for z in zones.iter_mut() {
            let floor = floors.get(&z.id).copied().unwrap_or(0.0);
            match z.step(&temps, cfg, dt, floor) {
                Ok(d) => decisions.push((z.id, d)),
                Err(e) => {
                    // A zone we cannot evaluate is a zone we cannot cool, and
                    // the BMC's own curve is disabled. This must escalate, not
                    // log-and-continue.
                    zone_failed = true;
                    err(&format!("zone {} cannot be evaluated: {e}", z.id));
                }
            }
        }
        if zone_failed {
            failures += 1;
            err(&format!(
                "zone evaluation failed ({failures}/{}) - check that the configured \
                 sensor names still match the BMC",
                cfg.max_failures
            ));
            bail_if_exhausted!(failures, "zone evaluation");
        }

        // Apply. Skip the write entirely when nothing changed - in steady state
        // this drops the tick from six IPMI calls to one.
        let verify = tick.is_multiple_of(cfg.verify_every);
        let mut reports = Vec::new();
        let mut all_commanded = !zone_failed;

        for (id, d) in decisions {
            let changed = written.get(&id) != Some(&d.duty);
            let mut readback = None;
            let mut control_lost = false;
            let mut write_failed = false;

            if changed {
                match set_duty(cfg, id, d.duty) {
                    Ok(()) => {
                        written.insert(id, d.duty);
                        failures = 0;
                        // Only now is the slew limiter allowed to anchor here.
                        if let Some(z) = zones.iter_mut().find(|z| z.id == id) {
                            z.applied = Some(d.duty);
                        }
                    }
                    Err(e) => {
                        write_failed = true;
                        all_commanded = false;
                        failures += 1;
                        err(&format!(
                            "duty write failed for zone {id} ({failures}/{}): {e}",
                            cfg.max_failures
                        ));
                    }
                }
            }

            // Do not read back after a failed write: the BMC still holds the old
            // duty, which would look like an override rather than our own error.
            if (changed && !write_failed) || verify {
                match get_duty(cfg, id) {
                    Ok(rb) => {
                        failures = 0;
                        control_lost = (rb as i32 - d.duty as i32).abs() > READBACK_TOLERANCE;
                        readback = Some(rb);
                        if control_lost {
                            err(&format!(
                                "CONTROL LOST on zone {id}: commanded {}%, BMC reports {rb}% \
                                 - something is overriding us",
                                d.duty
                            ));
                            written.remove(&id);
                            // Re-anchor the limiter to physical reality so the
                            // next step slews from where the fans actually are.
                            if let Some(z) = zones.iter_mut().find(|z| z.id == id) {
                                z.applied = Some(rb);
                            }
                        }
                    }
                    Err(e) => {
                        failures += 1;
                        warn(&format!(
                            "duty readback failed for zone {id} ({failures}/{}): {e}",
                            cfg.max_failures
                        ));
                    }
                }
            }

            if d.emergency {
                err(&format!(
                    "EMERGENCY zone {id}: {} ({}) at {}C -> 100%",
                    d.sensor, d.group, d.temp
                ));
            } else if d.saturated {
                warn(&format!(
                    "SATURATED zone {id}: {} ({}) at {}C, setpoint {}C, no headroom left",
                    d.sensor, d.group, d.temp, d.setpoint
                ));
            }

            let zone_name = zones
                .iter()
                .find(|z| z.id == id)
                .map(|z| z.name.clone())
                .unwrap_or_else(|| id.to_string());

            reports.push(Report {
                zone: zone_name,
                decision: d,
                readback,
                control_lost,
                assisting: floors.get(&id).copied().unwrap_or(0.0) > 0.0,
                write_failed,
            });
        }

        bail_if_exhausted!(failures, "duty commands");

        // Decide next tick's assist floors. A zone qualifies for help only when
        // it is saturated AND genuinely above setpoint - an emergency ramp sets
        // saturated=true too, and that alone should not floor a peer that is
        // sitting comfortably below its own target.
        floors.clear();
        if cfg.assist_enable {
            let needs_help = reports
                .iter()
                .any(|r| r.decision.saturated && r.decision.error > 0.0);
            if needs_help {
                for r in &reports {
                    if !(r.decision.saturated && r.decision.error > 0.0) {
                        if let Some(z) = zones.iter().find(|z| z.name == r.zone) {
                            floors.insert(z.id, cfg.assist_duty.min(cfg.max_duty));
                        }
                    }
                }
            }
        }

        write_metrics(cfg, &reports);

        // Written only when every zone was actually commanded. A heartbeat that
        // ticks while control is silently lost would defeat the whole point of
        // the independent watchdog.
        if all_commanded {
            if let Err(e) = write_atomic(&cfg.heartbeat_path, &format!("{}\n", now_secs())) {
                warn(&format!("heartbeat write failed: {e}"));
            }
        }

        sleep(cfg.tick);
    }
}

fn main() {
    let cfg = match Config::from_env() {
        Ok(c) => c,
        Err(e) => {
            // Nothing has been touched: the BMC still owns the fans.
            err(&format!("configuration error: {e}"));
            std::process::exit(2);
        }
    };

    let args: Vec<String> = env::args().skip(1).collect();

    // Used by the unit's ExecStopPost, and by hand when testing in the
    // foreground where there is no unit to put things back.
    if args.iter().any(|a| a == "--restore") {
        clear_metrics(&cfg);
        std::process::exit(if restore_bmc(&cfg, "--restore requested") {
            0
        } else {
            1
        });
    }

    let mut zones = match cfg.zones() {
        Ok(z) => z,
        Err(e) => {
            err(&format!("zone configuration error: {e}"));
            std::process::exit(2);
        }
    };

    log(&format!(
        "smc-fand starting: tick {}s, duty {}-{}%, slew +{}/-{} per tick, assist {}",
        cfg.tick.as_secs(),
        cfg.min_duty,
        cfg.max_duty,
        cfg.slew_up,
        cfg.slew_down,
        if cfg.assist_enable {
            format!("{}%", cfg.assist_duty)
        } else {
            "off".into()
        }
    ));

    // Resolve the configured sensor names against what the BMC actually
    // reports, before taking the fans. A mistyped name would otherwise leave a
    // whole group inert with no log, no metric and no error - and the DIMM aux
    // group going quietly dead is exactly the failure this daemon exists to
    // prevent.
    match read_temperatures(&cfg) {
        Ok(temps) => {
            let mut fatal = false;
            for z in &zones {
                for g in &z.groups {
                    let (found, missing): (Vec<_>, Vec<_>) =
                        g.sensors.iter().partition(|n| temps.contains_key(*n));
                    if !missing.is_empty() {
                        warn(&format!(
                            "zone {} group {}: no such sensor: {}",
                            z.id,
                            g.label,
                            missing
                                .iter()
                                .map(|s| s.as_str())
                                .collect::<Vec<_>>()
                                .join(", ")
                        ));
                    }
                    if found.is_empty() {
                        err(&format!(
                            "zone {} group {} resolved zero sensors - it would be silently inert",
                            z.id, g.label
                        ));
                        fatal = true;
                    } else {
                        log(&format!(
                            "zone {} ({}) group {}: setpoint {}C, emergency {}C, {} of {} sensors resolved",
                            z.id,
                            z.name,
                            g.label,
                            g.setpoint,
                            g.emergency,
                            found.len(),
                            g.sensors.len()
                        ));
                    }
                }
            }
            if fatal {
                err("refusing to start with an inert sensor group; BMC keeps control");
                std::process::exit(2);
            }
        }
        Err(e) => {
            err(&format!("cannot read sensors at startup: {e}"));
            std::process::exit(1);
        }
    }

    assert_fan_thresholds(&cfg);

    if let Err(e) = set_mode(&cfg, MODE_FULL) {
        err(&format!("could not engage manual fan mode: {e}"));
        std::process::exit(1);
    }
    log("manual fan mode engaged; BMC Standard mode is the fallback");

    let code = control_loop(&cfg, &mut zones);
    clear_metrics(&cfg);
    restore_bmc(&cfg, "control loop exited");
    std::process::exit(code);
}
