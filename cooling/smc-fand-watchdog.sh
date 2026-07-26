#!/bin/sh
# Independent safety net for smc-fand (defense-in-depth layer 7).
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
# Run from smc-fand-watchdog.timer every 60s.

set -u

HEARTBEAT="${SMC_HEARTBEAT_PATH:-/run/smc-fand/heartbeat}"
IPMITOOL="${SMC_IPMITOOL:-/usr/bin/ipmitool}"
# Generous relative to the 5s tick: only fires on a genuine stall, not on a
# slow tick caused by IPMI contention with the node-exporter collector.
MAX_AGE="${SMC_HEARTBEAT_MAX_AGE:-60}"

log() { echo "smc-fand-watchdog: $*"; }

force_bmc_control() {
    if timeout 15 "$IPMITOOL" -I open raw 0x30 0x45 0x01 0x00 >/dev/null 2>&1; then
        log "handed fan control back to the BMC (Standard mode)"
    else
        log "ERROR: could not restore BMC fan mode"
    fi
}

if ! systemctl is-active --quiet smc-fand.service; then
    # Not supposed to be running. ExecStopPost should already have restored the
    # BMC, but assert it anyway - this is the layer that assumes nothing.
    mode=$(timeout 15 "$IPMITOOL" -I open raw 0x30 0x45 0x00 2>/dev/null | tr -d ' \n')
    if [ "$mode" != "00" ]; then
        log "daemon inactive but BMC still in mode 0x${mode:-??}; correcting"
        force_bmc_control
    fi
    exit 0
fi

# Daemon claims to be running - verify it is actually doing work.
if [ ! -f "$HEARTBEAT" ]; then
    # Tolerated briefly at startup: the first heartbeat lands after tick one.
    log "no heartbeat file yet at $HEARTBEAT"
    exit 0
fi

now=$(date +%s)
stamp=$(cat "$HEARTBEAT" 2>/dev/null | tr -d ' \n')
case "$stamp" in
    ''|*[!0-9]*)
        log "ERROR: unreadable heartbeat (%s); treating as stalled" "$stamp"
        age=$((MAX_AGE + 1))
        ;;
    *)
        age=$((now - stamp))
        ;;
esac

if [ "$age" -gt "$MAX_AGE" ]; then
    log "ERROR: heartbeat is ${age}s old (limit ${MAX_AGE}s) - daemon is not controlling"
    # Order matters: make the box safe first, then try to recover the daemon.
    force_bmc_control
    log "restarting smc-fand.service"
    systemctl restart smc-fand.service || log "ERROR: restart failed"
    exit 1
fi

exit 0
