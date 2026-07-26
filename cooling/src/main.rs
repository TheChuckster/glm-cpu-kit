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
//! `--restore` is dispatched before any configuration is parsed. It is the
//! thing that recovers from a broken configuration, so it must not depend on
//! the configuration parsing cleanly.
//!
//! Further layers:
//!
//!   * Configuration is validated *before* manual mode is engaged. A bad value
//!     means we exit having never taken the fans away from the BMC.
//!   * Fan-failure thresholds are re-asserted and verified at startup. If they
//!     cannot be confirmed, the duty floor is raised rather than idling the
//!     fans under a threshold that would trip the BMC's ramp cycle.
//!   * Any domain at or above its emergency limit forces every zone to 100%,
//!     bypassing the PI output, the slew limiter and the authority matrix.
//!   * A saturated domain couples to every zone regardless of learned
//!     authority - being out of headroom is not the time to optimise.
//!   * IPMI failures are counted *per operation*, so one permanently failing
//!     zone escalates to `MAX_FAILURES` even while its neighbours succeed.
//!   * Duty writes are read back. A mismatch means something overrode us
//!     (chassis intrusion ramps do this), logged and exported as
//!     `smc_fand_control_lost` rather than passing silently.
//!   * The heartbeat is written only when every zone was actually commanded,
//!     and the exported heartbeat *metric* carries that same timestamp so
//!     alerting sees what the watchdog sees.

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
use util::{err, log, now_secs, out, warn, write_atomic, Failures, Result};

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
    let mut failures = Failures::new();
    let mut tick: u32 = 0;
    let mut written: HashMap<u8, u8> = HashMap::new();
    let mut drift = DriftMonitor::new();
    let mut last_instant = Instant::now();
    // The timestamp of the last tick in which every zone was actually
    // commanded. Exported as the heartbeat metric so alerting and the external
    // watchdog agree on what "alive" means.
    let mut last_good_tick = now_secs();

    macro_rules! bail_if_exhausted {
        () => {
            if let Some((what, n)) = failures.exhausted(cfg.max_failures) {
                err(&format!(
                    "{n} consecutive failures of {what}; handing control back to the BMC"
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
            Ok(t) => {
                failures.ok("sensor read");
                t
            }
            Err(e) => {
                let n = failures.fail("sensor read");
                err(&format!(
                    "sensor read failed ({n}/{}): {e}",
                    cfg.max_failures
                ));
                bail_if_exhausted!();
                sleep(cfg.tick);
                continue;
            }
        };

        // The BMC can drop out of manual mode on its own (a cold reset does
        // this). Checked periodically rather than every tick - it costs a fork
        // and the failure mode is slow-moving.
        if tick.is_multiple_of(cfg.mode_check_every) {
            match get_mode(cfg) {
                Ok(0x01) => failures.ok("mode check"),
                Ok(m) => {
                    failures.ok("mode check");
                    warn(&format!("BMC left manual mode (now 0x{m:02x}); re-engaging"));
                    re_engage(cfg, &mut failures, &mut written);
                }
                Err(e) => {
                    let n = failures.fail("mode check");
                    err(&format!(
                        "mode check failed ({n}/{}): {e}",
                        cfg.max_failures
                    ));
                    // A BMC whose mode-get is failing may also have silently
                    // dropped to Standard. Re-assert AND forget what we think
                    // the zones hold, or a BMC that reset keeps its own
                    // power-on duty while we skip writes as "unchanged".
                    re_engage(cfg, &mut failures, &mut written);
                }
            }
            bail_if_exhausted!();
        }

        let outcome = ctrl.step(&temps, cfg, authority, dt, true);

        for name in &outcome.unreadable {
            let n = failures.fail(&format!("domain {name} readable"));
            err(&format!(
                "domain {name:?} has no readable sensors ({n}/{}) - check that the \
                 configured names still match the BMC",
                cfg.max_failures
            ));
        }
        for d in &outcome.demands {
            failures.ok(&format!("domain {} readable", d.domain));
        }
        bail_if_exhausted!();

        let verify = tick.is_multiple_of(cfg.verify_every);
        let mut readback: HashMap<u8, Option<u8>> = HashMap::new();
        let mut control_lost: HashMap<u8, bool> = HashMap::new();
        let mut write_failed: HashMap<u8, bool> = HashMap::new();
        let mut all_commanded = outcome.unreadable.is_empty();

        for z in &outcome.zones {
            let changed = written.get(&z.zone) != Some(&z.duty);
            let mut failed = false;
            let write_key = format!("duty write zone {}", z.zone);

            if changed {
                match set_duty(cfg, z.zone, z.duty) {
                    Ok(()) => {
                        written.insert(z.zone, z.duty);
                        failures.ok(&write_key);
                        // Only now may the slew limiter anchor here.
                        ctrl.applied.insert(z.zone, z.duty);
                    }
                    Err(e) => {
                        failed = true;
                        all_commanded = false;
                        let n = failures.fail(&write_key);
                        err(&format!(
                            "duty write failed for zone {} ({n}/{}): {e}",
                            z.zone, cfg.max_failures
                        ));
                    }
                }
            }
            write_failed.insert(z.zone, failed);

            // Never read back after a failed write. The BMC still holds the old
            // duty, so the mismatch would be reported as an external override,
            // masking the write failure and anchoring the slew limiter to a
            // value we never set.
            let mut lost = false;
            if (changed || verify) && !failed {
                let read_key = format!("duty readback zone {}", z.zone);
                match get_duty(cfg, z.zone) {
                    Ok(rb) => {
                        failures.ok(&read_key);
                        lost = (rb as i32 - z.duty as i32).abs() > READBACK_TOLERANCE;
                        readback.insert(z.zone, Some(rb));
                        if lost {
                            all_commanded = false;
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
                        let n = failures.fail(&read_key);
                        warn(&format!(
                            "duty readback failed for zone {} ({n}/{}): {e}",
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
        bail_if_exhausted!();

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

        // Written only when every zone was actually commanded. A heartbeat that
        // ticks while control is silently lost would defeat the whole point of
        // the independent watchdog - and the exported metric must carry the
        // same timestamp, or the staleness alert can never fire.
        if all_commanded {
            last_good_tick = now_secs();
            if let Err(e) = write_atomic(&cfg.heartbeat_path, &format!("{last_good_tick}\n")) {
                warn(&format!("heartbeat write failed: {e}"));
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
                heartbeat: last_good_tick,
                thresholds_ok: ctrl.thresholds_ok,
            },
            cfg,
        );
        if let Err(e) = write_atomic(&cfg.metrics_path, &metrics) {
            // Losing metrics is not a reason to stop cooling the box.
            warn(&format!("metrics write failed: {e}"));
        }

        sleep(cfg.tick);
    }
}

/// Re-assert manual mode and forget the believed duty state.
///
/// `written` must be cleared: if the BMC reset, it holds its own power-on duty
/// while our map still says otherwise, so every zone would be skipped as
/// "unchanged" and the fans would run the BMC's numbers while we report ours.
fn re_engage(cfg: &Config, failures: &mut Failures, written: &mut HashMap<u8, u8>) {
    written.clear();
    match set_mode(cfg, MODE_FULL) {
        Ok(()) => failures.ok("mode set"),
        Err(e) => {
            let n = failures.fail("mode set");
            err(&format!(
                "could not re-engage manual mode ({n}/{}): {e}",
                cfg.max_failures
            ));
        }
    }
}

/// Print the matrix, each domain's demand, and the resulting duty.
///
/// Reads live sensors by default. `--explain -` reads `ipmitool sdr type
/// Temperature` output from stdin instead, which is how the controller is
/// tested without hardware. (Reading stdin unconditionally would hang on a
/// terminal, making the live path unreachable without Ctrl-D.)
fn run_explain(cfg: &Config, authority: &Authority, from_stdin: bool) -> Result<()> {
    let temps = if from_stdin {
        let mut input = String::new();
        std::io::stdin().read_to_string(&mut input)?;
        parse_temperatures(&input)?
    } else {
        read_temperatures(cfg)?
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
        Err(util::Error(
            "refusing to start with an inert domain; BMC keeps control".into(),
        ))
    } else {
        Ok(())
    }
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();

    // FIRST, before any configuration is parsed or validated. This is the
    // safety net that ExecStopPost invokes, and it has to work when the
    // configuration is exactly what is broken.
    if args.iter().any(|a| a == "--restore") {
        let cfg = Config::minimal();
        clear_metrics(&cfg);
        std::process::exit(if restore_bmc(&cfg, "--restore requested") {
            0
        } else {
            1
        });
    }

    let mut cfg = match Config::from_env() {
        Ok(c) => c,
        Err(e) => {
            // Nothing has been touched: the BMC still owns the fans.
            err(&format!("configuration error: {e}"));
            std::process::exit(2);
        }
    };

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
        let from_stdin = args.iter().any(|a| a == "-");
        if let Err(e) = run_explain(&cfg, &authority, from_stdin) {
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

    if let Err(e) = startup_checks(&cfg, &Controller::new(&cfg)) {
        err(&format!("{e}"));
        std::process::exit(2);
    }

    // If the fan-fail thresholds could not be confirmed, running at min_duty
    // would idle the fans under a threshold the BMC still enforces, which
    // restarts the exact ramp cycle this daemon exists to prevent. Raise the
    // floor instead of exiting: handing back to the BMC would idle them lower.
    let thresholds_ok = assert_fan_thresholds(&cfg);
    if !thresholds_ok {
        let raised = cfg.unverified_min_duty.max(cfg.min_duty).min(cfg.max_duty);
        err(&format!(
            "fan-fail thresholds could NOT be confirmed - raising the duty floor from {}% to {}% \
             so the fans stay clear of whatever threshold the BMC is enforcing. \
             Fix the thresholds and restart to get the quiet floor back.",
            cfg.min_duty, raised
        ));
        cfg.min_duty = raised;
        if cfg.idle_duty < cfg.min_duty {
            cfg.idle_duty = cfg.min_duty;
        }
    }

    let mut ctrl = Controller::new(&cfg);
    ctrl.thresholds_ok = thresholds_ok;

    if let Err(e) = set_mode(&cfg, MODE_FULL) {
        err(&format!("could not engage manual fan mode: {e}"));
        std::process::exit(1);
    }
    log("manual fan mode engaged; BMC Standard mode is the fallback");

    let code = control_loop(&cfg, &mut ctrl, &authority);
    clear_metrics(&cfg);
    // control_loop already restored on every path it returns from; this is the
    // belt to its braces, and restoring twice is harmless.
    restore_bmc(&cfg, "control loop exited");
    std::process::exit(code);
}
