import sys, json, base64, urllib.request

GPW = open(sys.argv[1]).read().strip()
GRAFANA = "http://localhost:3000"
AUTH = "Basic " + base64.b64encode(("admin:" + GPW).encode()).decode()

def api(path, data=None):
    req = urllib.request.Request(GRAFANA + path,
        data=(json.dumps(data).encode() if data is not None else None),
        headers={"Content-Type": "application/json", "Authorization": AUTH})
    return json.load(urllib.request.urlopen(req, timeout=15))

# find the Prometheus datasource uid
ds = [d for d in api("/api/datasources") if d.get("type") == "prometheus"]
DSUID = ds[0]["uid"]
DS = {"type": "prometheus", "uid": DSUID}
print("prometheus datasource uid:", DSUID)

# ensure a Loki datasource for logs panels
_loki = [d for d in api("/api/datasources") if d.get("type") == "loki"]
if _loki:
    LOKIUID = _loki[0]["uid"]
else:
    _r = api("/api/datasources", {"name": "Loki", "type": "loki", "url": "http://localhost:3100", "access": "proxy"})
    LOKIUID = (_r.get("datasource") or {}).get("uid") or _r.get("uid")
LOKI = {"type": "loki", "uid": LOKIUID}
print("loki datasource uid:", LOKIUID)

pid = [0]
def nid():
    pid[0] += 1
    return pid[0]

def target(expr, legend=None):
    t = {"refId": chr(65 + (nid() % 26)), "expr": expr, "datasource": DS}
    if legend: t["legendFormat"] = legend
    return t

def stat(title, expr, x, y, w=4, h=4, unit="none", legend=None, decimals=None, color="thresholds"):
    d = {"unit": unit, "color": {"mode": color}, "thresholds": {"steps": [{"color": "green", "value": None}]}}
    if decimals is not None: d["decimals"] = decimals
    return {"id": nid(), "title": title, "type": "stat", "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"colorMode": "value", "graphMode": "area", "reduceOptions": {"calcs": ["lastNotNull"]}},
            "targets": [target(expr, legend)]}

def ts(title, exprs, x, y, w=12, h=7, unit="none", stack=False, fill=12):
    tgs = [target(e[0], e[1]) for e in exprs]
    return {"id": nid(), "title": title, "type": "timeseries", "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, "custom": {"drawStyle": "line", "fillOpacity": fill,
                "lineWidth": 2, "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
            "targets": tgs}

def row(title, y):
    return {"id": nid(), "title": title, "type": "row", "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}

def logs(title, expr, x, y, w=24, h=11):
    return {"id": nid(), "title": title, "type": "logs", "datasource": LOKI,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True,
                        "sortOrder": "Descending", "dedupStrategy": "none", "prettifyLogMessage": False},
            "targets": [{"refId": "A", "expr": expr, "datasource": LOKI}]}

panels = []
# ---- Row 1: GLM inference ----
panels.append(row("GLM-5.2 Inference", 0))
panels.append(stat("TG tok/s", "llamacpp:predicted_tokens_seconds", 0, 1, 4, 4, "none", decimals=1))
panels.append(stat("PP tok/s", "llamacpp:prompt_tokens_seconds", 4, 1, 4, 4, "none", decimals=1))
panels.append(stat("KV cache used", "llamacpp:kv_cache_usage_ratio * 100", 8, 1, 4, 4, "percent", decimals=1))
panels.append(stat("KV tokens", "llamacpp:kv_cache_tokens", 12, 1, 4, 4, "none"))
panels.append(stat("Reqs processing", "llamacpp:requests_processing", 16, 1, 4, 4, "none"))
panels.append(stat("Reqs deferred", "llamacpp:requests_deferred", 20, 1, 4, 4, "none"))
panels.append(ts("Throughput (tok/s)", [("llamacpp:predicted_tokens_seconds", "TG"), ("llamacpp:prompt_tokens_seconds", "PP")], 0, 5, 12, 7))
panels.append(ts("KV cache usage", [("llamacpp:kv_cache_usage_ratio * 100", "KV %")], 12, 5, 12, 7, "percent"))
panels.append(ts("Tokens/sec (1m rate)", [("rate(llamacpp:tokens_predicted_total[1m])", "generated"), ("rate(llamacpp:prompt_tokens_total[1m])", "prompt")], 0, 12, 12, 7))
panels.append(ts("Requests", [("llamacpp:requests_processing", "processing"), ("llamacpp:requests_deferred", "deferred")], 12, 12, 12, 7))

# ---- Row 2: System ----
panels.append(row("System", 19))
CPU = '(1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))) * 100'
MEMU = 'node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes'
panels.append(stat("CPU util", CPU, 0, 20, 4, 4, "percent", decimals=0))
panels.append(stat("Mem used", MEMU, 4, 20, 4, 4, "bytes"))
panels.append(stat("Load (1m)", "node_load1", 8, 20, 4, 4, "none", decimals=1))
panels.append(stat("Mem avail", "node_memory_MemAvailable_bytes", 12, 20, 4, 4, "bytes"))
panels.append(stat("Cores busy (est)", CPU + " * " + "count(count(node_cpu_seconds_total) by (cpu)) / 100", 16, 20, 4, 4, "none", decimals=0))
panels.append(stat("Uptime", "node_time_seconds - node_boot_time_seconds", 20, 20, 4, 4, "s"))
panels.append(ts("CPU utilization", [(CPU, "cpu %")], 0, 24, 12, 7, "percent"))
panels.append(ts("Memory", [(MEMU, "used"), ("node_memory_MemAvailable_bytes", "available")], 12, 24, 12, 7, "bytes"))
panels.append(ts("Disk I/O (bytes/s)", [('rate(node_disk_read_bytes_total[1m])', "read {{device}}"), ('rate(node_disk_written_bytes_total[1m])', "write {{device}}")], 0, 31, 12, 7, "Bps"))
panels.append(ts("Network (bytes/s)", [('rate(node_network_receive_bytes_total{device!="lo"}[1m])', "rx {{device}}"), ('rate(node_network_transmit_bytes_total{device!="lo"}[1m])', "tx {{device}}")], 12, 31, 12, 7, "Bps"))

# ---- Row 3: Thermal ----
panels.append(row("Thermal", 38))
panels.append(stat("Max temp", "max(node_hwmon_temp_celsius)", 0, 39, 4, 4, "celsius", decimals=0))
panels.append(ts("Temperatures (hwmon)", [("node_hwmon_temp_celsius", "{{chip}} {{sensor}}")], 4, 39, 20, 7, "celsius"))

# ---- Row 4: Power & BMC ----
panels.append(row("Power & BMC sensors", 46))
panels.append(stat("Power draw", "bmc_power_watts", 0, 47, 4, 4, "watt", decimals=0))
panels.append(stat("Peak power (30m)", "max_over_time(bmc_power_watts[30m])", 4, 47, 4, 4, "watt", decimals=0))
panels.append(stat("PSUs healthy", "sum(bmc_psu_health)", 8, 47, 4, 4, "none"))
panels.append(stat("Max fan", "max(bmc_fan_rpm)", 12, 47, 4, 4, "rotrpm"))
panels.append(stat("CPU (BMC)", 'bmc_temp_celsius{sensor="CPU Temp"}', 16, 47, 4, 4, "celsius", decimals=0))
panels.append(stat("DIMM (BMC)", 'max(bmc_temp_celsius{sensor=~"DIMM.*"})', 20, 47, 4, 4, "celsius", decimals=0))
panels.append(ts("Power draw (W)", [("bmc_power_watts", "watts")], 0, 51, 12, 7, "watt"))
panels.append(ts("Fans (RPM)", [("bmc_fan_rpm", "{{fan}}")], 12, 51, 12, 7, "rotrpm"))
panels.append(ts("BMC temperatures", [("bmc_temp_celsius", "{{sensor}}")], 0, 58, 24, 7, "celsius"))

# ---- Row 5: Live logs / introspection ----
panels.append(row("Live logs / introspection", 65))
panels.append(logs("Engine activity (prompt-processing / generation / slots)",
                   '{job="glm-server"} |~ "print_timing|kv cache rm|slot released|prompt eval time|launch_slot|update_slots"',
                   0, 66, 24, 11))

dash = {"dashboard": {"uid": "glm-52", "title": "GLM-5.2", "tags": ["glm", "llama.cpp", "system"],
                      "timezone": "browser", "schemaVersion": 39, "version": 0, "refresh": "5s",
                      "time": {"from": "now-30m", "to": "now"}, "panels": panels},
        "overwrite": True, "message": "expanded: LLM + system + thermal"}
r = api("/api/dashboards/db", dash)
print("dashboard:", r.get("status"), "| url:", r.get("url"), "| version:", r.get("version"))
