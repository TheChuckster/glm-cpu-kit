# cooling — fan control for Supermicro CPU-inference hosts

`smc-fand` replaces the BMC's built-in fan curve with a PI controller driven by
IPMI temperature sensors, and keeps the BMC's own curve as a fallback.

Two problems on a large CPU-inference box that the stock BMC does not solve:

1. **Quiet fans look like failed fans.** Low-RPM fans (Noctuas) idle below the
   BMC's `LowerThresholdCritical`, so the BMC declares a fan failure and ramps
   the whole chassis to 100%. The fan then spins back above the threshold, the
   BMC returns to its curve, the fan slows below it again — a full-speed rev
   every ~20 seconds, indefinitely.
2. **The BMC cools the CPU, not the DIMMs.** Under sustained all-core inference,
   a machine with ~1 TB of DDR5 is DIMM-limited long before it is CPU-limited.
   The stock curve does not know that.

## Symptom 1: periodic full-speed ramping

Confirm from the event log — a healthy box has none of these:

```sh
ipmitool -I open sel list | grep -i 'Lower Critical'
```

Repeating assert/deassert pairs on a fan sensor mean the threshold is above the
fan's idle RPM. Check what the fan actually does versus where the limit sits:

```sh
ipmitool -I open sensor get FAN4     # 'Sensor Reading' vs 'Lower Critical'
```

Fix by lowering the threshold beneath the fan's real idle speed:

```sh
ipmitool -I open sensor thresh FAN4 lcr 140
```

Always read back afterwards — these BMCs quantise to fixed RPM steps and will
silently round or ignore a value they dislike. `smc-fand` re-asserts the
thresholds in `SMC_FAN_THRESHOLDS` at every startup, because a BMC firmware
update reverts them to factory and resurrects the bug silently.

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

The rear exhaust (zone 1) was pinned at 100% and losing ground; the mid-wall
fans (zone 0) were idling at 56% with all the actual authority over DIMM
temperature. DDR5 throttles around 85 °C, so this was 1 °C from the limit.

Hence two mechanisms in the controller:

- **Per-zone auxiliary sensor groups.** Each zone has a primary group and an
  optional aux group with its own setpoint; the zone's error is the worst demand
  across both. The DIMM sensors are wired into *both* zones so zone 0 responds
  to memory heat directly instead of waiting to be asked.
- **Cross-zone assist.** If any zone saturates while still above setpoint, the
  others are floored at `SMC_ASSIST_DUTY`, spending idle headroom on cooling.

## Control behaviour

Two PI loops, one per zone. No derivative term — BMC temperature readings are
quantised and noisy, and D amplifies exactly that on a plant this slow.

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
| Emergency | Any group above its limit forces 100%, bypassing PI and slew — evaluated across all groups, not just the driving one |
| IPMI failures | *Any* consecutive failure — read, write, readback, mode check — counts toward `SMC_MAX_FAILURES`, then hands back to the BMC |
| Readback | Every write verified; mismatch logged and exported as `smc_fand_control_lost` |
| Heartbeat | Written only when every zone was actually commanded, so it cannot tick while control is silently lost |
| Watchdog | Separate timer unit, fails closed on missing/unreadable/future timestamps — catches hangs systemd cannot see |
| Alerting | `smc-fand-alerts.yml`; saturation is the leading indicator, not emergency |

`smc_fand_up` is the liveness anchor: the daemon deletes its textfile on a clean
exit, so `absent(smc_fand_up)` means *stopped* while a stale heartbeat with
`smc_fand_up` present means *wedged*. Without that distinction the textfile
collector keeps serving the last sample forever and the two look identical.

The daemon installs **no signal handlers** on purpose. Letting SIGTERM take its
default action and relying on `ExecStopPost` covers strictly more cases than a
handler could — including SIGKILL — and keeps the binary free of `unsafe`.

## Install

Static musl binary, no runtime dependencies:

```sh
cargo build --release --target x86_64-unknown-linux-musl
```

```sh
sudo install -m 0755 target/x86_64-unknown-linux-musl/release/smc-fand /usr/local/sbin/
sudo install -m 0755 smc-fand-watchdog.sh /usr/local/sbin/
sudo install -m 0644 smc-fand.env /etc/default/smc-fand
sudo install -m 0644 smc-fand.service smc-fand-watchdog.service smc-fand-watchdog.timer \
    /etc/systemd/system/
sudo systemctl daemon-reload
```

Metrics are optional, but if you want them create the textfile-collector
directory first — `ProtectSystem=strict` makes the daemon unable to create it:

```sh
sudo mkdir -p /var/lib/prometheus/node-exporter
sudo mkdir -p /etc/prometheus/rules
sudo install -m 0644 smc-fand-alerts.yml /etc/prometheus/rules/
```

Add to `prometheus.yml` if not already present, then reload Prometheus:

```yaml
rule_files:
  - /etc/prometheus/rules/*.yml
```

Finally, **map the zones before enabling** (next section), then:

```sh
sudo systemctl enable --now smc-fand.service smc-fand-watchdog.timer
```

## Retune before deploying elsewhere

Zone-to-fan mapping, sensor names and setpoints are specific to the board. The
daemon resolves every configured sensor name at startup and refuses to run if a
group resolves zero sensors, so a typo fails loudly rather than leaving a group
silently inert — but the zone-to-fan mapping it cannot check for you.

Verify it by raising one zone at a time and watching which fans respond. **Stop
the daemon first**, or it will overwrite your duty within one tick and you will
be reading its numbers instead of yours:

```sh
sudo systemctl stop smc-fand                          # daemon must not be running
sudo ipmitool -I open raw 0x30 0x45 0x01 0x01         # manual mode
sudo ipmitool -I open raw 0x30 0x70 0x66 0x01 0x00 0x45   # zone 0 -> 69%
sudo ipmitool -I open raw 0x30 0x70 0x66 0x01 0x01 0x1f   # zone 1 -> 31%
sleep 25 && sudo ipmitool -I open sdr type Fan         # which fans went up?
```

Then swap the two duties and repeat to confirm. **Always restore afterwards** —
leaving the BMC in manual mode at a fixed duty means no thermal response at all:

```sh
sudo /usr/local/sbin/smc-fand --restore    # hands the fans back to the BMC
```

Put the result in `SMC_ZONE0_*` / `SMC_ZONE1_*`, then start the daemon.

## Verify

Fault injection matters more than the happy path. Each of these must end with
the BMC back in Standard mode (`raw 0x30 0x45 0x00` returning `00`):

```sh
sudo systemctl stop smc-fand                     # -> 00
sudo kill -9 $(systemctl show -p MainPID --value smc-fand)   # -> 00, then restarts
```

For the hang case, the watchdog fires once the heartbeat exceeds
`SMC_HEARTBEAT_MAX_AGE` (default 120s), so allow more than two minutes:

```sh
sudo kill -STOP $(systemctl show -p MainPID --value smc-fand)
sleep 130 && sudo /usr/local/sbin/smc-fand-watchdog.sh    # or wait for the timer
```

Configuration validation should also be exercised — each of these must exit
before touching the BMC, leaving mode at `00`:

```sh
sudo SMC_SLEW_UP=-5 /usr/local/sbin/smc-fand      # negative slew
sudo SMC_MAX_DUTY=20 /usr/local/sbin/smc-fand     # min > max
sudo SMC_ZONE0_KP=nan /usr/local/sbin/smc-fand    # NaN
sudo SMC_VERIFY_EVERY=0 /usr/local/sbin/smc-fand  # zero divisor
```

Confirm the metrics reach Prometheus:

```sh
curl -s localhost:9100/metrics | grep smc_fand
```

`smc_fand_driver` shows which sensor and group is currently driving each zone —
the quickest way to tell whether the CPU or the DIMMs are setting fan speed.
