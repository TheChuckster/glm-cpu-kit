//! Configuration, and the validation that must pass before we take the fans.
//!
//! Every failure here happens before manual mode is engaged, so a misconfigured
//! daemon exits with the BMC still in charge rather than aborting mid-loop with
//! the fans latched at whatever they happened to be.

use std::env;
use std::path::PathBuf;
use std::time::Duration;

use crate::bail;
use crate::util::{env_f64, env_str, env_u32, split_list, Result};

/// A set of components sharing a thermal limit - the unit of control.
///
/// Domains are deliberately independent of fan zones. A domain says "these
/// sensors must stay below this temperature"; which fans achieve that is the
/// authority matrix's problem, not the operator's.
pub struct DomainSpec {
    pub name: String,
    pub sensors: Vec<String>,
    pub setpoint: f64,
    pub emergency: f64,
    pub kp: f64,
    pub ki: f64,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum AuthorityMode {
    /// Declared in config: 1.0 for listed domains, 0.0 otherwise.
    Static,
    /// Measured by --calibrate, loaded from the authority file.
    Calibrated,
    /// No information at all: everything couples. Safe, loud.
    Uniform,
}

pub struct Config {
    pub ipmitool: String,
    pub timeout_bin: String,
    pub call_timeout: u64,
    pub tick: Duration,
    pub min_duty: f64,
    pub max_duty: f64,
    pub idle_duty: f64,
    pub slew_up: f64,
    pub slew_down: f64,
    pub max_failures: u32,
    pub verify_every: u32,
    pub mode_check_every: u32,
    pub fan_thresholds: Vec<(String, u32)>,
    pub metrics_path: PathBuf,
    pub heartbeat_path: PathBuf,

    pub domains: Vec<DomainSpec>,
    pub authority_mode: AuthorityMode,
    pub authority_path: PathBuf,
    /// Static-mode declaration: zone id -> domain names it serves.
    pub static_zone_domains: Vec<(u8, Vec<String>)>,

    // Calibration.
    pub calibrate_steps: Vec<f64>,
    pub calibrate_settle: Duration,
    pub calibrate_samples: u32,
    pub calibrate_hold_duty: f64,
    /// Abort the sweep when any sensor comes within this many degrees of its
    /// domain's emergency threshold.
    pub calibrate_abort_margin: f64,
    pub calibrate_lock_path: PathBuf,
    pub max_zone_probe: u8,
    /// Settle time around each discovery probe. Fans take tens of seconds to
    /// spin down; too short and a coasting fan is attributed to the wrong zone.
    pub discover_settle: Duration,

    // Drift detection.
    pub drift_enable: bool,
    pub drift_tolerance: f64,
}

/// Variables from the pre-domain layout. Silently ignoring these would run a
/// different profile than the operator believes is configured, which is exactly
/// the class of surprise this daemon exists to avoid.
const RETIRED_VARS: &[(&str, &str)] = &[
    ("SMC_ZONE0_SENSORS", "SMC_DOMAIN_<NAME>_SENSORS + SMC_ZONE0_DOMAINS"),
    ("SMC_ZONE1_SENSORS", "SMC_DOMAIN_<NAME>_SENSORS + SMC_ZONE1_DOMAINS"),
    ("SMC_ZONE0_SETPOINT", "SMC_DOMAIN_<NAME>_SETPOINT"),
    ("SMC_ZONE1_SETPOINT", "SMC_DOMAIN_<NAME>_SETPOINT"),
    ("SMC_ZONE0_EMERGENCY", "SMC_DOMAIN_<NAME>_EMERGENCY"),
    ("SMC_ZONE1_EMERGENCY", "SMC_DOMAIN_<NAME>_EMERGENCY"),
    ("SMC_ZONE0_AUX_SENSORS", "a domain of its own, listed in SMC_ZONE0_DOMAINS"),
    ("SMC_ZONE1_AUX_SENSORS", "a domain of its own, listed in SMC_ZONE1_DOMAINS"),
    ("SMC_ZONE0_AUX_SETPOINT", "SMC_DOMAIN_<NAME>_SETPOINT"),
    ("SMC_ZONE1_AUX_SETPOINT", "SMC_DOMAIN_<NAME>_SETPOINT"),
    ("SMC_ZONE0_AUX_EMERGENCY", "SMC_DOMAIN_<NAME>_EMERGENCY"),
    ("SMC_ZONE1_AUX_EMERGENCY", "SMC_DOMAIN_<NAME>_EMERGENCY"),
    ("SMC_ZONE0_KP", "SMC_DOMAIN_<NAME>_KP"),
    ("SMC_ZONE1_KP", "SMC_DOMAIN_<NAME>_KP"),
    ("SMC_ZONE0_KI", "SMC_DOMAIN_<NAME>_KI"),
    ("SMC_ZONE1_KI", "SMC_DOMAIN_<NAME>_KI"),
    ("SMC_ASSIST_ENABLE", "nothing - a saturated domain now couples to every zone automatically"),
    ("SMC_ASSIST_DUTY", "nothing - a saturated domain now couples to every zone automatically"),
];

// Defaults describe the reference chassis (Supermicro H13, EPYC 9575F, 12x DDR5
// RDIMM). They match smc-fand.env exactly, which that file asserts.
const DEFAULT_DOMAINS: &str = "cpu,dimm,periph";

fn default_domain(name: &str) -> Option<(&'static str, f64, f64)> {
    match name {
        "cpu" => Some((
            "CPU Temp,CPU_VRM0 Temp,CPU_VRM1 Temp,SOC_VRM Temp,VDDIO_VRM Temp",
            70.0,
            90.0,
        )),
        // Tighter than the CPU domain on purpose: DDR5 RDIMMs throttle around
        // 85C and this board has already logged a DIMM at 85C.
        "dimm" => Some(("DIMMA~F Temp,DIMMG~L Temp", 62.0, 82.0)),
        "periph" => Some(("System Temp,Peripheral Temp,NIC Temp", 60.0, 80.0)),
        _ => None,
    }
}

impl Config {
    pub fn from_env() -> Result<Self> {
        let mut retired = Vec::new();
        for (old, new) in RETIRED_VARS {
            if env::var(old).is_ok() {
                retired.push(format!("  {old} -> {new}"));
            }
        }
        if !retired.is_empty() {
            bail!(
                "these variables were retired when zones stopped owning sensors:\n{}\n\
                 See smc-fand.env. Refusing to start rather than silently running \
                 a different configuration than you believe is set.",
                retired.join("\n")
            );
        }

        let fan_thresholds = {
            let raw = env_str("SMC_FAN_THRESHOLDS", "FAN4:140,FANB:140");
            let mut out = Vec::new();
            for entry in raw.split(',').map(str::trim).filter(|e| !e.is_empty()) {
                let Some((name, value)) = entry.split_once(':') else {
                    bail!("SMC_FAN_THRESHOLDS entry {entry:?} is not NAME:RPM");
                };
                let Ok(rpm) = value.trim().parse::<u32>() else {
                    bail!("SMC_FAN_THRESHOLDS entry {entry:?} has a bad RPM");
                };
                out.push((name.trim().to_string(), rpm));
            }
            out
        };

        let domains = Self::domains_from_env()?;

        let authority_mode = match env_str("SMC_AUTHORITY_MODE", "static").as_str() {
            "static" => AuthorityMode::Static,
            "calibrated" => AuthorityMode::Calibrated,
            "uniform" => AuthorityMode::Uniform,
            other => bail!("SMC_AUTHORITY_MODE={other:?} must be static, calibrated or uniform"),
        };

        let mut static_zone_domains = Vec::new();
        for zone in 0u8..8 {
            let key = format!("SMC_ZONE{zone}_DOMAINS");
            let declared = match env::var(&key) {
                Ok(v) => split_list(&v),
                // Reference-chassis defaults for the two zones we know about.
                Err(_) if zone == 0 => split_list("cpu,dimm"),
                Err(_) if zone == 1 => split_list("periph,dimm"),
                Err(_) => continue,
            };
            for d in &declared {
                if !domains.iter().any(|s| &s.name == d) {
                    bail!("{key} references unknown domain {d:?}; known: {}",
                        domains.iter().map(|s| s.name.as_str()).collect::<Vec<_>>().join(", "));
                }
            }
            static_zone_domains.push((zone, declared));
        }
        if authority_mode == AuthorityMode::Static
            && static_zone_domains.iter().all(|(_, d)| d.is_empty())
        {
            bail!("SMC_AUTHORITY_MODE=static but no SMC_ZONE<N>_DOMAINS are set");
        }

        let calibrate_steps = {
            // Order matters as much as the values. A monotonic 25,50,75,100
            // sweep aliases background thermal drift straight onto the duty
            // ramp: if the box is warming for unrelated reasons, the fit reads
            // it as "more duty, hotter" and reports a positive gain. Observed
            // on the reference box - zone 1 measured +0.02 C/%, which is
            // physically impossible for a fan.
            //
            // 50,100,25,75 is balanced: cov(step_time, duty) == 0, so a linear
            // drift cancels out of the least-squares slope exactly.
            let raw = env_str("SMC_CALIBRATE_STEPS", "50,100,25,75");
            let mut out = Vec::new();
            for p in raw.split(',').map(str::trim).filter(|p| !p.is_empty()) {
                let Ok(v) = p.parse::<f64>() else {
                    bail!("SMC_CALIBRATE_STEPS entry {p:?} is not a number");
                };
                if !v.is_finite() || !(0.0..=100.0).contains(&v) {
                    bail!("SMC_CALIBRATE_STEPS entry {p:?} is outside 0..=100");
                }
                out.push(v);
            }
            if out.len() < 2 {
                bail!("SMC_CALIBRATE_STEPS needs at least two distinct duty levels");
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
            slew_up: env_f64("SMC_SLEW_UP", 30.0)?,
            slew_down: env_f64("SMC_SLEW_DOWN", 3.0)?,
            max_failures: env_u32("SMC_MAX_FAILURES", 3)?,
            verify_every: env_u32("SMC_VERIFY_EVERY", 12)?,
            mode_check_every: env_u32("SMC_MODE_CHECK_EVERY", 6)?,
            fan_thresholds,
            metrics_path: PathBuf::from(env_str(
                "SMC_METRICS_PATH",
                "/var/lib/prometheus/node-exporter/smc_fand.prom",
            )),
            heartbeat_path: PathBuf::from(env_str("SMC_HEARTBEAT_PATH", "/run/smc-fand/heartbeat")),

            domains,
            authority_mode,
            authority_path: PathBuf::from(env_str(
                "SMC_AUTHORITY_PATH",
                "/var/lib/smc-fand/authority",
            )),
            static_zone_domains,

            calibrate_steps,
            calibrate_settle: Duration::from_secs(env_u32("SMC_CALIBRATE_SETTLE", 120)?.max(5) as u64),
            calibrate_samples: env_u32("SMC_CALIBRATE_SAMPLES", 5)?.max(1),
            calibrate_hold_duty: env_f64("SMC_CALIBRATE_HOLD_DUTY", 60.0)?,
            calibrate_abort_margin: env_f64("SMC_CALIBRATE_ABORT_MARGIN", 8.0)?,
            calibrate_lock_path: PathBuf::from(env_str(
                "SMC_CALIBRATE_LOCK",
                "/run/smc-fand/calibrating",
            )),
            max_zone_probe: env_u32("SMC_MAX_ZONE_PROBE", 4)?.clamp(1, 8) as u8,
            discover_settle: Duration::from_secs(env_u32("SMC_DISCOVER_SETTLE", 45)?.max(10) as u64),

            drift_enable: env_u32("SMC_DRIFT_ENABLE", 1)? != 0,
            drift_tolerance: env_f64("SMC_DRIFT_TOLERANCE", 0.35)?,
        };

        cfg.validate()?;
        Ok(cfg)
    }

    fn domains_from_env() -> Result<Vec<DomainSpec>> {
        let names = split_list(&env_str("SMC_DOMAINS", DEFAULT_DOMAINS));
        if names.is_empty() {
            bail!("SMC_DOMAINS is empty; at least one thermal domain is required");
        }

        let mut out = Vec::new();
        for name in names {
            let key = name.to_uppercase().replace(['-', ' '], "_");
            let defaults = default_domain(&name);

            let sensors_default = defaults.map(|d| d.0).unwrap_or("");
            let sensors = split_list(&env_str(
                &format!("SMC_DOMAIN_{key}_SENSORS"),
                sensors_default,
            ));
            if sensors.is_empty() {
                bail!(
                    "domain {name:?} has no sensors; set SMC_DOMAIN_{key}_SENSORS"
                );
            }

            let setpoint = env_f64(
                &format!("SMC_DOMAIN_{key}_SETPOINT"),
                defaults.map(|d| d.1).unwrap_or(65.0),
            )?;
            let emergency = env_f64(
                &format!("SMC_DOMAIN_{key}_EMERGENCY"),
                defaults.map(|d| d.2).unwrap_or(85.0),
            )?;
            if emergency <= setpoint {
                bail!(
                    "domain {name:?}: emergency ({emergency}) must exceed setpoint ({setpoint})"
                );
            }

            out.push(DomainSpec {
                name: name.clone(),
                sensors,
                setpoint,
                emergency,
                kp: env_f64(&format!("SMC_DOMAIN_{key}_KP"), 2.5)?,
                // Halved from 0.05 after zone 0 visibly hunted under load
                // (65 -> 75 -> 55 -> 62 -> 58) while CPU temperature barely moved.
                ki: env_f64(&format!("SMC_DOMAIN_{key}_KI"), 0.025)?,
            });
        }
        Ok(out)
    }

    fn validate(&self) -> Result<()> {
        // These are used as `%` divisors; 0 is the intuitive way to try to
        // disable a periodic check and would abort the process on tick 1.
        if self.verify_every == 0 {
            bail!("SMC_VERIFY_EVERY must be >= 1");
        }
        if self.mode_check_every == 0 {
            bail!("SMC_MODE_CHECK_EVERY must be >= 1");
        }
        if self.max_failures == 0 {
            bail!("SMC_MAX_FAILURES must be >= 1");
        }
        // f64::clamp panics when min > max, so catch the inversion here.
        if self.min_duty > self.max_duty {
            bail!(
                "SMC_MIN_DUTY ({}) exceeds SMC_MAX_DUTY ({})",
                self.min_duty,
                self.max_duty
            );
        }
        if !(0.0..=100.0).contains(&self.min_duty) || !(0.0..=100.0).contains(&self.max_duty) {
            bail!("duty bounds must lie within 0..=100");
        }
        // Negative slew inverts the clamp bounds and panics on the first tick.
        // "-3" is a natural thing to write, given the banner renders "+30/-3".
        if self.slew_up <= 0.0 || self.slew_down <= 0.0 {
            bail!("SMC_SLEW_UP and SMC_SLEW_DOWN must be positive (the sign is implied)");
        }
        if !(0.0..=100.0).contains(&self.calibrate_hold_duty) {
            bail!("SMC_CALIBRATE_HOLD_DUTY must lie within 0..=100");
        }
        if self.calibrate_abort_margin < 0.0 {
            bail!("SMC_CALIBRATE_ABORT_MARGIN must not be negative");
        }
        if !(0.0..=1.0).contains(&self.drift_tolerance) {
            bail!("SMC_DRIFT_TOLERANCE must lie within 0..=1");
        }
        Ok(())
    }

    /// Zone ids the controller drives. In static mode this comes from the
    /// declaration; otherwise from the calibrated matrix, falling back to the
    /// declaration so an uncalibrated box still has something to drive.
    pub fn declared_zones(&self) -> Vec<u8> {
        self.static_zone_domains.iter().map(|(z, _)| *z).collect()
    }
}
