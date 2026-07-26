//! Thermal domains, the PI loop, and duty allocation through the matrix.
//!
//! A domain asks for a duty; the authority matrix decides which zones have to
//! supply it. Zones no longer own sensors, which is what removes the fan-to-
//! zone and intake-versus-exhaust knowledge from the operator's problem.

use std::collections::HashMap;

use crate::authority::Authority;
use crate::config::{Config, DomainSpec};
use crate::util::esc;

/// Runtime state for one thermal domain.
pub struct Domain {
    pub name: String,
    pub setpoint: f64,
    pub emergency: f64,
    sensors: Vec<String>,
    kp: f64,
    ki: f64,
    integral: f64,
}

impl Domain {
    pub fn new(spec: &DomainSpec) -> Self {
        Domain {
            name: spec.name.clone(),
            setpoint: spec.setpoint,
            emergency: spec.emergency,
            sensors: spec.sensors.clone(),
            kp: spec.kp,
            ki: spec.ki,
            integral: 0.0,
        }
    }

    pub fn sensors(&self) -> &[String] {
        &self.sensors
    }
}

/// What one domain wants, in duty units: "the duty a fully-authoritative zone
/// must run to hold me at setpoint".
#[derive(Clone)]
pub struct DomainDemand {
    pub domain: String,
    pub demand: f64,
    pub temp: f64,
    pub sensor: String,
    pub setpoint: f64,
    pub error: f64,
    pub emergency: bool,
    pub saturated: bool,
}

#[derive(Clone)]
pub struct ZoneDuty {
    pub zone: u8,
    pub duty: u8,
    /// Which domain won the allocation, and through what authority.
    pub driver: String,
    pub driver_authority: f64,
    pub coupled: bool,
}

pub struct Outcome {
    pub demands: Vec<DomainDemand>,
    pub zones: Vec<ZoneDuty>,
    /// Any domain in emergency forces every zone to 100%, matrix ignored.
    pub emergency: bool,
    /// Domains whose sensors were all unreadable.
    pub unreadable: Vec<String>,
}

pub struct Controller {
    pub domains: Vec<Domain>,
    /// False when the fan-fail thresholds could not be confirmed at startup.
    /// Exported so the operator learns about it from an alert rather than by
    /// noticing the chassis has started revving again.
    pub thresholds_ok: bool,
    /// Last duty the BMC is believed to have *accepted*, per zone. Only updated
    /// after a successful write, so a failed write cannot anchor the slew
    /// limiter to a duty the fans never reached.
    pub applied: HashMap<u8, u8>,
}

impl Controller {
    pub fn new(cfg: &Config) -> Self {
        Controller {
            domains: cfg.domains.iter().map(Domain::new).collect(),
            thresholds_ok: true,
            applied: HashMap::new(),
        }
    }

    /// One control step. `slew` is disabled when false, which --explain uses so
    /// its output is a pure function of the inputs.
    pub fn step(
        &mut self,
        temps: &HashMap<String, f64>,
        cfg: &Config,
        authority: &Authority,
        dt: f64,
        slew: bool,
    ) -> Outcome {
        let mut demands = Vec::new();
        let mut unreadable = Vec::new();

        for d in self.domains.iter_mut() {
            match d.demand(temps, cfg, dt) {
                Some(dd) => demands.push(dd),
                None => unreadable.push(d.name.clone()),
            }
        }

        let emergency = demands.iter().any(|d| d.emergency);
        let zones = self.allocate(&demands, cfg, authority, emergency, slew);

        Outcome {
            demands,
            zones,
            emergency,
            unreadable,
        }
    }

    /// Turn per-domain demands into per-zone duty.
    ///
    /// `duty[z] = max over domains of ( demand[d] * authority(z, d) )`
    ///
    /// Two rules override the matrix:
    ///   * Emergency: every zone goes to 100%. A learned zero must never be
    ///     able to hold a zone back while something is over its limit.
    ///   * A saturated domain couples to every zone regardless of authority.
    ///     A domain out of headroom is not a place to be optimising, and this
    ///     is what used to be bolted on as "cross-zone assist".
    fn allocate(
        &mut self,
        demands: &[DomainDemand],
        cfg: &Config,
        authority: &Authority,
        emergency: bool,
        slew: bool,
    ) -> Vec<ZoneDuty> {
        let zones: Vec<u8> = if authority.zones().is_empty() {
            cfg.declared_zones()
        } else {
            authority.zones().to_vec()
        };

        let mut out = Vec::new();
        for z in zones {
            if emergency {
                let duty = cfg.max_duty.round() as u8;
                let hottest = demands
                    .iter()
                    .filter(|d| d.emergency)
                    .max_by(|a, b| a.error.total_cmp(&b.error));
                out.push(ZoneDuty {
                    zone: z,
                    duty,
                    driver: hottest.map(|d| d.domain.clone()).unwrap_or_default(),
                    driver_authority: 1.0,
                    coupled: true,
                });
                continue;
            }

            let mut best = cfg.min_duty;
            let mut driver = String::new();
            let mut driver_authority = 1.0;
            let mut coupled = false;

            for d in demands {
                // Saturation couples; otherwise consult the matrix.
                let (a, is_coupled) = if d.saturated {
                    (1.0, true)
                } else {
                    (authority.effective(z, &d.domain), false)
                };
                let contribution = d.demand * a;
                if contribution > best || driver.is_empty() {
                    best = contribution.max(cfg.min_duty);
                    driver = d.domain.clone();
                    driver_authority = a;
                    coupled = is_coupled;
                }
            }

            let mut duty = best.clamp(cfg.min_duty, cfg.max_duty);

            // Asymmetric slew: react fast, taper slowly. Heat arrives faster
            // than it leaves, and the error directions have different costs -
            // ramping up late risks throttling, ramping down late costs noise.
            if slew {
                if let Some(prev) = self.applied.get(&z) {
                    let prev = *prev as f64;
                    duty = duty
                        .clamp(prev - cfg.slew_down, prev + cfg.slew_up)
                        .clamp(cfg.min_duty, cfg.max_duty);
                }
            }

            out.push(ZoneDuty {
                zone: z,
                duty: duty.round() as u8,
                driver,
                driver_authority,
                coupled,
            });
        }
        out
    }
}

impl Domain {
    /// PI step for this domain. Returns None when no sensor could be read.
    fn demand(
        &mut self,
        temps: &HashMap<String, f64>,
        cfg: &Config,
        dt: f64,
    ) -> Option<DomainDemand> {
        let (sensor, temp) = self
            .sensors
            .iter()
            .filter_map(|n| temps.get(n).map(|t| (n.clone(), *t)))
            .max_by(|a, b| a.1.total_cmp(&b.1))?;

        let error = temp - self.setpoint;

        if temp >= self.emergency {
            self.integral = 0.0;
            return Some(DomainDemand {
                domain: self.name.clone(),
                demand: cfg.max_duty,
                temp,
                sensor,
                setpoint: self.setpoint,
                error,
                emergency: true,
                saturated: true,
            });
        }

        let proportional = self.kp * error;

        // Anti-windup: only accumulate while the output is off the rails.
        // Without this the integral keeps growing at 100% and the fans stay
        // maxed long after the box has cooled.
        let candidate = cfg.idle_duty + proportional + self.ki * (self.integral + error * dt);
        if candidate > cfg.min_duty && candidate < cfg.max_duty {
            self.integral += error * dt;
        }

        let raw = cfg.idle_duty + proportional + self.ki * self.integral;

        Some(DomainDemand {
            domain: self.name.clone(),
            demand: raw.clamp(cfg.min_duty, cfg.max_duty),
            temp,
            sensor,
            setpoint: self.setpoint,
            error,
            emergency: false,
            saturated: raw >= cfg.max_duty,
        })
    }
}

/// Human-readable trace of one decision, for --explain.
///
/// "Why is this fan at 60%?" should have a direct answer, and this is it.
pub fn explain(outcome: &Outcome, authority: &Authority, cfg: &Config) -> String {
    let mut s = String::new();
    s.push_str(&authority.render(cfg));
    s.push('\n');

    s.push_str("domain demands\n");
    for d in &outcome.demands {
        s.push_str(&format!(
            "  {:<8} {:>6.1}%   {} {:.1}C vs setpoint {:.1}C (error {:+.1}){}{}\n",
            d.domain,
            d.demand,
            d.sensor,
            d.temp,
            d.setpoint,
            d.error,
            if d.emergency { "  EMERGENCY" } else { "" },
            if d.saturated && !d.emergency {
                "  SATURATED"
            } else {
                ""
            },
        ));
    }
    for u in &outcome.unreadable {
        s.push_str(&format!("  {u:<8} UNREADABLE - no sensor resolved\n"));
    }

    s.push_str("\nzone duty\n");
    for z in &outcome.zones {
        s.push_str(&format!(
            "  zone {} -> {:>3}%   driven by {} (authority {:.2}){}\n",
            z.zone,
            z.duty,
            if z.driver.is_empty() { "floor" } else { &z.driver },
            z.driver_authority,
            if z.coupled { "  [coupled]" } else { "" },
        ));
    }
    s
}

pub struct MetricInputs<'a> {
    pub outcome: &'a Outcome,
    pub authority: &'a Authority,
    pub readback: &'a HashMap<u8, Option<u8>>,
    pub control_lost: &'a HashMap<u8, bool>,
    pub write_failed: &'a HashMap<u8, bool>,
    pub drift: &'a HashMap<(u8, String), bool>,
    pub heartbeat: u64,
    pub thresholds_ok: bool,
}

pub fn render_metrics(m: &MetricInputs, cfg: &Config) -> String {
    let mut s = String::new();

    s.push_str("# HELP smc_fand_up smc-fand is running and in control.\n");
    s.push_str("# TYPE smc_fand_up gauge\n");
    s.push_str("smc_fand_up 1\n");

    macro_rules! domain_gauge {
        ($name:expr, $help:expr, $val:expr) => {{
            s.push_str(&format!("# HELP {} {}\n# TYPE {} gauge\n", $name, $help, $name));
            for d in &m.outcome.demands {
                s.push_str(&format!(
                    "{}{{domain=\"{}\"}} {}\n",
                    $name,
                    esc(&d.domain),
                    $val(d)
                ));
            }
        }};
    }

    domain_gauge!(
        "smc_fand_domain_demand_percent",
        "Duty this domain needs from a fully-authoritative zone.",
        |d: &DomainDemand| format!("{:.1}", d.demand)
    );
    domain_gauge!(
        "smc_fand_domain_temperature_celsius",
        "Hottest sensor in the domain.",
        |d: &DomainDemand| format!("{:.1}", d.temp)
    );
    domain_gauge!(
        "smc_fand_domain_setpoint_celsius",
        "Target temperature for the domain.",
        |d: &DomainDemand| format!("{:.1}", d.setpoint)
    );
    domain_gauge!(
        "smc_fand_domain_error_celsius",
        "Degrees above setpoint. Leading indicator, and stable across driver changes.",
        |d: &DomainDemand| format!("{:.1}", d.error)
    );
    domain_gauge!(
        "smc_fand_domain_saturated",
        "Domain is demanding maximum duty with no headroom left.",
        |d: &DomainDemand| if d.saturated { "1" } else { "0" }.to_string()
    );
    domain_gauge!(
        "smc_fand_domain_emergency",
        "Domain is above its emergency threshold.",
        |d: &DomainDemand| if d.emergency { "1" } else { "0" }.to_string()
    );

    macro_rules! zone_gauge {
        ($name:expr, $help:expr, $val:expr) => {{
            s.push_str(&format!("# HELP {} {}\n# TYPE {} gauge\n", $name, $help, $name));
            for z in &m.outcome.zones {
                s.push_str(&format!("{}{{zone=\"{}\"}} {}\n", $name, z.zone, $val(z)));
            }
        }};
    }

    zone_gauge!(
        "smc_fand_duty_percent",
        "Commanded fan duty cycle.",
        |z: &ZoneDuty| z.duty.to_string()
    );
    zone_gauge!(
        "smc_fand_zone_coupled",
        "Zone duty was forced by a saturated or emergency domain, bypassing the authority matrix.",
        |z: &ZoneDuty| if z.coupled { "1" } else { "0" }.to_string()
    );
    zone_gauge!(
        "smc_fand_duty_readback",
        "Duty reported back by the BMC, or -1 when not sampled this tick.",
        |z: &ZoneDuty| m
            .readback
            .get(&z.zone)
            .copied()
            .flatten()
            .map(|v| v as i32)
            .unwrap_or(-1)
            .to_string()
    );
    zone_gauge!(
        "smc_fand_control_lost",
        "Duty readback disagreed with the commanded value.",
        |z: &ZoneDuty| if m.control_lost.get(&z.zone).copied().unwrap_or(false) {
            "1"
        } else {
            "0"
        }
        .to_string()
    );
    zone_gauge!(
        "smc_fand_write_failed",
        "The last duty write to this zone returned an error.",
        |z: &ZoneDuty| if m.write_failed.get(&z.zone).copied().unwrap_or(false) {
            "1"
        } else {
            "0"
        }
        .to_string()
    );

    // Emitted for every CONFIGURED domain, not just those that produced a
    // demand. A domain whose sensors vanish drops out of outcome.demands
    // entirely, so without this its saturation and emergency series would
    // simply disappear and their alerts would become unevaluable - silence
    // that reads identically to "healthy".
    s.push_str("# HELP smc_fand_domain_readable At least one of the domain's sensors could be read.\n");
    s.push_str("# TYPE smc_fand_domain_readable gauge\n");
    for d in &cfg.domains {
        let readable = m.outcome.demands.iter().any(|x| x.domain == d.name);
        s.push_str(&format!(
            "smc_fand_domain_readable{{domain=\"{}\"}} {}\n",
            esc(&d.name),
            if readable { 1 } else { 0 }
        ));
    }

    s.push_str("# HELP smc_fand_fan_threshold_ok Fan-fail thresholds were confirmed at startup.\n");
    s.push_str("# TYPE smc_fand_fan_threshold_ok gauge\n");
    s.push_str(&format!(
        "smc_fand_fan_threshold_ok {}\n",
        if m.thresholds_ok { 1 } else { 0 }
    ));

    s.push_str("# HELP smc_fand_driver Domain currently setting each zone's duty.\n");
    s.push_str("# TYPE smc_fand_driver gauge\n");
    for z in &m.outcome.zones {
        if !z.driver.is_empty() {
            s.push_str(&format!(
                "smc_fand_driver{{zone=\"{}\",domain=\"{}\"}} 1\n",
                z.zone,
                esc(&z.driver)
            ));
        }
    }

    s.push_str("# HELP smc_fand_authority Effective authority of a zone over a domain. 1.0 = fully coupled.\n");
    s.push_str("# TYPE smc_fand_authority gauge\n");
    for z in &m.outcome.zones {
        for d in &cfg.domains {
            s.push_str(&format!(
                "smc_fand_authority{{zone=\"{}\",domain=\"{}\"}} {:.3}\n",
                z.zone,
                esc(&d.name),
                m.authority.effective(z.zone, &d.name)
            ));
        }
    }

    s.push_str("# HELP smc_fand_authority_confidence Confidence in the measured authority.\n");
    s.push_str("# TYPE smc_fand_authority_confidence gauge\n");
    for z in &m.outcome.zones {
        for d in &cfg.domains {
            let c = m
                .authority
                .cell(z.zone, &d.name)
                .map(|c| c.confidence)
                .unwrap_or(0.0);
            s.push_str(&format!(
                "smc_fand_authority_confidence{{zone=\"{}\",domain=\"{}\"}} {c:.3}\n",
                z.zone,
                esc(&d.name)
            ));
        }
    }

    s.push_str("# HELP smc_fand_authority_drift Measured response no longer matches the calibrated matrix; recalibrate.\n");
    s.push_str("# TYPE smc_fand_authority_drift gauge\n");
    for z in &m.outcome.zones {
        for d in &cfg.domains {
            let flagged = m
                .drift
                .get(&(z.zone, d.name.clone()))
                .copied()
                .unwrap_or(false);
            s.push_str(&format!(
                "smc_fand_authority_drift{{zone=\"{}\",domain=\"{}\"}} {}\n",
                z.zone,
                esc(&d.name),
                if flagged { 1 } else { 0 }
            ));
        }
    }

    s.push_str("# HELP smc_fand_authority_timestamp_seconds When the matrix was calibrated; 0 for static or uniform.\n");
    s.push_str("# TYPE smc_fand_authority_timestamp_seconds gauge\n");
    s.push_str(&format!(
        "smc_fand_authority_timestamp_seconds {}\n",
        m.authority.calibrated_at
    ));

    s.push_str("# HELP smc_fand_heartbeat_timestamp_seconds Last fully successful control tick.\n");
    s.push_str("# TYPE smc_fand_heartbeat_timestamp_seconds gauge\n");
    s.push_str(&format!(
        "smc_fand_heartbeat_timestamp_seconds {}\n",
        m.heartbeat
    ));

    s
}
