# cooling — fan control for Supermicro CPU-inference hosts

`smc-fand` replaces the BMC's built-in fan curve with PI control driven by IPMI
temperature sensors, and keeps the BMC's own curve as the fallback.

Two problems on a large CPU-inference box that the stock BMC does not solve:

1. **Quiet fans look like failed fans.** Low-RPM fans (Noctuas) idle below the
   BMC's `LowerThresholdCritical`, so the BMC declares a fan failure and ramps
   the whole chassis to 100%. The fan then spins back above the threshold, the
   BMC returns to its curve, the fan slows below it again — a full-speed rev
   every ~20 seconds, indefinitely.
2. **The BMC cools the CPU, not the DIMMs.** Under sustained all-core inference,
   a machine with ~1 TB of DDR5 is DIMM-limited long before it is CPU-limited.
   The stock curve does not know that.

## The model: domains and authority

The design deliberately avoids asking the operator for things that are hard to
know and easy to get wrong — which fans are in which BMC zone, which sensors
each zone can influence, which fans are intake versus exhaust.

None of that is what a controller needs. It needs one measurable quantity: **how
much does duty on zone Z move sensor S**, in °C per % duty. That is the
*authority matrix*. Zone membership becomes a display detail, and intake versus
exhaust never enters the model, because a measured gain already encodes whatever
the airflow does.

So control is organised around **thermal domains** — sets of sensors sharing a
limit — not fan zones:

```
each domain runs a PI loop and asks for a duty
duty[zone] = max over domains of ( demand[domain] × authority[zone][domain] )
```

Configuration reduces to the irreducible part: you state how hot a DIMM may get.
You do not state which fans cool it.

### Why full coupling is the safe default

Authority is `0.0..1.0`, normalised per domain so the most effective zone is
1.0. **1.0 means fully coupled, and full coupling is always safe**: every zone
runs at the worst domain's demand, which is at least as much cooling as any
correct mapping would produce. Louder, never hotter.

The effective value blends toward that prior by confidence:

```rust
effective = 1.0 + confidence × (authority − 1.0)     // lerp(1.0, learned, confidence)
```

That single line is the safety mechanism. A missing file, a corrupt row, an
unknown domain, a sensor that appeared after calibration — everything degenerate
lands on 1.0. Learning never makes the box hotter than an uncalibrated one; it
only ever earns the right to decouple.

Two hard rules sit above the matrix. **Emergency ignores it entirely** — any
domain over its limit sends every zone to 100%, so a learned zero can never hold
a zone back. And **a saturated domain couples to all zones** regardless of
authority, because a domain out of headroom is not a place to be optimising.
That second rule is what used to be bolted on as "cross-zone assist"; it now
falls out of the model.

## Modes

Both modes share one abstraction: the controller only ever calls
`authority.effective(zone, domain)`. The mode decides how the matrix is
*populated*, never how it is *consumed*, so there is one control path to reason
about and test.

| `SMC_AUTHORITY_MODE` | Matrix from | Use when |
|---|---|---|
| `static` (default) | `SMC_ZONE<N>_DOMAINS` in config | You know the layout, or you want deterministic behaviour |
| `calibrated` | measured by `--calibrate` | You want the box to work it out |
| `uniform` | nothing; all 1.0 | Uncharacterised hardware. Safe, loudest |

Static is the default so an upgrade changes nothing. Calibrated is opt-in, after
you have run a sweep and looked at the numbers.

## Calibration

```sh
sudo systemctl stop smc-fand
sudo /usr/local/sbin/smc-fand --calibrate      # or: systemctl start smc-fand-calibrate
```

It does two things. First it **discovers zones** by probing zone ids and noting
which fan RPMs respond — that is the fan→zone map you would otherwise have to
work out by hand. Then it **measures gains**: each zone is swept across
25–100% while the others are held fixed, waiting ~120 s per step for thermal
settling, and every sensor's response is fit by least squares.

Takes 15–20 minutes and is loud. It aborts and restores normal control if any
sensor comes within `SMC_CALIBRATE_ABORT_MARGIN` of its emergency threshold — a
measurement is never worth cooking the box for. It refuses to run while the
daemon is active, since two writers would produce nonsense gains.

While running it writes `/run/smc-fand/calibrating`, and the watchdog stands
down for it — bounded by age, so a crashed sweep cannot disable the last safety
layer indefinitely.

Output is deliberately line-based and hand-editable, because **the file is the
explanation** of why the fans do what they do:

```
# zone  domain   authority  confidence  gain_C_per_pct  samples
0       cpu      1.00       0.95        -0.42           4
0       dimm     1.00       0.91        -0.31           4
1       cpu      0.18       0.88        -0.08           4
1       dimm     0.28       0.90        -0.09           4
```

Then set `SMC_AUTHORITY_MODE=calibrated` and restart.

### Why not learn passively and continuously?

Because it does not work. Duty correlates with load, so passively observed data
says "more fans ⇒ hotter" — the controller would be learning the confound, not
the causal gain. Calibration breaks it by *choosing* the duty itself. That is
the whole reason it is a deliberate, operator-initiated act rather than
something the daemon does quietly.

Passive observation still runs, but only as **drift detection**: it answers "has
authority *changed*" — a fan swapped, a shroud added, a filter clogged — and
raises `smc_fand_authority_drift`. It flags; it never retunes control.

## Explaining a decision

```sh
sudo /usr/local/sbin/smc-fand --explain </dev/null
```

Reads current sensors (or `ipmitool sdr type Temperature` output on stdin) and
prints the whole derivation:

```
zone             cpu          dimm        periph
0               1.00          1.00          0.00
1               0.00          1.00          1.00

domain demands
  cpu        25.0%   VDDIO_VRM Temp 45.0C vs setpoint 70.0C (error -25.0)
  dimm       70.0%   DIMMA~F Temp 78.0C vs setpoint 62.0C (error +16.0)
  periph     25.0%   NIC Temp 45.0C vs setpoint 60.0C (error -15.0)

zone duty
  zone 0 ->  70%   driven by dimm (authority 1.00)
  zone 1 ->  70%   driven by dimm (authority 1.00)
```

Because it takes sensor readings on stdin, the controller is testable without
hardware — which is how static mode was checked against the previous
implementation across a table of synthetic scenarios.

## Symptom 1: periodic full-speed ramping

Confirm from the event log — a healthy box has none of these:

```sh
ipmitool -I open sel list | grep -i 'Lower Critical'
```

Repeating assert/deassert pairs on a fan sensor mean the threshold is above the
fan's idle RPM. Fix by lowering it beneath the fan's real idle speed:

```sh
ipmitool -I open sensor thresh FAN4 lcr 140
```

Always read back — these BMCs quantise to fixed RPM steps and will silently
round or ignore a value. `smc-fand` re-asserts and verifies the thresholds in
`SMC_FAN_THRESHOLDS` at every startup, because a BMC firmware update reverts
them to factory and resurrects the bug silently.

If the fans are stuck at 100% and ignore all duty commands, the BMC has latched
a fan-failure state. Nothing short of restarting the controller clears it:

```sh
ipmitool -I open mc reset cold       # host OS unaffected; BMC gone ~2 min
```

## Symptom 2: DIMMs hot while the CPU is comfortable

Measured on an EPYC 9575F with 1.1 TB DDR5 under sustained llama.cpp load:

| | zone 0 duty | DIMMA~F | DIMMG~L |
|---|---|---|---|
| zone 1 alone, saturated at 100% | 56% | 84 °C | 77 °C |
| after raising zone 0 | 98% | 78 °C | 70 °C |

The rear exhaust was pinned at 100% and losing ground; the mid-wall fans were
idling at 56% holding all the actual authority over DIMM temperature. DDR5
throttles around 85 °C, so this was 1 °C from the limit. Exactly the kind of
fact `--calibrate` is meant to discover for you.

## Control behaviour

One PI loop per domain. No derivative term — BMC readings are quantised and
noisy, and D amplifies exactly that on a plant this slow.

Slew is deliberately asymmetric (**react fast, taper slowly**): heat arrives
faster than it leaves, and the error directions have different costs. Ramping up
late risks throttling; ramping down late costs a little noise. Defaults are
+30/−3 per tick, so idle→full takes ~15 s and full→idle ~2 min. Emergencies
bypass the limiter entirely.

## Safety model

The governing rule: **if the daemon is not running, the BMC controls the fans.**

| Layer | Mechanism |
|---|---|
| Silicon | CPU/DIMM thermal throttling, independent of all software |
| Config validation | Bad values abort *before* manual mode is engaged — a misconfigured daemon never takes the fans |
| BMC curve | `ExecStopPost` runs `smc-fand --restore` on *every* exit — clean, panic, `kill -9`, reboot |
| Thresholds | Re-asserted *and read back* at startup, so falling back to the BMC curve does not re-trigger problem 1 |
| Uniform prior | Unknown authority means fully coupled, so a bad or missing matrix cools more, never less |
| Emergency | Any domain over its limit sends *every* zone to 100%, bypassing PI, slew and the matrix |
| IPMI failures | *Any* consecutive failure — read, write, readback, mode check — counts toward `SMC_MAX_FAILURES`, then hands back to the BMC |
| Readback | Every write verified; mismatch logged and exported as `smc_fand_control_lost` |
| Heartbeat | Written only when every zone was actually commanded, so it cannot tick while control is silently lost |
| Watchdog | Separate timer unit, fails closed on missing/unreadable/future timestamps — catches hangs systemd cannot see |
| Alerting | `smc-fand-alerts.yml`; saturation is the leading indicator, not emergency |

The daemon installs **no signal handlers** on purpose. Letting SIGTERM take its
default action and relying on `ExecStopPost` covers strictly more cases than a
handler could — including SIGKILL — and keeps the binary free of `unsafe`.

`smc_fand_up` is the liveness anchor: the daemon deletes its textfile on a clean
exit, so `absent(smc_fand_up)` means *stopped* while a stale heartbeat with
`smc_fand_up` present means *wedged*.

## Install

Static musl binary, no runtime dependencies:

```sh
cargo build --release --target x86_64-unknown-linux-musl
```

```sh
sudo install -m 0755 target/x86_64-unknown-linux-musl/release/smc-fand /usr/local/sbin/
sudo install -m 0755 smc-fand-watchdog.sh /usr/local/sbin/
sudo install -m 0644 smc-fand.env /etc/default/smc-fand
sudo install -m 0644 smc-fand.service smc-fand-calibrate.service \
    smc-fand-watchdog.service smc-fand-watchdog.timer /etc/systemd/system/
sudo mkdir -p /var/lib/smc-fand
sudo systemctl daemon-reload
```

Metrics are optional, but if you want them create the textfile-collector
directory first — `ProtectSystem=strict` stops the daemon creating it:

```sh
sudo mkdir -p /var/lib/prometheus/node-exporter /etc/prometheus/rules
sudo install -m 0644 smc-fand-alerts.yml /etc/prometheus/rules/
```

Add to `prometheus.yml` if not already present, then reload Prometheus:

```yaml
rule_files:
  - /etc/prometheus/rules/*.yml
```

Then set the domains for your hardware in `/etc/default/smc-fand` and start:

```sh
sudo systemctl enable --now smc-fand.service smc-fand-watchdog.timer
```

**Sensor names are board-specific.** The daemon resolves every configured name
at startup and refuses to run if a domain resolves zero sensors, so a typo fails
loudly rather than leaving a domain silently inert. Check what your BMC offers
with `ipmitool -I open sdr type Temperature`.

## Verify

Fault injection matters more than the happy path. Each of these must end with
the BMC back in Standard mode (`raw 0x30 0x45 0x00` returning `00`):

```sh
sudo systemctl stop smc-fand                     # -> 00
sudo kill -9 $(systemctl show -p MainPID --value smc-fand)   # -> 00, then restarts
```

For the hang case, the watchdog fires once the heartbeat exceeds
`SMC_HEARTBEAT_MAX_AGE` (default 120s):

```sh
sudo kill -STOP $(systemctl show -p MainPID --value smc-fand)
sleep 130 && sudo /usr/local/sbin/smc-fand-watchdog.sh    # or wait for the timer
```

Configuration validation — each must exit 2 before touching the BMC:

```sh
sudo env SMC_SLEW_UP=-5 /usr/local/sbin/smc-fand         # negative slew
sudo env SMC_MAX_DUTY=20 /usr/local/sbin/smc-fand        # min > max
sudo env SMC_DOMAIN_CPU_KI=nan /usr/local/sbin/smc-fand  # NaN
sudo env SMC_VERIFY_EVERY=0 /usr/local/sbin/smc-fand     # zero divisor
sudo env SMC_ZONE0_SENSORS=foo /usr/local/sbin/smc-fand  # retired variable
```

Metrics reaching Prometheus:

```sh
curl -s localhost:9100/metrics | grep smc_fand
```

`smc_fand_driver` shows which domain is setting each zone's duty — the quickest
way to tell whether the CPU or the DIMMs are in charge.

## Rollback

`SMC_AUTHORITY_MODE=static` and restart puts you back on the declared mapping;
no recalibration or file surgery needed. Deleting the authority file falls back
to `uniform`, which is coupled and safe.

To remove entirely:

```sh
sudo systemctl disable --now smc-fand.service smc-fand-watchdog.timer
```

`ExecStopPost` restores BMC Standard mode automatically, and the 140 RPM
thresholds stay in place — so the box returns to a *working* Standard-mode
configuration, not the broken one it started from.
