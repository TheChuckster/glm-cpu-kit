#!/bin/sh
# Independent safety net for smc-fand (the outermost software layer).
#
# This exists because systemd can only see failures it is told about. If the
# daemon deadlocks, blocks forever on a wedged /dev/ipmi0, or gets SIGSTOPped,
# the process is still "running" as far as systemd is concerned while the fans
# sit frozen at whatever duty was last commanded.
#
# So: a separate process, on a separate schedule, checks that the daemon is
# actually completing control ticks. If it is not, we hand the fans back to the
# BMC's own curve, which is always a safe place to land.
#
# The guiding rule is FAIL CLOSED. Every uncertain state - missing heartbeat,
# unreadable timestamp, clock skew, an IPMI read that returns nothing, an
# uptime we cannot determine - must end with the BMC in control. A branch that
# reaches `exit 0` because it could not tell what was going on is a bug.
#
# Run from smc-fand-watchdog.timer every 60s.

set -u

HEARTBEAT="${SMC_HEARTBEAT_PATH:-/run/smc-fand/heartbeat}"
IPMITOOL="${SMC_IPMITOOL:-/usr/bin/ipmitool}"
TIMEOUT_BIN="${SMC_TIMEOUT_BIN:-/usr/bin/timeout}"
CALL_TIMEOUT="${SMC_CALL_TIMEOUT:-10}"

# Must exceed the daemon's worst-case time to first heartbeat, not just its
# steady-state tick. Startup does sensor resolution plus threshold verification
# plus tick one - roughly 13 serialised ipmitool calls, so ~130s at a 10s call
# timeout. Below that we would restart a healthy daemon during every start.
MAX_AGE="${SMC_HEARTBEAT_MAX_AGE:-180}"

# Our own record of when we first saw the service active without a heartbeat.
# systemctl's ActiveEnterTimestamp is not always usable, and "I cannot tell how
# long this has been broken" must not mean "assume it is fine".
FIRST_SEEN="${SMC_WATCHDOG_FIRST_SEEN:-/run/smc-fand/watchdog-first-seen}"

log() { echo "smc-fand-watchdog: $*"; }

is_number() {
    case "${1:-}" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

if ! is_number "$MAX_AGE"; then
    log "ERROR: SMC_HEARTBEAT_MAX_AGE='$MAX_AGE' is not a plain integer number of seconds"
    log "refusing to run with an uninterpretable limit; falling back to 180"
    MAX_AGE=180
fi

force_bmc_control() {
    if "$TIMEOUT_BIN" "$CALL_TIMEOUT" "$IPMITOOL" -I open raw 0x30 0x45 0x01 0x00 >/dev/null 2>&1; then
        log "handed fan control back to the BMC (Standard mode)"
    else
        log "ERROR: could not restore BMC fan mode"
    fi
}

recover() {
    # Order matters: make the box safe first, then try to recover the daemon.
    force_bmc_control
    rm -f "$FIRST_SEEN"
    log "restarting smc-fand.service"
    # --no-block is essential. The headline case this watchdog exists for is a
    # daemon wedged on /dev/ipmi0; a blocking restart would then wait on a stop
    # job that cannot complete, and since this unit is Type=oneshot it would
    # never exit - so OnUnitActiveSec could not fire again and the last safety
    # layer would be silently gone.
    systemctl --no-block restart smc-fand.service || log "ERROR: restart request failed"
}

now=$(date +%s)

# A calibration sweep deliberately drives the fans with the service stopped.
# Without this we would see "daemon inactive, BMC not in Standard" and fight it,
# corrupting the very measurement it is taking.
#
# The lock holds an EXPIRY computed from the sweep's own length, not its start
# time. A manual --calibrate has no ExecStopPost, so Ctrl-C leaves the fans
# latched with no controller; we must take over shortly after the sweep should
# have finished rather than after some flat, generous interval.
CALIBRATE_LOCK="${SMC_CALIBRATE_LOCK:-/run/smc-fand/calibrating}"

if [ -f "$CALIBRATE_LOCK" ]; then
    expiry=$(tr -d ' \n' < "$CALIBRATE_LOCK" 2>/dev/null)
    if is_number "${expiry:-}" && [ "$now" -lt "$expiry" ]; then
        log "calibration in progress (expires in $(( expiry - now ))s); standing down"
        exit 0
    fi
    log "ERROR: calibration lock is expired or unreadable (expiry='${expiry:-}', now=$now)"
    log "assuming the calibration died; removing the lock and taking the fans back"
    rm -f "$CALIBRATE_LOCK"
    force_bmc_control
    exit 1
fi

if ! systemctl is-active --quiet smc-fand.service; then
    rm -f "$FIRST_SEEN"
    # Not supposed to be running. ExecStopPost should already have restored the
    # BMC, but assert it anyway - this is the layer that assumes nothing.
    #
    # Note the comparison: an EMPTY reading (ipmitool timed out, /dev/ipmi0
    # contended) is NOT "00", so it forces the correction. This is the branch
    # that rescues a daemon left in a failed state with manual mode engaged,
    # and it must not skip that job merely because it could not read the mode.
    mode=$("$TIMEOUT_BIN" "$CALL_TIMEOUT" "$IPMITOOL" -I open raw 0x30 0x45 0x00 2>/dev/null | tr -d ' \n')
    if [ "$mode" != "00" ]; then
        log "daemon inactive and BMC mode is '${mode:-unreadable}', not 00; correcting"
        force_bmc_control
    fi
    exit 0
fi

# Daemon claims to be active. Track how long we have been watching it, so a
# missing heartbeat is bounded by something we control rather than by an
# uptime query that may return nothing useful.
if [ ! -f "$FIRST_SEEN" ]; then
    echo "$now" > "$FIRST_SEEN" 2>/dev/null || log "WARNING: cannot write $FIRST_SEEN"
fi
first_seen=$(tr -d ' \n' < "$FIRST_SEEN" 2>/dev/null)
if is_number "${first_seen:-}" && [ "$first_seen" -le "$now" ]; then
    watched=$(( now - first_seen ))
else
    # Unreadable or in the future. Fail closed: assume we have been watching
    # long enough rather than granting an unbounded grace period.
    log "WARNING: $FIRST_SEEN unusable ('${first_seen:-}'); assuming grace expired"
    watched=$(( MAX_AGE + 1 ))
fi

if [ ! -f "$HEARTBEAT" ]; then
    # Manual mode is engaged before the control loop starts, so a daemon that
    # wedges before its first tick has taken the fans and left nobody driving
    # them. This must not be tolerated indefinitely.
    if [ "$watched" -gt "$MAX_AGE" ]; then
        log "ERROR: active for ${watched}s with no heartbeat at $HEARTBEAT"
        log "daemon wedged before its first successful tick; manual mode engaged with nobody driving"
        recover
        exit 1
    fi
    log "no heartbeat file yet at $HEARTBEAT (watched ${watched}s, grace ${MAX_AGE}s)"
    exit 0
fi

stamp=$(tr -d ' \n' < "$HEARTBEAT" 2>/dev/null)
if ! is_number "${stamp:-}"; then
    log "ERROR: heartbeat contains '${stamp:-}', not a timestamp; treating as stalled"
    recover
    exit 1
fi

age=$(( now - stamp ))

# A heartbeat from the future means the clock jumped backwards (NTP correction,
# RTC sync at boot). Left alone the negative age never exceeds MAX_AGE and the
# watchdog is silently disabled for the whole offset.
if [ "$age" -lt 0 ]; then
    log "WARNING: heartbeat is $(( -age ))s in the future - clock skew; cannot judge liveness"
    log "treating as stalled rather than trusting the comparison"
    recover
    exit 1
fi

if [ "$age" -gt "$MAX_AGE" ]; then
    log "ERROR: heartbeat is ${age}s old (limit ${MAX_AGE}s) - daemon is not controlling"
    recover
    exit 1
fi

exit 0
