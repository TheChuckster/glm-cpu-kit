//! The authority matrix: how much duty on a zone moves a thermal domain.
//!
//! This replaces declared fan-to-zone-to-sensor mappings with one measurable
//! quantity. Zone membership becomes a display detail, and intake versus
//! exhaust never enters the model - a measured gain already encodes whatever
//! the airflow topology does.
//!
//! # The safety property
//!
//! Authority is `0.0..=1.0`, normalised per domain so the most authoritative
//! zone is 1.0. **1.0 means fully coupled, which is always safe**: if every
//! zone runs at the worst domain's demand, the box gets at least as much
//! cooling as any correct mapping would give it. Louder, never hotter.
//!
//! So the effective value blends toward that prior by confidence, and every
//! degenerate case - missing file, corrupt row, unknown domain, a sensor that
//! appeared after calibration - lands on 1.0. Learning never makes the box
//! hotter than an uncalibrated one; it only earns the right to decouple.

use std::collections::HashMap;
use std::fs;
use std::path::Path;

use crate::config::{AuthorityMode, Config};
use crate::util::{linear_fit, log, warn, write_atomic, Error, Result};

#[derive(Clone, Copy, Debug)]
pub struct Cell {
    /// Normalised 0..=1. 1.0 = this zone is the most effective at cooling the
    /// domain; 0.0 = it does nothing for it.
    pub authority: f64,
    /// 0..=1. How much we trust `authority`. Drives the blend toward the safe
    /// uniform prior.
    pub confidence: f64,
    /// Raw measured slope, degrees C per percent duty. Negative means cooling.
    /// Kept for explainability and drift comparison, not used for control.
    pub gain: f64,
    pub samples: u32,
}

pub struct Authority {
    pub mode: AuthorityMode,
    /// Unix seconds when the matrix was measured; 0 for static/uniform.
    pub calibrated_at: u64,
    cells: HashMap<(u8, String), Cell>,
    zones: Vec<u8>,
}

impl Authority {
    /// The only method the control loop uses.
    ///
    /// `lerp(1.0, learned, confidence)` - this single line is the safety
    /// mechanism. Anything unknown or untrusted returns 1.0, which couples the
    /// zone to the domain and cools more than necessary rather than less.
    pub fn effective(&self, zone: u8, domain: &str) -> f64 {
        match self.cells.get(&(zone, domain.to_string())) {
            None => 1.0,
            Some(c) => {
                let a = 1.0 + c.confidence.clamp(0.0, 1.0) * (c.authority.clamp(0.0, 1.0) - 1.0);
                a.clamp(0.0, 1.0)
            }
        }
    }

    pub fn cell(&self, zone: u8, domain: &str) -> Option<&Cell> {
        self.cells.get(&(zone, domain.to_string()))
    }

    pub fn zones(&self) -> &[u8] {
        &self.zones
    }

    /// Everything couples. The safe baseline, and what an uncharacterised box
    /// gets.
    pub fn uniform(zones: Vec<u8>) -> Self {
        Authority {
            mode: AuthorityMode::Uniform,
            calibrated_at: 0,
            cells: HashMap::new(),
            zones,
        }
    }

    /// Operator-declared mapping: 1.0 for listed domains, 0.0 for the rest,
    /// full confidence. Reproduces the pre-matrix behaviour exactly.
    pub fn from_static(cfg: &Config) -> Self {
        let mut cells = HashMap::new();
        let mut zones = Vec::new();
        for (zone, served) in &cfg.static_zone_domains {
            zones.push(*zone);
            for d in &cfg.domains {
                let serves = served.iter().any(|s| s == &d.name);
                cells.insert(
                    (*zone, d.name.clone()),
                    Cell {
                        authority: if serves { 1.0 } else { 0.0 },
                        confidence: 1.0,
                        gain: 0.0,
                        samples: 0,
                    },
                );
            }
        }
        Authority {
            mode: AuthorityMode::Static,
            calibrated_at: 0,
            cells,
            zones,
        }
    }

    pub fn from_cells(cells: HashMap<(u8, String), Cell>, at: u64) -> Self {
        let mut zones: Vec<u8> = cells.keys().map(|(z, _)| *z).collect();
        zones.sort_unstable();
        zones.dedup();
        Authority {
            mode: AuthorityMode::Calibrated,
            calibrated_at: at,
            cells,
            zones,
        }
    }

    /// Build whatever the configured mode asks for. A calibrated mode with an
    /// unreadable file degrades to uniform with a warning rather than failing:
    /// coupled-and-running beats not-running.
    pub fn load(cfg: &Config) -> Self {
        match cfg.authority_mode {
            AuthorityMode::Static => Self::from_static(cfg),
            AuthorityMode::Uniform => Self::uniform(cfg.declared_zones()),
            AuthorityMode::Calibrated => match Self::read_file(&cfg.authority_path) {
                Ok(mut a) => {
                    // A zone present on the box but absent from the matrix
                    // would otherwise never be commanded at all, while manual
                    // mode is engaged - fans latched with nobody driving them.
                    // Merging the declared zones in means any such zone gets
                    // the uniform prior: coupled, and therefore safe.
                    for z in cfg.declared_zones() {
                        if !a.zones.contains(&z) {
                            warn(&format!(
                                "zone {z} is configured but missing from the calibrated matrix;                                  treating it as fully coupled until the next --calibrate"
                            ));
                            a.zones.push(z);
                        }
                    }
                    a.zones.sort_unstable();
                    log(&format!(
                        "loaded calibrated authority matrix from {} ({} cells, {} zones)",
                        cfg.authority_path.display(),
                        a.cells.len(),
                        a.zones.len()
                    ));
                    a
                }
                Err(e) => {
                    warn(&format!(
                        "could not load {}: {e} - falling back to uniform authority \
                         (all zones coupled: safe, louder). Run --calibrate.",
                        cfg.authority_path.display()
                    ));
                    Self::uniform(cfg.declared_zones())
                }
            },
        }
    }

    /// Line-based and hand-editable on purpose: this file *is* the explanation
    /// of why the fans do what they do, so it has to be readable with `cat`.
    pub fn read_file(path: &Path) -> Result<Self> {
        let text = fs::read_to_string(path)?;
        let mut cells = HashMap::new();
        let mut at = 0u64;

        for (n, line) in text.lines().enumerate() {
            let line = line.trim();
            if let Some(rest) = line.strip_prefix("# calibrated ") {
                at = rest.split_whitespace().next().and_then(|s| s.parse().ok()).unwrap_or(0);
                continue;
            }
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let f: Vec<&str> = line.split_whitespace().collect();
            if f.len() < 6 {
                warn(&format!("{}:{}: expected 6 fields, ignoring", path.display(), n + 1));
                continue;
            }
            let (Ok(zone), Ok(authority), Ok(confidence), Ok(gain), Ok(samples)) = (
                f[0].parse::<u8>(),
                f[2].parse::<f64>(),
                f[3].parse::<f64>(),
                f[4].parse::<f64>(),
                f[5].parse::<u32>(),
            ) else {
                warn(&format!("{}:{}: unparseable row, ignoring", path.display(), n + 1));
                continue;
            };
            // A row we cannot trust must not decouple anything. Dropping it
            // means effective() returns 1.0 for that cell - coupled and safe.
            if !authority.is_finite() || !confidence.is_finite() || !gain.is_finite() {
                warn(&format!("{}:{}: non-finite value, ignoring", path.display(), n + 1));
                continue;
            }
            cells.insert(
                (zone, f[1].to_string()),
                Cell {
                    authority: authority.clamp(0.0, 1.0),
                    confidence: confidence.clamp(0.0, 1.0),
                    gain,
                    samples,
                },
            );
        }

        if cells.is_empty() {
            return Err(Error("no usable rows".into()));
        }
        Ok(Self::from_cells(cells, at))
    }

    pub fn write_file(&self, path: &Path) -> Result<()> {
        let mut s = String::new();
        s.push_str("# smc-fand authority matrix\n");
        s.push_str("# How much duty on each zone moves each thermal domain.\n");
        s.push_str("# authority is normalised per domain: 1.0 = most effective zone.\n");
        s.push_str("# Unknown or low-confidence cells fall back to 1.0 (fully coupled, safe).\n");
        s.push_str(&format!("# calibrated {} (unix seconds)\n", self.calibrated_at));
        s.push_str("#\n# zone\tdomain\tauthority\tconfidence\tgain_C_per_pct\tsamples\n");

        let mut keys: Vec<_> = self.cells.keys().cloned().collect();
        keys.sort();
        for k in keys {
            let c = &self.cells[&k];
            s.push_str(&format!(
                "{}\t{}\t{:.2}\t{:.2}\t{:.4}\t{}\n",
                k.0, k.1, c.authority, c.confidence, c.gain, c.samples
            ));
        }
        write_atomic(path, &s)
    }

    /// Human-readable rendering for --explain and startup logs.
    pub fn render(&self, cfg: &Config) -> String {
        let mut s = String::new();
        s.push_str(&format!("authority mode: {:?}\n", self.mode));
        if self.calibrated_at > 0 {
            s.push_str(&format!("calibrated at:  {} (unix)\n", self.calibrated_at));
        }
        s.push_str(&format!("{:<6}", "zone"));
        for d in &cfg.domains {
            s.push_str(&format!("{:>14}", d.name));
        }
        s.push('\n');
        for z in &self.zones {
            s.push_str(&format!("{z:<6}"));
            for d in &cfg.domains {
                let eff = self.effective(*z, &d.name);
                match self.cell(*z, &d.name) {
                    Some(c) if c.confidence < 1.0 => {
                        s.push_str(&format!("{:>14}", format!("{eff:.2}(c{:.2})", c.confidence)))
                    }
                    _ => s.push_str(&format!("{eff:>14.2}")),
                }
            }
            s.push('\n');
        }
        s
    }
}

/// Watches whether measured authority still matches the calibrated matrix.
///
/// Deliberately advisory. Passive learning is a trap here: duty correlates with
/// load, so the naive observed relationship is "more fans => hotter", and a
/// controller acting on that would fight itself. Calibration breaks the
/// confound by choosing the duty itself; passive observation cannot. So this
/// answers only "has something *changed*" - a fan swapped, a shroud added, a
/// filter clogged - and its output is a flag for a human, never a control input.
pub struct DriftMonitor {
    /// (zone, domain) -> recent (duty, temp) observations.
    samples: HashMap<(u8, String), Vec<(f64, f64)>>,
    /// (zone, domain) -> consecutive evaluations that disagreed.
    strikes: HashMap<(u8, String), u32>,
    pub flagged: HashMap<(u8, String), bool>,
    window: usize,
    min_spread: f64,
    strikes_needed: u32,
}

impl DriftMonitor {
    pub fn new() -> Self {
        DriftMonitor {
            samples: HashMap::new(),
            strikes: HashMap::new(),
            flagged: HashMap::new(),
            // ~30 min of 5s ticks; thermal responses here are minutes long.
            window: 360,
            // Without this much duty spread the slope is noise, not signal.
            min_spread: 15.0,
            strikes_needed: 3,
        }
    }

    /// Record one tick. `usable` gates on everything that would confound the
    /// estimate: emergencies, saturation, lost control, failed writes.
    pub fn observe(&mut self, zone: u8, domain: &str, duty: f64, temp: f64, usable: bool) {
        if !usable {
            return;
        }
        let v = self.samples.entry((zone, domain.to_string())).or_default();
        v.push((duty, temp));
        if v.len() > self.window {
            let excess = v.len() - self.window;
            v.drain(0..excess);
        }
    }

    /// Compare observed slope against the calibrated gain. Returns newly
    /// flagged cells so the caller can log them once rather than every tick.
    pub fn evaluate(&mut self, authority: &Authority, tolerance: f64) -> Vec<(u8, String)> {
        let mut newly = Vec::new();
        let keys: Vec<_> = self.samples.keys().cloned().collect();

        for key in keys {
            let Some(points) = self.samples.get(&key) else {
                continue;
            };
            if points.len() < 30 {
                continue;
            }
            let min = points.iter().map(|p| p.0).fold(f64::INFINITY, f64::min);
            let max = points.iter().map(|p| p.0).fold(f64::NEG_INFINITY, f64::max);
            if max - min < self.min_spread {
                continue;
            }
            let Some((slope, r2)) = linear_fit(points) else {
                continue;
            };
            // A weak fit tells us nothing; that is the expected state most of
            // the time, since load dominates.
            if r2 < 0.3 {
                self.strikes.insert(key.clone(), 0);
                continue;
            }
            let Some(cell) = authority.cell(key.0, &key.1) else {
                continue;
            };
            if cell.samples == 0 {
                continue; // static or uniform: nothing to compare against
            }

            // Compare magnitudes relatively, and treat a sign flip as maximal
            // disagreement - a zone that used to cool and now appears to heat
            // is precisely the hardware change we want to notice.
            let disagreement = if slope.signum() != cell.gain.signum() {
                1.0
            } else {
                let denom = cell.gain.abs().max(1e-6);
                ((slope.abs() - cell.gain.abs()).abs() / denom).clamp(0.0, 1.0)
            };

            let entry = self.strikes.entry(key.clone()).or_insert(0);
            if disagreement > tolerance {
                *entry += 1;
                if *entry >= self.strikes_needed && !self.flagged.get(&key).copied().unwrap_or(false)
                {
                    self.flagged.insert(key.clone(), true);
                    newly.push(key.clone());
                }
            } else {
                *entry = 0;
                self.flagged.insert(key.clone(), false);
            }
        }
        newly
    }
}
