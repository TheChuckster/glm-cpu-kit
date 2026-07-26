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
# unreadable timestamp, clock skew, bad configuration - must end with the BMC
# in control, never with a silent `exit 0`.
#
# Run from smc-fand-watchdog.timer every 60s.

set -u

HEARTBEAT="${SMC_HEARTBEAT_PATH:-/run/smc-fand/heartbeat}"
IPMITOOL="${SMC_IPMITOOL:-/usr/bin/ipmitool}"
TIMEOUT_BIN="${SMC_TIMEOUT_BIN:-/usr/bin/timeout}"
CALL_TIMEOUT="${SMC_CALL_TIMEOUT:-10}"

# Must exceed the daemon's worst-case tick, or we restart a healthy daemon
# mid-load. Worst case is roughly (IPMI calls per tick) x SMC_CALL_TIMEOUT:
# 7 x 10s = 70s, so 120 leaves margin without being sluggish.
MAX_AGE="${SMC_HEARTBEAT_MAX_AGE:-120}"

log() { echo "smc-fand-watchdog: $*"; }

# Reject anything non-numeric rather than letting `[ ... -gt ... ]` error out
# and fall through to a silent exit 0. "60s" and "1m" are natural things to
# write given systemd's time syntax, and both would disable this check.
is_number() {
    case "${1:-}" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

if ! is_number "$MAX_AGE"; then
    log "ERROR: SMC_HEARTBEAT_MAX_AGE='$MAX_AGE' is not a plain integer number of seconds"
    log "refusing to run with an uninterpretable limit; falling back to 120"
    MAX_AGE=120
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
    log "restarting smc-fand.service"
    systemctl restart smc-fand.service || log "ERROR: restart failed"
}

if ! systemctl is-active --quiet smc-fand.service; then
    # Not supposed to be running. ExecStopPost should already have restored the
    # BMC, but assert it anyway - this is the layer that assumes nothing.
    mode=$("$TIMEOUT_BIN" "$CALL_TIMEOUT" "$IPMITOOL" -I open raw 0x30 0x45 0x00 2>/dev/null | tr -d ' \n')
    if [ -n "$mode" ] && [ "$mode" != "00" ]; then
        log "daemon inactive but BMC still in mode 0x${mode}; correcting"
        force_bmc_control
    fi
    exit 0
fi

now=$(date +%s)

# How long has the unit claimed to be active? A daemon that wedges before its
# first tick never writes a heartbeat at all, and RuntimeDirectory= wipes the
# file on every stop, so "file missing" cannot be tolerated indefinitely - it
# has to be bounded by the service's own uptime. Manual mode is engaged before
# the loop starts, so this window is not harmless.
started=$(systemctl show -p ActiveEnterTimestampMonotonic --value smc-fand.service 2>/dev/null)
uptime_s=""
if is_number "${started:-}" && [ "${started:-0}" -gt 0 ]; then
    mono=$(awk '{printf "%d", $1}' /proc/uptime 2>/dev/null)
    if is_number "${mono:-}"; then
        uptime_s=$(( mono - started / 1000000 ))
    fi
fi

if [ ! -f "$HEARTBEAT" ]; then
    if is_number "${uptime_s:-}" && [ "$uptime_s" -gt "$MAX_AGE" ]; then
        log "ERROR: active for ${uptime_s}s with no heartbeat at $HEARTBEAT"
        log "daemon wedged before its first successful tick; manual mode is engaged with nobody driving"
        recover
        exit 1
    fi
    log "no heartbeat file yet at $HEARTBEAT (service up ${uptime_s:-?}s, grace ${MAX_AGE}s)"
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
