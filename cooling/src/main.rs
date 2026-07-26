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
//! components with very different thermal limits.
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
//! fans.* That is enforced by the unit's `ExecStopPost`, which restores
//! Standard mode on every exit path - clean stop, panic, `kill -9`, or reboot.
//! We deliberately install no signal handlers: letting SIGTERM take its default
//! action and relying on `ExecStopPost` covers strictly more cases than a
//! handler could, including SIGKILL, and keeps this binary free of `unsafe`.
//!
//! Further layers, innermost first:
//!
//!   * Any group at or above its emergency limit forces 100% duty, bypassing
//!     both the PI output and the slew limiter.
//!   * If one zone saturates while still above setpoint, the other zone is
//!     floored at the assist duty - unused headroom gets spent on cooling
//!     rather than sitting idle while something cooks.
//!   * IPMI read failures are retried; after `MAX_FAILURES` consecutive
//!     failures we ramp to a safe-high duty, restore BMC control and exit
//!     non-zero so systemd restarts us.
//!   * Duty writes are read back. A mismatch means something overrode us
//!     (chassis intrusion ramps do this), logged and exported as
//!     `smc_fand_control_lost` rather than passing silently.
//!   * Fan-failure thresholds are re-asserted at startup, because a BMC
//!     firmware update silently reverts them and resurrects the low-RPM
//!     fan-fail ramp this daemon exists to avoid.
//!   * A heartbeat file is touched each tick. An independent systemd timer
//!     watches its age and forces BMC control if we wedge in a way systemd
//!     cannot see.

use std::collections::HashMap;
use std::env;
use std::fmt;
use std::fs;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread::sleep;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

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

fn log(msg: &str) {
    println!("{msg}");
    let _ = std::io::stdout().flush();
}

fn env_str(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_num<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

fn split_list(s: &str) -> Vec<String> {
    s.split(',')
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect()
}

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
    last_duty: Option<u8>,
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
}

impl Zone {
    fn step(&mut self, temps: &HashMap<String, f64>, cfg: &Config, dt: f64) -> Result<Decision> {
        // Worst demand across groups drives the loop. A zone is only as cool as
        // its most stressed component, and components differ in what "hot"
        // means - hence per-group setpoints rather than one per zone.
        let worst = self
            .groups
            .iter()
            .filter_map(|g| g.demand(temps))
            .max_by(|a, b| a.error.total_cmp(&b.error))
            .ok_or_else(|| Error(format!("no sensors readable for zone '{}'", self.name)))?;

        let setpoint = worst.temp - worst.error;

        if worst.emergency {
            self.integral = 0.0;
            self.last_duty = Some(cfg.max_duty as u8);
            return Ok(Decision {
                duty: cfg.max_duty as u8,
                temp: worst.temp,
                sensor: worst.sensor,
                group: worst.group,
                setpoint,
                emergency: true,
                saturated: true,
            });
        }

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
        let mut duty = raw.clamp(cfg.min_duty, cfg.max_duty);

        // Asymmetric slew: climb quickly, back off slowly. Ramping up late is a
        // thermal risk; ramping down fast just makes the box audibly hunt.
        if let Some(prev) = self.last_duty {
            let prev = prev as f64;
            duty = duty.clamp(prev - cfg.slew_down, prev + cfg.slew_up);
            duty = duty.clamp(cfg.min_duty, cfg.max_duty);
        }

        let duty = duty.round() as u8;
        self.last_duty = Some(duty);
        Ok(Decision {
            duty,
            temp: worst.temp,
            sensor: worst.sensor,
            group: worst.group,
            setpoint,
            emergency: false,
            saturated,
        })
    }

    /// Raise this zone's floor to lend cooling to a saturated peer.
    fn apply_assist(&mut self, floor: u8, cfg: &Config) -> u8 {
        let duty = self.last_duty.unwrap_or(cfg.min_duty as u8).max(floor);
        self.last_duty = Some(duty);
        duty
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
    assist_duty: u8,
    assist_enable: bool,
    max_failures: u32,
    failure_duty: u8,
    /// Re-verify duty readback every N ticks even when nothing changed.
    verify_every: u32,
    /// Re-check the BMC is still in manual mode every N ticks.
    mode_check_every: u32,
    /// "FAN4:140,FANB:140" - lower-critical thresholds to re-assert at startup.
    fan_thresholds: Vec<(String, u32)>,
    metrics_path: PathBuf,
    heartbeat_path: PathBuf,
}

impl Config {
    fn from_env() -> Self {
        let thresholds = env_str("SMC_FAN_THRESHOLDS", "FAN4:140,FANB:140")
            .split(',')
            .filter_map(|entry| {
                let (name, value) = entry.trim().split_once(':')?;
                Some((name.trim().to_string(), value.trim().parse().ok()?))
            })
            .collect();

        Config {
            ipmitool: env_str("SMC_IPMITOOL", "/usr/bin/ipmitool"),
            timeout_bin: env_str("SMC_TIMEOUT_BIN", "/usr/bin/timeout"),
            call_timeout: env_num("SMC_CALL_TIMEOUT", 15),
            tick: Duration::from_secs(env_num("SMC_TICK_SECONDS", 5)),
            min_duty: env_num("SMC_MIN_DUTY", 25.0),
            max_duty: env_num("SMC_MAX_DUTY", 100.0),
            idle_duty: env_num("SMC_IDLE_DUTY", 30.0),
            slew_up: env_num("SMC_SLEW_UP", 20.0),
            slew_down: env_num("SMC_SLEW_DOWN", 5.0),
            assist_duty: env_num("SMC_ASSIST_DUTY", 85),
            assist_enable: env_num::<u32>("SMC_ASSIST_ENABLE", 1) != 0,
            max_failures: env_num("SMC_MAX_FAILURES", 3),
            failure_duty: env_num("SMC_FAILURE_DUTY", 70),
            verify_every: env_num("SMC_VERIFY_EVERY", 12),
            mode_check_every: env_num("SMC_MODE_CHECK_EVERY", 6),
            fan_thresholds: thresholds,
            metrics_path: PathBuf::from(env_str(
                "SMC_METRICS_PATH",
                "/var/lib/prometheus/node-exporter/smc_fand.prom",
            )),
            heartbeat_path: PathBuf::from(env_str("SMC_HEARTBEAT_PATH", "/run/smc-fand/heartbeat")),
        }
    }

    fn zones(&self) -> Vec<Zone> {
        // Zone 0 drives FAN1-FAN4, zone 1 drives FANA/FANB - verified by
        // raising one zone at a time and watching which fans responded.
        vec![
            Zone {
                id: 0,
                name: "cpu".into(),
                groups: build_groups(
                    "cpu",
                    "SMC_ZONE0",
                    "CPU Temp,CPU_VRM0 Temp,CPU_VRM1 Temp,SOC_VRM Temp,VDDIO_VRM Temp",
                    70.0,
                    90.0,
                    // Zone 0 has the most authority over DIMM temperature on
                    // this chassis, so it watches the DIMMs too.
                    "DIMMA~F Temp,DIMMG~L Temp",
                    62.0,
                    82.0,
                ),
                kp: env_num("SMC_ZONE0_KP", 2.5),
                // Halved from the original 0.05: zone 0 visibly hunted
                // (65 -> 75 -> 55 -> 62 -> 58) while CPU temperature barely moved.
                ki: env_num("SMC_ZONE0_KI", 0.025),
                integral: 0.0,
                last_duty: None,
            },
            Zone {
                id: 1,
                name: "periph".into(),
                groups: build_groups(
                    "periph",
                    "SMC_ZONE1",
                    "System Temp,Peripheral Temp,NIC Temp",
                    60.0,
                    80.0,
                    "DIMMA~F Temp,DIMMG~L Temp",
                    62.0,
                    82.0,
                ),
                kp: env_num("SMC_ZONE1_KP", 2.5),
                ki: env_num("SMC_ZONE1_KI", 0.025),
                integral: 0.0,
                last_duty: None,
            },
        ]
    }
}

#[allow(clippy::too_many_arguments)]
fn build_groups(
    zone: &str,
    prefix: &str,
    default_sensors: &str,
    default_setpoint: f64,
    default_emergency: f64,
    default_aux_sensors: &str,
    default_aux_setpoint: f64,
    default_aux_emergency: f64,
) -> Vec<SensorGroup> {
    let mut groups = vec![SensorGroup {
        label: format!("{zone}/primary"),
        sensors: split_list(&env_str(&format!("{prefix}_SENSORS"), default_sensors)),
        setpoint: env_num(&format!("{prefix}_SETPOINT"), default_setpoint),
        emergency: env_num(&format!("{prefix}_EMERGENCY"), default_emergency),
    }];

    let aux = split_list(&env_str(&format!("{prefix}_AUX_SENSORS"), default_aux_sensors));
    if !aux.is_empty() {
        groups.push(SensorGroup {
            label: format!("{zone}/aux"),
            sensors: aux,
            setpoint: env_num(&format!("{prefix}_AUX_SETPOINT"), default_aux_setpoint),
            emergency: env_num(&format!("{prefix}_AUX_EMERGENCY"), default_aux_emergency),
        });
    }
    groups
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
/// Absent sensors read `ns` / `No Reading` and are skipped.
fn read_temperatures(cfg: &Config) -> Result<HashMap<String, f64>> {
    let out = ipmi(cfg, &["sdr", "type", "Temperature"])?;
    let mut temps = HashMap::new();

    for line in out.lines() {
        let fields: Vec<&str> = line.split('|').collect();
        if fields.len() < 5 || fields[2].trim() != "ok" {
            continue;
        }
        let value = fields[4].trim();
        if !value.ends_with("degrees C") {
            continue;
        }
        if let Some(Ok(c)) = value.split_whitespace().next().map(str::parse::<f64>) {
            temps.insert(fields[0].trim().to_string(), c);
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

/// Re-assert the fan-failure thresholds this daemon's low duty cycles depend on.
///
/// A BMC firmware update resets these to the factory 420 RPM, at which point
/// the rear fans idling at 420 look like failures and the BMC starts the
/// full-speed ramp cycle again. Cheap to check, and silent breakage otherwise.
fn assert_fan_thresholds(cfg: &Config) {
    for (fan, want) in &cfg.fan_thresholds {
        match ipmi(cfg, &["sensor", "get", fan]) {
            Ok(out) => {
                let current = out.lines().find_map(|l| {
                    let (k, v) = l.split_once(':')?;
                    if k.trim().eq_ignore_ascii_case("Lower Critical") {
                        v.trim().parse::<f64>().ok()
                    } else {
                        None
                    }
                });
                match current {
                    Some(c) if (c - *want as f64).abs() < 1.0 => {}
                    Some(c) => {
                        log(&format!(
                            "{fan} lower-critical is {c}, expected {want} - re-asserting"
                        ));
                        let w = want.to_string();
                        if let Err(e) = ipmi(cfg, &["sensor", "thresh", fan, "lcr", &w]) {
                            log(&format!("WARNING: could not set {fan} threshold: {e}"));
                        }
                    }
                    None => log(&format!("WARNING: no lower-critical reported for {fan}")),
                }
            }
            Err(e) => log(&format!("WARNING: could not read {fan}: {e}")),
        }
    }
}

/// Give the fans back to the BMC's own curve. Best-effort by definition: this
/// runs on the way out, and the unit's ExecStopPost repeats it regardless.
fn restore_bmc(cfg: &Config, reason: &str) {
    match set_mode(cfg, MODE_STANDARD) {
        Ok(()) => log(&format!("restored BMC Standard fan mode ({reason})")),
        Err(e) => log(&format!(
            "WARNING: could not restore BMC fan mode ({reason}): {e} \
             - systemd ExecStopPost will retry"
        )),
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
        f.sync_all()?;
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
}

fn write_metrics(cfg: &Config, reports: &[Report]) {
    let mut s = String::new();

    macro_rules! gauge {
        ($name:expr, $help:expr, $val:expr) => {{
            s.push_str(&format!("# HELP {} {}\n# TYPE {} gauge\n", $name, $help, $name));
            for r in reports {
                s.push_str(&format!("{}{{zone=\"{}\"}} {}\n", $name, r.zone, $val(r)));
            }
        }};
    }

    gauge!("smc_fand_duty_percent", "Commanded fan duty cycle.", |r: &Report| r
        .decision
        .duty);
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
        "smc_fand_saturated",
        "Zone is at maximum duty with no headroom left. Leading indicator of insufficient cooling.",
        |r: &Report| if r.decision.saturated { 1 } else { 0 }
    );
    gauge!(
        "smc_fand_emergency",
        "Zone is above its emergency threshold.",
        |r: &Report| if r.decision.emergency { 1 } else { 0 }
    );
    gauge!(
        "smc_fand_control_lost",
        "Duty readback disagreed with the commanded value.",
        |r: &Report| if r.control_lost { 1 } else { 0 }
    );
    gauge!(
        "smc_fand_assisting",
        "Zone duty was raised to lend cooling to a saturated peer zone.",
        |r: &Report| if r.assisting { 1 } else { 0 }
    );

    // Which sensor/group is driving each zone, as an info-style metric.
    s.push_str("# HELP smc_fand_driver Sensor and group currently driving the zone.\n");
    s.push_str("# TYPE smc_fand_driver gauge\n");
    for r in reports {
        s.push_str(&format!(
            "smc_fand_driver{{zone=\"{}\",sensor=\"{}\",group=\"{}\"}} 1\n",
            r.zone, r.decision.sensor, r.decision.group
        ));
    }

    s.push_str("# HELP smc_fand_duty_readback Duty as reported back by the BMC.\n");
    s.push_str("# TYPE smc_fand_duty_readback gauge\n");
    for r in reports {
        if let Some(rb) = r.readback {
            s.push_str(&format!(
                "smc_fand_duty_readback{{zone=\"{}\"}} {}\n",
                r.zone, rb
            ));
        }
    }

    s.push_str("# HELP smc_fand_heartbeat_timestamp_seconds Last successful control tick.\n");
    s.push_str("# TYPE smc_fand_heartbeat_timestamp_seconds gauge\n");
    s.push_str(&format!(
        "smc_fand_heartbeat_timestamp_seconds {}\n",
        now_secs()
    ));

    if let Err(e) = write_atomic(&cfg.metrics_path, &s) {
        // Losing metrics is not a reason to stop cooling the box.
        log(&format!("WARNING: metrics write failed: {e}"));
    }
}

fn control_loop(cfg: &Config) -> i32 {
    let mut zones = cfg.zones();
    let mut failures: u32 = 0;
    let mut tick: u32 = 0;
    // Last duty actually written per zone, so a steady state costs no IPMI calls.
    let mut written: HashMap<u8, u8> = HashMap::new();
    let dt = cfg.tick.as_secs_f64();

    for z in &zones {
        for g in &z.groups {
            log(&format!(
                "zone {} ({}) group {}: setpoint {}C, emergency {}C, sensors: {}",
                z.id,
                z.name,
                g.label,
                g.setpoint,
                g.emergency,
                g.sensors.join(", ")
            ));
        }
    }

    loop {
        tick = tick.wrapping_add(1);

        let temps = match read_temperatures(cfg) {
            Ok(t) => {
                failures = 0;
                t
            }
            Err(e) => {
                failures += 1;
                log(&format!(
                    "sensor read failed ({failures}/{}): {e}",
                    cfg.max_failures
                ));
                if failures >= cfg.max_failures {
                    // Blind. Ramp up first so the box stays cool while systemd
                    // restarts us, then hand back to the BMC.
                    for z in &zones {
                        let _ = set_duty(cfg, z.id, cfg.failure_duty);
                    }
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
        if tick % cfg.mode_check_every == 0 {
            match get_mode(cfg) {
                Ok(0x01) => {}
                Ok(m) => {
                    log(&format!("BMC left manual mode (now 0x{m:02x}); re-engaging"));
                    if let Err(e) = set_mode(cfg, MODE_FULL) {
                        log(&format!("WARNING: could not re-engage manual mode: {e}"));
                    }
                    written.clear(); // force duty rewrite after a mode change
                }
                Err(e) => log(&format!("WARNING: mode check failed: {e}")),
            }
        }

        // Pass one: decide each zone independently.
        let mut decisions = Vec::new();
        for z in zones.iter_mut() {
            match z.step(&temps, cfg, dt) {
                Ok(d) => decisions.push((z.id, d)),
                Err(e) => log(&format!("WARNING: {e}")),
            }
        }

        // Pass two: if any zone is saturated and still above setpoint, spend the
        // other zones' headroom on it. Measured on this box: with zone 1 pegged
        // and DIMMs at 84C, raising zone 0 from 56% to 98% dropped them 6C.
        let peer_saturated = decisions.iter().any(|(_, d)| d.saturated);
        let mut assisting: HashMap<u8, bool> = HashMap::new();
        if cfg.assist_enable && peer_saturated {
            for (id, d) in decisions.iter_mut() {
                if !d.saturated && d.duty < cfg.assist_duty {
                    if let Some(z) = zones.iter_mut().find(|z| z.id == *id) {
                        let boosted = z.apply_assist(cfg.assist_duty, cfg);
                        log(&format!(
                            "zone {id} assisting saturated peer: {}% -> {}%",
                            d.duty, boosted
                        ));
                        d.duty = boosted;
                        assisting.insert(*id, true);
                    }
                }
            }
        }

        // Pass three: apply. Skip the write entirely when nothing changed - in
        // steady state this drops the tick from six IPMI calls to one.
        let verify = tick % cfg.verify_every == 0;
        let mut reports = Vec::new();
        for (id, d) in decisions {
            let changed = written.get(&id) != Some(&d.duty);
            let mut readback = None;
            let mut control_lost = false;

            if changed {
                match set_duty(cfg, id, d.duty) {
                    Ok(()) => {
                        written.insert(id, d.duty);
                    }
                    Err(e) => log(&format!("WARNING: duty write failed for zone {id}: {e}")),
                }
            }

            if changed || verify {
                match get_duty(cfg, id) {
                    Ok(rb) => {
                        control_lost = (rb as i32 - d.duty as i32).abs() > READBACK_TOLERANCE;
                        readback = Some(rb);
                        if control_lost {
                            log(&format!(
                                "CONTROL LOST on zone {id}: commanded {}%, BMC reports {rb}% \
                                 - something is overriding us",
                                d.duty
                            ));
                            written.remove(&id); // force a rewrite next tick
                        }
                    }
                    Err(e) => log(&format!("WARNING: duty readback failed for zone {id}: {e}")),
                }
            }

            if d.emergency {
                log(&format!(
                    "EMERGENCY zone {id}: {} ({}) at {}C -> 100%",
                    d.sensor, d.group, d.temp
                ));
            } else if d.saturated {
                log(&format!(
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
                assisting: assisting.get(&id).copied().unwrap_or(false),
            });
        }

        write_metrics(cfg, &reports);

        // Touched last, and only after a full successful tick: the independent
        // watchdog treats a stale heartbeat as "this daemon is not in control".
        if let Err(e) = write_atomic(&cfg.heartbeat_path, &format!("{}\n", now_secs())) {
            log(&format!("WARNING: heartbeat write failed: {e}"));
        }

        sleep(cfg.tick);
    }
}

fn main() {
    let cfg = Config::from_env();
    let args: Vec<String> = env::args().skip(1).collect();

    // Escape hatch for foreground testing, where there is no ExecStopPost to
    // put things back.
    if args.iter().any(|a| a == "--restore") {
        restore_bmc(&cfg, "--restore requested");
        return;
    }

    log(&format!(
        "smc-fand starting: tick {}s, duty {}-{}%, slew +{}/-{} per tick, assist {}%",
        cfg.tick.as_secs(),
        cfg.min_duty,
        cfg.max_duty,
        cfg.slew_up,
        cfg.slew_down,
        if cfg.assist_enable {
            cfg.assist_duty.to_string()
        } else {
            "off".into()
        }
    ));

    assert_fan_thresholds(&cfg);

    if let Err(e) = set_mode(&cfg, MODE_FULL) {
        log(&format!("fatal: could not engage manual fan mode: {e}"));
        // Never started controlling, so the BMC still has it. Nothing to undo.
        std::process::exit(1);
    }
    log("manual fan mode engaged; BMC Standard mode is the fallback");

    let code = control_loop(&cfg);
    restore_bmc(&cfg, "control loop exited");
    std::process::exit(code);
}
