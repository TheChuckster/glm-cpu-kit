//! smc-fand - temperature-driven fan control for Supermicro H13/X13 BMCs.
//!
//! Puts the BMC into manual ("Full") fan mode and drives each fan zone's duty
//! from IPMI temperature sensors. The BMC's own automatic curve is retained as
//! the fallback layer, not the primary controller.
//!
//! # Domains and authority
//!
//! Control is organised around *thermal domains* - sets of sensors sharing a
//! limit - rather than fan zones. Each domain runs a PI loop and asks for a
//! duty; an *authority matrix* decides which zones must supply it. See
//! `authority.rs` for why this removes fan-to-zone and intake-versus-exhaust
//! knowledge from the operator's problem, and why full coupling is the safe
//! default.
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
//!   * Any domain at or above its emergency limit forces every zone to 100%,
//!     bypassing the PI output, the slew limiter and the authority matrix.
//!   * A saturated domain couples to every zone regardless of learned
//!     authority - being out of headroom is not the time to optimise.
//!   * *Any* consecutive IPMI failure - sensor read, duty write, readback or
//!     mode check - counts toward `MAX_FAILURES`, after which we hand back to
//!     the BMC and exit non-zero so systemd restarts us.
//!   * Duty writes are read back. A mismatch means something overrode us
//!     (chassis intrusion ramps do this), logged and exported as
//!     `smc_fand_control_lost` rather than passing silently.
//!   * Fan-failure thresholds are re-asserted and verified at startup, because
//!     a BMC firmware update silently reverts them and resurrects the low-RPM
//!     fan-fail ramp this daemon exists to avoid.
//!   * The heartbeat is written only when every zone was actually commanded.
//!     An independent systemd timer watches its age and forces BMC control if
//!     we wedge in a way systemd cannot see.

mod authority;
mod calibrate;
mod config;
mod control;
mod ipmi;
mod util;

use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::Read as _;
use std::thread::sleep;
use std::time::Instant;

use authority::{Authority, DriftMonitor};
use config::{AuthorityMode, Config};
use control::{explain, render_metrics, Controller, MetricInputs};
use ipmi::{
    assert_fan_thresholds, get_duty, get_mode, parse_temperatures, read_temperatures, restore_bmc,
    set_duty, set_mode, MODE_FULL,
};
use util::{err, log, now_secs, out, warn, write_atomic, Result};

/// The BMC quantises duty, so a readback rarely equals what we wrote exactly
/// (writing 0x20 reads back 0x1f). Anything within this margin is a match;
/// beyond it, we have lost control of the zone.
const READBACK_TOLERANCE: i32 = 4;

fn clear_metrics(cfg: &Config) {
    match fs::remove_file(&cfg.metrics_path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => warn(&format!("could not remove metrics file: {e}")),
    }
}

fn control_loop(cfg: &Config, ctrl: &mut Controller, authority: &Authority) -> i32 {
    let mut failures: u32 = 0;
    let mut tick: u32 = 0;
    let mut written: HashMap<u8, u8> = HashMap::new();
    let mut drift = DriftMonitor::new();
    let mut last_instant = Instant::now();

    macro_rules! bail_if_exhausted {
        ($what:expr) => {
            if failures >= cfg.max_failures {
                err(&format!(
                    "{failures} consecutive IPMI failures ({}); handing control back to the BMC",
                    $what
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
                    // recompute duty from its own curve within milliseconds.
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
                    written.clear();
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
            bail_if_exhausted!("mode check");
        }

        let outcome = ctrl.step(&temps, cfg, authority, dt, true);

        for name in &outcome.unreadable {
            failures += 1;
            err(&format!(
                "domain {name:?} has no readable sensors ({failures}/{}) - check that the \
                 configured names still match the BMC",
                cfg.max_failures
            ));
        }
        bail_if_exhausted!("unreadable domain");

        let verify = tick.is_multiple_of(cfg.verify_every);
        let mut readback: HashMap<u8, Option<u8>> = HashMap::new();
        let mut control_lost: HashMap<u8, bool> = HashMap::new();
        let mut write_failed: HashMap<u8, bool> = HashMap::new();
        let mut all_commanded = outcome.unreadable.is_empty();

        for z in &outcome.zones {
            let changed = written.get(&z.zone) != Some(&z.duty);
            let mut failed = false;

            if changed {
                match set_duty(cfg, z.zone, z.duty) {
                    Ok(()) => {
                        written.insert(z.zone, z.duty);
                        failures = 0;
                        // Only now may the slew limiter anchor here.
                        ctrl.applied.insert(z.zone, z.duty);
                    }
                    Err(e) => {
                        failed = true;
                        all_commanded = false;
                        failures += 1;
                        err(&format!(
                            "duty write failed for zone {} ({failures}/{}): {e}",
                            z.zone, cfg.max_failures
                        ));
                    }
                }
            }
            write_failed.insert(z.zone, failed);

            // Do not read back after a failed write: the BMC still holds the old
            // duty, which would look like an override rather than our own error.
            let mut lost = false;
            if (changed && !failed) || verify {
                match get_duty(cfg, z.zone) {
                    Ok(rb) => {
                        failures = 0;
                        lost = (rb as i32 - z.duty as i32).abs() > READBACK_TOLERANCE;
                        readback.insert(z.zone, Some(rb));
                        if lost {
                            err(&format!(
                                "CONTROL LOST on zone {}: commanded {}%, BMC reports {rb}% \
                                 - something is overriding us",
                                z.zone, z.duty
                            ));
                            written.remove(&z.zone);
                            // Re-anchor to physical reality so the next step
                            // slews from where the fans actually are.
                            ctrl.applied.insert(z.zone, rb);
                        }
                    }
                    Err(e) => {
                        failures += 1;
                        warn(&format!(
                            "duty readback failed for zone {} ({failures}/{}): {e}",
                            z.zone, cfg.max_failures
                        ));
                        readback.insert(z.zone, None);
                    }
                }
            } else {
                readback.insert(z.zone, None);
            }
            control_lost.insert(z.zone, lost);
        }
        bail_if_exhausted!("duty commands");

        for d in &outcome.demands {
            if d.emergency {
                err(&format!(
                    "EMERGENCY domain {}: {} at {}C -> all zones 100%",
                    d.domain, d.sensor, d.temp
                ));
            } else if d.saturated {
                warn(&format!(
                    "SATURATED domain {}: {} at {}C, setpoint {}C, no headroom left",
                    d.domain, d.sensor, d.temp, d.setpoint
                ));
            }
        }

        // Drift observation is gated on everything that would confound it.
        if cfg.drift_enable {
            let quiet = !outcome.emergency
                && !outcome.demands.iter().any(|d| d.saturated)
                && !control_lost.values().any(|v| *v)
                && !write_failed.values().any(|v| *v);
            for z in &outcome.zones {
                for d in &outcome.demands {
                    drift.observe(z.zone, &d.domain, z.duty as f64, d.temp, quiet);
                }
            }
            if tick.is_multiple_of(120) {
                for (zone, domain) in drift.evaluate(authority, cfg.drift_tolerance) {
                    warn(&format!(
                        "authority drift on zone {zone} / domain {domain}: measured response no \
                         longer matches the calibrated matrix. Re-run --calibrate. \
                         (Control is unaffected; this is advisory.)"
                    ));
                }
            }
        }

        let metrics = render_metrics(
            &MetricInputs {
                outcome: &outcome,
                authority,
                readback: &readback,
                control_lost: &control_lost,
                write_failed: &write_failed,
                drift: &drift.flagged,
                heartbeat: now_secs(),
            },
            cfg,
        );
        if let Err(e) = write_atomic(&cfg.metrics_path, &metrics) {
            // Losing metrics is not a reason to stop cooling the box.
            warn(&format!("metrics write failed: {e}"));
        }

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

/// Print the matrix, each domain's demand, and the resulting duty for a set of
/// sensor readings fed on stdin in `ipmitool sdr type Temperature` format.
///
/// This makes the controller testable without hardware, which is how static
/// mode was checked against the previous implementation.
fn run_explain(cfg: &Config, authority: &Authority) -> Result<()> {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input)?;

    let temps = if input.trim().is_empty() {
        read_temperatures(cfg)?
    } else {
        parse_temperatures(&input)?
    };

    let mut ctrl = Controller::new(cfg);
    // No slew: a single shot should be a pure function of its inputs.
    let outcome = ctrl.step(&temps, cfg, authority, cfg.tick.as_secs_f64(), false);
    out(&explain(&outcome, authority, cfg));
    Ok(())
}

fn startup_checks(cfg: &Config, ctrl: &Controller) -> Result<()> {
    // Resolve configured sensor names against what the BMC actually reports,
    // before taking the fans. A mistyped name would otherwise leave a whole
    // domain inert with no log, no metric and no error.
    let temps = read_temperatures(cfg)?;
    let mut fatal = false;

    for d in &ctrl.domains {
        let (found, missing): (Vec<_>, Vec<_>) =
            d.sensors().iter().partition(|n| temps.contains_key(*n));
        if !missing.is_empty() {
            warn(&format!(
                "domain {}: no such sensor: {}",
                d.name,
                missing.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
            ));
        }
        if found.is_empty() {
            err(&format!(
                "domain {} resolved zero sensors - it would be silently inert",
                d.name
            ));
            fatal = true;
        } else {
            log(&format!(
                "domain {}: setpoint {}C, emergency {}C, {} of {} sensors resolved",
                d.name,
                d.setpoint,
                d.emergency,
                found.len(),
                d.sensors().len()
            ));
        }
    }

    if fatal {
        util::Result::<()>::Err(util::Error(
            "refusing to start with an inert domain; BMC keeps control".into(),
        ))
    } else {
        Ok(())
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

    if args.iter().any(|a| a == "--calibrate") {
        if let Err(e) = calibrate::ensure_daemon_stopped() {
            err(&format!("{e}"));
            std::process::exit(2);
        }
        match calibrate::calibrate(&cfg) {
            Ok(_) => std::process::exit(0),
            Err(e) => {
                calibrate::report_error(&e);
                std::process::exit(1);
            }
        }
    }

    let authority = Authority::load(&cfg);

    if args.iter().any(|a| a == "--explain") {
        if let Err(e) = run_explain(&cfg, &authority) {
            err(&format!("{e}"));
            std::process::exit(1);
        }
        std::process::exit(0);
    }

    log(&format!(
        "smc-fand starting: tick {}s, duty {}-{}%, slew +{}/-{} per tick, authority {:?}",
        cfg.tick.as_secs(),
        cfg.min_duty,
        cfg.max_duty,
        cfg.slew_up,
        cfg.slew_down,
        cfg.authority_mode
    ));
    if cfg.authority_mode == AuthorityMode::Uniform {
        log("uniform authority: every zone tracks the worst domain. Safe, louder than \
             necessary. Run --calibrate to let zones decouple.");
    }
    for line in authority.render(&cfg).lines() {
        log(line);
    }

    let mut ctrl = Controller::new(&cfg);
    if let Err(e) = startup_checks(&cfg, &ctrl) {
        err(&format!("{e}"));
        std::process::exit(2);
    }

    assert_fan_thresholds(&cfg);

    if let Err(e) = set_mode(&cfg, MODE_FULL) {
        err(&format!("could not engage manual fan mode: {e}"));
        std::process::exit(1);
    }
    log("manual fan mode engaged; BMC Standard mode is the fallback");

    let code = control_loop(&cfg, &mut ctrl, &authority);
    clear_metrics(&cfg);
    restore_bmc(&cfg, "control loop exited");
    std::process::exit(code);
}
