#!/usr/bin/env python3
"""Build heatpump_bridge.conf from the add-on options.

Reads /data/options.json directly (no Supervisor API needed for user-provided
values). If the MQTT host is left blank, looks up the Supervisor MQTT service
(requires hassio_api). This avoids the bashio "forbidden" failures for reading
plain options.
"""
import json
import os
import urllib.request

OPTIONS = "/data/options.json"
CONF = os.path.join(os.path.dirname(__file__), "heatpump_bridge.conf")


def load_options():
    try:
        with open(OPTIONS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def supervisor_mqtt():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        "http://supervisor/services/mqtt",
        headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r).get("data", {})
    except Exception as e:  # noqa: BLE001
        print(f"[conf] MQTT service lookup failed: {e}", flush=True)
        return None


def main():
    opt = load_options()
    host = opt.get("mqtt_host") or ""
    port = opt.get("mqtt_port") or 1883
    user = opt.get("mqtt_username") or ""
    pw = opt.get("mqtt_password") or ""

    if not host:
        svc = supervisor_mqtt()
        if svc:
            host = svc.get("host", "")
            port = svc.get("port", 1883)
            user = svc.get("username", "")
            pw = svc.get("password", "")
            print(f"[conf] using Supervisor MQTT service at {host}:{port}",
                  flush=True)

    conf = {
        "mqtt": {
            "host": host,
            "port": int(port),
            "username": user,
            "password": pw,
        },
        "module": {
            "module_ip": opt.get("module_ip", ""),
            "bridge_host": opt.get("bridge_host", ""),
            "cloud_host": opt.get("cloud_host") or "www.fzdbiology.com",
            "cloud_port": int(opt.get("cloud_port") or 502),
        },
        "capture": bool(opt.get("cloud_capture")),
    }
    with open(CONF, "w") as f:
        json.dump(conf, f)
    print(f"[conf] mqtt host={host or '(empty)'} port={port} "
          f"module_ip={opt.get('module_ip') or '(auto)'}", flush=True)


if __name__ == "__main__":
    main()
