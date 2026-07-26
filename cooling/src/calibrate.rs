//! Zone discovery and gain measurement.
//!
//! This is the part that removes the fan-to-zone mapping from the operator's
//! problem. It probes which zone ids exist, notes which fans respond to each,
//! then sweeps each zone's duty and measures how far every sensor moves.
//!
//! Why an explicit sweep rather than passive learning: duty correlates with
//! load, so passively observed data says "more fans => hotter". Only by
//! *choosing* the duty ourselves do we break that confound. That is the whole
//! reason calibration is a deliberate, operator-initiated act.

use std::collections::HashMap;
use std::fs;
use std::thread::sleep;
use std::time::Duration;

use crate::authority::{Authority, Cell};
use crate::config::Config;
use crate::ipmi::{
    get_duty, read_fans, read_temperatures, restore_bmc, set_duty, set_mode, MODE_FULL,
};
use crate::util::{err, linear_fit, log, now_secs, out, warn, write_atomic, Error, Result};

pub struct Zone {
    pub id: u8,
    /// Fans whose RPM responded to this zone. Informational: the control loop
    /// never needs it, but it is the answer to "which fans are in which zone".
    pub fans: Vec<String>,
}

/// Probe zone ids and see which ones the BMC accepts and which fans move.
///
/// Two details matter for this to give the same answer twice.
///
/// The baseline is re-measured immediately before *every* probe rather than
/// once at the start. Fans take tens of seconds to spin down, so against a
/// single stale baseline a fan still coasting from the previous probe reads as
/// responding to the next one - which made discovery depend on whatever the
/// fans happened to be doing when the sweep began.
///
/// And each fan is assigned to the zone that moved it *most*, not to every zone
/// that moved it at all. Chassis airflow couples the fans slightly, so a strong
/// zone can drag a neighbour's tach up a little; without argmax that shows up
/// as one fan belonging to three zones, including ids that do not exist.
pub fn discover_zones(cfg: &Config) -> Result<Vec<Zone>> {
    log("discovering fan zones...");
    let low = 30u8;
    let high = 90u8;
    let settle = cfg.discover_settle;

    // (zone, fan) -> relative RPM rise over that probe's own baseline.
    let mut responses: HashMap<(u8, String), f64> = HashMap::new();
    let mut accepted: Vec<u8> = Vec::new();

    for z in 0..cfg.max_zone_probe {
        // Quiesce everything and take a fresh baseline for this probe alone.
        for other in 0..cfg.max_zone_probe {
            let _ = set_duty(cfg, other, low);
        }
        sleep(settle);
        let base = read_fans(cfg)?;

        // A zone that does not exist usually errors or refuses the value.
        // Note the BMC also happily accepts writes to ids that drive nothing,
        // so acceptance alone proves nothing - the fan response is the test.
        if set_duty(cfg, z, high).is_err() {
            continue;
        }
        let Ok(rb) = get_duty(cfg, z) else {
            let _ = set_duty(cfg, z, low);
            continue;
        };
        if (rb as i32 - high as i32).abs() > 10 {
            let _ = set_duty(cfg, z, low);
            continue;
        }
        accepted.push(z);

        sleep(settle);
        let raised = read_fans(cfg)?;
        let _ = set_duty(cfg, z, low);

        for (name, rpm) in &raised {
            let Some(b) = base.get(name) else { continue };
            if *b <= 0.0 {
                continue;
            }
            let rise = (rpm - b) / b;
            // 15% is well outside the BMC's ~140 RPM quantisation.
            if rise > 0.15 {
                responses.insert((z, name.clone()), rise);
            }
        }
    }

    // Winner-takes-all: each fan belongs to whichever zone moved it hardest.
    let mut owner: HashMap<String, (u8, f64)> = HashMap::new();
    for ((z, fan), rise) in &responses {
        let e = owner.entry(fan.clone()).or_insert((*z, *rise));
        if *rise > e.1 {
            *e = (*z, *rise);
        }
    }

    let mut zones: Vec<Zone> = Vec::new();
    for z in accepted {
        let mut fans: Vec<String> = owner
            .iter()
            .filter(|(_, (owner_zone, _))| *owner_zone == z)
            .map(|(fan, _)| fan.clone())
            .collect();
        fans.sort();

        if fans.is_empty() {
            log(&format!(
                "  zone {z}: accepted duty but owns no fan - ignoring"
            ));
            continue;
        }
        let detail: Vec<String> = fans
            .iter()
            .map(|f| {
                let rise = responses.get(&(z, f.clone())).copied().unwrap_or(0.0);
                format!("{f} (+{:.0}%)", rise * 100.0)
            })
            .collect();
        log(&format!("  zone {z}: {}", detail.join(", ")));
        zones.push(Zone { id: z, fans });
    }

    if zones.is_empty() {
        return Err(Error("no controllable fan zones found".into()));
    }
    Ok(zones)
}

/// Abort if anything is close to its limit. Measuring is never worth cooking.
fn check_safe(cfg: &Config, temps: &HashMap<String, f64>) -> Result<()> {
    for d in &cfg.domains {
        for s in &d.sensors {
            if let Some(t) = temps.get(s) {
                if *t >= d.emergency - cfg.calibrate_abort_margin {
                    return Err(Error(format!(
                        "{s} at {t}C is within {}C of the {} emergency limit ({}C)",
                        cfg.calibrate_abort_margin, d.name, d.emergency
                    )));
                }
            }
        }
    }
    Ok(())
}

fn sample_temps(cfg: &Config) -> Result<HashMap<String, f64>> {
    let mut acc: HashMap<String, (f64, u32)> = HashMap::new();
    for i in 0..cfg.calibrate_samples {
        if i > 0 {
            sleep(Duration::from_secs(3));
        }
        for (k, v) in read_temperatures(cfg)? {
            let e = acc.entry(k).or_insert((0.0, 0));
            e.0 += v;
            e.1 += 1;
        }
    }
    Ok(acc
        .into_iter()
        .map(|(k, (sum, n))| (k, sum / n as f64))
        .collect())
}

/// Run the sweep and write the authority matrix.
pub fn calibrate(cfg: &Config) -> Result<Authority> {
    // Stand the watchdog down for the duration - it would otherwise see the
    // service inactive, force BMC Standard mode, and fight the sweep. Bounded
    // by age on the watchdog side so a crash here cannot disable it forever.
    write_atomic(&cfg.calibrate_lock_path, &format!("{}\n", now_secs()))?;
    let result = calibrate_inner(cfg);
    let _ = fs::remove_file(&cfg.calibrate_lock_path);

    if result.is_err() {
        // Whatever went wrong, do not leave the chassis latched in manual mode
        // at whatever duty the sweep happened to stop at.
        restore_bmc(cfg, "calibration aborted");
    }
    result
}

fn calibrate_inner(cfg: &Config) -> Result<Authority> {
    set_mode(cfg, MODE_FULL)?;

    let temps = read_temperatures(cfg)?;
    check_safe(cfg, &temps)?;

    let zones = discover_zones(cfg)?;
    let steps = &cfg.calibrate_steps;
    let per_zone = steps.len() as u64 * (cfg.calibrate_settle.as_secs() + 15);
    log(&format!(
        "sweeping {} zones x {} steps, about {} minutes total",
        zones.len(),
        steps.len(),
        (per_zone * zones.len() as u64).div_ceil(60)
    ));

    // sensor -> (duty, temp) observations, per zone.
    let mut observations: HashMap<(u8, String), Vec<(f64, f64)>> = HashMap::new();

    for z in &zones {
        log(&format!("zone {}: sweeping {:?}", z.id, steps));
        for step in steps {
            // Hold every other zone fixed so the only thing varying is this one.
            for other in &zones {
                if other.id != z.id {
                    let _ = set_duty(cfg, other.id, cfg.calibrate_hold_duty.round() as u8);
                }
            }
            set_duty(cfg, z.id, step.round() as u8)?;

            sleep(cfg.calibrate_settle);
            let t = sample_temps(cfg)?;
            check_safe(cfg, &t)?;

            for (sensor, temp) in &t {
                observations
                    .entry((z.id, sensor.clone()))
                    .or_default()
                    .push((*step, *temp));
            }
            log(&format!("  zone {} at {:>5.1}%: sampled", z.id, step));
        }
    }

    // Fit a gain per (zone, domain): the strongest cooling response among the
    // domain's sensors, since a domain is limited by its hottest component.
    let mut raw: HashMap<(u8, String), (f64, f64, u32)> = HashMap::new();
    for z in &zones {
        for d in &cfg.domains {
            let mut best: Option<(f64, f64, u32)> = None;
            for sensor in &d.sensors {
                let Some(points) = observations.get(&(z.id, sensor.clone())) else {
                    continue;
                };
                let Some((slope, r2)) = linear_fit(points) else {
                    continue;
                };
                if best.map(|b| slope < b.0).unwrap_or(true) {
                    best = Some((slope, r2, points.len() as u32));
                }
            }
            if let Some((slope, r2, n)) = best {
                raw.insert((z.id, d.name.clone()), (slope, r2, n));
            }
        }
    }

    // Normalise per domain so the most effective zone is 1.0. A positive slope
    // means "more duty, hotter", which is noise or a confounder rather than a
    // real effect - clamp it to zero authority rather than believing it.
    let mut cells: HashMap<(u8, String), Cell> = HashMap::new();
    for d in &cfg.domains {
        let strongest = zones
            .iter()
            .filter_map(|z| raw.get(&(z.id, d.name.clone())))
            .map(|(slope, _, _)| if *slope < 0.0 { -slope } else { 0.0 })
            .fold(0.0f64, f64::max);

        for z in &zones {
            let Some((slope, r2, n)) = raw.get(&(z.id, d.name.clone())).copied() else {
                continue;
            };
            let cooling = if slope < 0.0 { -slope } else { 0.0 };
            let authority = if strongest > 1e-9 {
                (cooling / strongest).clamp(0.0, 1.0)
            } else {
                // Nothing measurably cooled this domain. Couple everything.
                1.0
            };
            // Confidence combines fit quality with signal size: a tidy fit on a
            // 0.01 C/% slope is still indistinguishable from sensor quantisation.
            let signal = (cooling / 0.05).clamp(0.0, 1.0);
            let confidence = if strongest > 1e-9 {
                (r2 * signal.max(0.2)).clamp(0.0, 1.0)
            } else {
                0.0
            };
            cells.insert(
                (z.id, d.name.clone()),
                Cell {
                    authority,
                    confidence,
                    gain: slope,
                    samples: n,
                },
            );
        }
    }

    let authority = Authority::from_cells(cells, now_secs());
    authority.write_file(&cfg.authority_path)?;

    out("");
    out("fan zones discovered:");
    for z in &zones {
        out(&format!("  zone {} -> {}", z.id, z.fans.join(", ")));
    }
    out("");
    out(&authority.render(cfg));
    out(&format!("written to {}", cfg.authority_path.display()));
    out("");
    out("Set SMC_AUTHORITY_MODE=calibrated and restart smc-fand to use it.");

    restore_bmc(cfg, "calibration complete");
    Ok(authority)
}

/// Refuse to sweep while the daemon is driving; two writers would produce
/// nonsense gains and the operator would never know.
pub fn ensure_daemon_stopped() -> Result<()> {
    let out = std::process::Command::new("systemctl")
        .args(["is-active", "--quiet", "smc-fand.service"])
        .status();
    match out {
        Ok(s) if s.success() => Err(Error(
            "smc-fand.service is running; stop it first \
             (`systemctl stop smc-fand`) so the sweep is the only writer"
                .into(),
        )),
        // systemctl missing or erroring is not proof the daemon is running, and
        // refusing to calibrate on that basis would be worse than proceeding.
        Ok(_) => Ok(()),
        Err(e) => {
            warn(&format!("could not query smc-fand.service ({e}); proceeding"));
            Ok(())
        }
    }
}

pub fn report_error(e: &Error) {
    err(&format!("calibration aborted: {e}"));
    err("the previous authority matrix, if any, is unchanged");
}
