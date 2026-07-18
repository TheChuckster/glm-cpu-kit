#!/usr/bin/env python3
# Standalone Redfish->Prometheus exporter for a bare-metal host BMC.
# Pure HTTPS to the BMC (no IPMI/root). Background-refreshes every 15s; serves
# cached text on scrape so Prometheus scrapes stay fast. Bound to localhost:9101.
import http.server, threading, time, json, ssl, base64, urllib.request, os

BMC = os.environ.get("BMC_URL", "https://BMC_IP")  # EDIT: your BMC/Redfish address (or set $BMC_URL)
USER = os.environ.get("BMC_USER", "ADMIN")
PW = open(os.path.expanduser("~/.bmc-pw")).read().strip()
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
AUTH = "Basic " + base64.b64encode((USER + ":" + PW).encode()).decode()
metrics_text = "# starting\n"
# This board's BMC intermittently reports a bogus ~6144W power value on both
# Redfish and DCMI. Reject implausible readings and hold the last good one.
last_power = [None]
POWER_MIN, POWER_MAX = 50.0, 1500.0

def redfish(path):
    req = urllib.request.Request(BMC + path, headers={"Authorization": AUTH})
    return json.load(urllib.request.urlopen(req, timeout=8, context=CTX))

def esc(s):
    return (s or "").replace('\\', '').replace('"', '').strip()

def refresh():
    global metrics_text
    while True:
        L = ["# HELP bmc_power_watts Chassis power draw (Redfish)",
             "# TYPE bmc_power_watts gauge"]
        try:
            p = redfish("/redfish/v1/Chassis/1/Power")
            for pc in p.get("PowerControl", []):
                w = pc.get("PowerConsumedWatts")
                if w is not None:
                    L.append("bmc_power_raw_watts %s" % w)
                    try:
                        wf = float(w)
                        if POWER_MIN <= wf <= POWER_MAX:
                            last_power[0] = wf
                    except Exception:
                        pass
            if last_power[0] is not None:
                L.append("bmc_power_watts %s" % last_power[0])
            for ps in p.get("PowerSupplies", []):
                nm = esc(ps.get("Name", "psu"))
                h = 1 if ps.get("Status", {}).get("Health") == "OK" else 0
                L.append('bmc_psu_health{psu="%s"} %d' % (nm, h))
                v = ps.get("LineInputVoltage")
                if v is not None: L.append('bmc_psu_input_volts{psu="%s"} %s' % (nm, v))
            L.append("bmc_power_scrape_ok 1")
        except Exception:
            L.append("bmc_power_scrape_ok 0")
        try:
            t = redfish("/redfish/v1/Chassis/1/Thermal")
            for tt in t.get("Temperatures", []):
                r = tt.get("ReadingCelsius")
                if r is not None:
                    L.append('bmc_temp_celsius{sensor="%s"} %s' % (esc(tt.get("Name")), r))
            for f in t.get("Fans", []):
                r = f.get("Reading")
                if r is not None:
                    L.append('bmc_fan_rpm{fan="%s"} %s' % (esc(f.get("Name")), r))
            L.append("bmc_thermal_scrape_ok 1")
        except Exception:
            L.append("bmc_thermal_scrape_ok 0")
        metrics_text = "\n".join(L) + "\n"
        time.sleep(15)

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            b = metrics_text.encode()
            self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass

threading.Thread(target=refresh, daemon=True).start()
http.server.HTTPServer(("127.0.0.1", 9101), H).serve_forever()
