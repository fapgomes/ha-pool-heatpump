#!/usr/bin/env python3
"""Local bridge for the pool heat pump — replaces the manufacturer cloud.

The pump mainboard connects here (via a transparent WiFi/serial module in TCP
Client mode). The bridge:
  - answers registration (FC 0x41) and ACKs the pushed telemetry (FC 0x10);
  - decodes the registers into a state dictionary;
  - accepts commands on a local control port (127.0.0.1:CTRL_PORT) and injects
    them into the pump connection as FC 0x06 (unit 0x81);
  - publishes state and Home Assistant auto-discovery over MQTT, and turns HA
    commands into register writes.

Control port (text lines):
  get                 -> print current JSON state
  set <addr> <value>  -> write a register (e.g. "set 2004 30" = setpoint 30 C)
  setpoint <c>        -> shortcut for reg 2004
  power <0|1>         -> shortcut for reg 2001
  mode <n>            -> shortcut for reg 2000
  poll                -> request a full register dump (0x41 query)

No external dependencies (stdlib only).
"""
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import heatpump_proto as p
import dotels_module as mod
from mqtt_min import MqttClient

LISTEN_PUMP = ("0.0.0.0", 8502)
LISTEN_CTRL = ("127.0.0.1", 9000)
BRIDGE_PORT = 8502

REG_MODE = 2000
REG_POWER = 2001
REG_SETPOINT = 2004

POLL_QUERY = bytes.fromhex("000000000009814100000001020000")

# MQTT / Home Assistant topics
DISCOVERY_PREFIX = "homeassistant"
NODE = "pool_heat_pump"
BASE = f"heatpump/{NODE}"
CONF_PATH = os.path.join(os.path.dirname(__file__), "heatpump_bridge.conf")

# module adoption / rollback defaults (AquaTemp/fzdbiology cloud)
DEFAULT_CLOUD_HOST = "www.fzdbiology.com"
DEFAULT_CLOUD_PORT = 502


class Bridge:
    def __init__(self, mqtt_conf=None, mod_conf=None):
        self.regs = {}  # addr -> uint16
        self.lock = threading.Lock()
        self.pump_sock = None
        self.pump_ip = None  # IP of the module currently connected to us
        self.tid = 0x1000
        self.last_update = 0.0
        self.mqtt = None
        self.mqtt_conf = mqtt_conf
        self.mod_conf = mod_conf or {}

    # -- state --------------------------------------------------------------
    def store(self, start, values):
        with self.lock:
            for i, v in enumerate(values):
                self.regs[start + i] = v
            self.last_update = time.monotonic()

    def state(self):
        with self.lock:
            r = self.regs
            age = time.monotonic() - self.last_update if self.last_update else None
            return {
                "power": r.get(REG_POWER),
                "mode": r.get(REG_MODE),
                "setpoint_c": r.get(REG_SETPOINT),
                "water_temp_c": p.s16(r.get(1001, 0)) / 10 if 1001 in r else None,
                "temp_1003_c": p.s16(r.get(1003, 0)) / 10 if 1003 in r else None,
                "regs_2000": [r.get(2000 + i) for i in range(7)],
                "n_regs": len(r),
                "age_s": round(age, 1) if age is not None else None,
            }

    # -- commands -----------------------------------------------------------
    def send_write(self, addr, value):
        sock = self.pump_sock
        if sock is None:
            return "error: pump not connected"
        self.tid = (self.tid + 1) & 0xFFFF
        frame = p.cmd_write_single(self.tid, addr, value)
        try:
            sock.sendall(frame)
            return f"ok: reg{addr} <- {value} (frame {frame.hex()})"
        except OSError as e:
            return f"send error: {e}"

    def send_raw(self, frame):
        sock = self.pump_sock
        if sock is None:
            return "error: pump not connected"
        try:
            sock.sendall(frame)
            return f"ok: sent {frame.hex()}"
        except OSError as e:
            return f"send error: {e}"

    # -- pump loop ----------------------------------------------------------
    def handle_pump(self, sock, addr):
        print(f"[pump] connected from {addr}", flush=True)
        self.pump_sock = sock
        self.pump_ip = addr[0]
        buf = b""
        sock.settimeout(120)
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                buf += data
                frames, buf = p.parse_frames(buf)
                for f in frames:
                    self.on_frame(sock, f)
        except OSError as e:
            print(f"[pump] error: {e}", flush=True)
        finally:
            print("[pump] disconnected", flush=True)
            if self.pump_sock is sock:
                self.pump_sock = None
                # keep pump_ip: it's still the module's IP for adopt/restore
            sock.close()

    def on_frame(self, sock, f):
        if f.unit == p.UNIT_TELEMETRY and f.fc == p.FC_WRITE_MULTI:
            start, values = p.decode_write_multi(f)
            self.store(start, values)
            sock.sendall(p.ack_write_multi(f))
            print(f"[block] {start} x{len(values)}", flush=True)
        elif f.unit == p.UNIT_TELEMETRY and f.fc == p.FC_REGISTER:
            sock.sendall(p.ack_register(f))
            print(f"[pump] registration MAC {f.payload[5:].hex()}", flush=True)
        elif f.unit == p.UNIT_COMMAND and f.fc == p.FC_WRITE_SINGLE:
            # echo of one of our commands — the pump's confirmation
            pass
        else:
            print(f"[pump] unexpected frame unit={f.unit:#x} fc={f.fc:#x} "
                  f"payload={f.payload.hex(' ')}", flush=True)

    # -- control loop -------------------------------------------------------
    def handle_ctrl(self, sock, addr):
        f = sock.makefile("rw")
        for line in f:
            line = line.strip()
            if not line:
                continue
            reply = self.dispatch(line)
            f.write(reply + "\n")
            f.flush()
        sock.close()

    def dispatch(self, line):
        parts = line.split()
        cmd = parts[0].lower()
        try:
            if cmd == "get":
                return json.dumps(self.state())
            if cmd == "set":
                return self.send_write(int(parts[1]), int(parts[2]))
            if cmd == "setpoint":
                return self.send_write(REG_SETPOINT, int(parts[1]))
            if cmd == "power":
                return self.send_write(REG_POWER, int(parts[1]))
            if cmd == "mode":
                return self.send_write(REG_MODE, int(parts[1]))
            if cmd == "poll":
                # replicate the 0x41 query the cloud uses to request a full dump
                return self.send_raw(POLL_QUERY)
            if cmd == "raw":
                return self.send_raw(bytes.fromhex(parts[1]))
            return f"error: unknown command '{cmd}'"
        except (IndexError, ValueError) as e:
            return f"syntax error: {e}"

    # -- MQTT / Home Assistant ---------------------------------------------
    def mqtt_start(self):
        c = self.mqtt_conf
        self.mqtt = MqttClient(
            host=c["host"], port=c.get("port", 1883),
            username=c.get("username"), password=c.get("password"),
            client_id="heatpump-bridge", keepalive=30,
            on_message=self.on_mqtt,
        )
        self.mqtt.connect()
        self.publish_discovery()
        self.mqtt.subscribe(f"{BASE}/set/#")
        self.mqtt.subscribe(f"{BASE}/module/#")
        threading.Thread(target=self._state_loop, daemon=True).start()
        print(f"[mqtt] connected to {c['host']}:{c.get('port', 1883)}", flush=True)
        threading.Thread(target=self.publish_module_target, daemon=True).start()

    def publish_discovery(self):
        dev = {
            "identifiers": [NODE],
            "name": "Pool heat pump",
            "manufacturer": "AquaTemp-family (Neoboost / DOTELS)",
            "model": "Full Inverter (local Modbus)",
        }
        avail = [{"topic": f"{BASE}/availability"}]
        # climate
        climate = {
            "name": "Pool heat pump",
            "unique_id": f"{NODE}_climate",
            "device": dev,
            "availability": avail,
            "modes": ["off", "heat"],
            "mode_state_topic": f"{BASE}/state",
            "mode_state_template": "{{ 'heat' if value_json.power == 1 else 'off' }}",
            "mode_command_topic": f"{BASE}/set/mode_hvac",
            "current_temperature_topic": f"{BASE}/state",
            "current_temperature_template": "{{ value_json.water_temp_c }}",
            "temperature_state_topic": f"{BASE}/state",
            "temperature_state_template": "{{ value_json.setpoint_c }}",
            "temperature_command_topic": f"{BASE}/set/setpoint",
            "min_temp": 15, "max_temp": 40, "temp_step": 1,
            "temperature_unit": "C",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/climate/{NODE}/config",
            json.dumps(climate), retain=True)
        # extra sensor: secondary temperature
        sensor = {
            "name": "Pool secondary temperature",
            "unique_id": f"{NODE}_temp2",
            "device": dev, "availability": avail,
            "state_topic": f"{BASE}/state",
            "value_template": "{{ value_json.temp_1003_c }}",
            "unit_of_measurement": "°C", "device_class": "temperature",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/sensor/{NODE}/temp2/config",
            json.dumps(sensor), retain=True)
        # buttons to adopt / restore the WiFi module
        adopt = {
            "name": "Adopt module (point to HA)",
            "unique_id": f"{NODE}_adopt",
            "device": dev, "availability": avail,
            "command_topic": f"{BASE}/module/adopt",
            "icon": "mdi:lan-connect",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/button/{NODE}/adopt/config",
            json.dumps(adopt), retain=True)
        restore = {
            "name": "Restore module to cloud",
            "unique_id": f"{NODE}_restore",
            "device": dev, "availability": avail,
            "command_topic": f"{BASE}/module/restore",
            "icon": "mdi:cloud-upload",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/button/{NODE}/restore/config",
            json.dumps(restore), retain=True)
        # sensor showing the module's current data target (NETP)
        target = {
            "name": "Module target",
            "unique_id": f"{NODE}_module_target",
            "device": dev, "availability": avail,
            "state_topic": f"{BASE}/module/target",
            "icon": "mdi:server-network",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/sensor/{NODE}/module_target/config",
            json.dumps(target), retain=True)
        self.mqtt.publish(f"{BASE}/availability", "online", retain=True)

    def publish_state(self):
        if self.mqtt:
            self.mqtt.publish(f"{BASE}/state", json.dumps(self.state()), retain=True)

    def on_mqtt(self, topic, payload):
        val = payload.decode(errors="replace").strip()
        print(f"[mqtt] cmd {topic} = {val}", flush=True)
        if topic.endswith("/module/adopt"):
            self.module_adopt()
            return
        if topic.endswith("/module/restore"):
            self.module_restore()
            return
        if topic.endswith("/set/setpoint"):
            self.send_write(REG_SETPOINT, int(round(float(val))))
        elif topic.endswith("/set/mode_hvac"):
            self.send_write(REG_POWER, 0 if val == "off" else 1)
        elif topic.endswith("/set/power"):
            self.send_write(REG_POWER, int(val))
        elif topic.endswith("/set/mode"):
            self.send_write(REG_MODE, int(val))
        time.sleep(1.5)
        self.send_raw(POLL_QUERY)  # refresh the 2000 block

    # -- module management (adopt / restore) --------------------------------
    def _find_module_ip(self):
        # 1) explicit config wins
        ip = self.mod_conf.get("module_ip")
        if ip:
            return ip
        # 2) the module currently connected to us is unambiguous
        if self.pump_ip:
            return self.pump_ip
        # 3) discover on the LAN; several HF modules may exist, so filter
        found = mod.discover()
        for f in found:
            name = f["name"].upper()
            if "DOTELS" in name or "SWP" in name:
                return f["ip"]
        if len(found) == 1:
            return found[0]["ip"]
        if found:
            names = ", ".join(f"{f['name']}@{f['ip']}" for f in found)
            print(f"[module] ambiguous — set module_ip. Candidates: {names}",
                  flush=True)
        return None

    def module_adopt(self):
        """Point the WiFi module at this add-on (local, no cloud)."""
        ip = self._find_module_ip()
        if not ip:
            print("[module] adopt: no module found on the LAN", flush=True)
            return
        host = self.mod_conf.get("bridge_host") or mod.local_ip_towards(ip)
        print(f"[module] adopt: {ip} -> {host}:{BRIDGE_PORT}", flush=True)
        mod.set_target(ip, host, BRIDGE_PORT)
        time.sleep(12)
        self.publish_module_target()

    def module_restore(self):
        """Point the WiFi module back at the manufacturer cloud."""
        ip = self._find_module_ip()
        if not ip:
            print("[module] restore: no module found on the LAN", flush=True)
            return
        host = self.mod_conf.get("cloud_host") or DEFAULT_CLOUD_HOST
        port = self.mod_conf.get("cloud_port") or DEFAULT_CLOUD_PORT
        print(f"[module] restore: {ip} -> {host}:{port}", flush=True)
        mod.set_target(ip, host, port)
        time.sleep(12)
        self.publish_module_target()

    def publish_module_target(self):
        if not self.mqtt:
            return
        try:
            ip = self._find_module_ip()
            target = mod.get_target(ip) if ip else None
        except OSError:
            target = None
        self.mqtt.publish(f"{BASE}/module/target", target or "unknown", retain=True)

    def _state_loop(self):
        while True:
            if self.pump_sock is not None:
                self.send_raw(POLL_QUERY)  # request a periodic full dump
                time.sleep(2)
                self.publish_state()
            time.sleep(28)

    # -- startup ------------------------------------------------------------
    def serve(self):
        if self.mqtt_conf:
            try:
                self.mqtt_start()
            except Exception as e:  # noqa: BLE001
                print(f"[mqtt] connection failed: {e}", flush=True)
        threading.Thread(target=self._accept_loop,
                         args=(LISTEN_CTRL, self.handle_ctrl, "ctrl"),
                         daemon=True).start()
        self._accept_loop(LISTEN_PUMP, self.handle_pump, "pump")

    @staticmethod
    def _accept_loop(bind, handler, name):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(bind)
        srv.listen(5)
        print(f"[{name}] listening on {bind}", flush=True)
        while True:
            cli, addr = srv.accept()
            threading.Thread(target=handler, args=(cli, addr), daemon=True).start()


def load_conf():
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH) as f:
            data = json.load(f)
            return data.get("mqtt"), data.get("module", {})
    return None, {}


if __name__ == "__main__":
    mqtt_conf, mod_conf = load_conf()
    Bridge(mqtt_conf=mqtt_conf, mod_conf=mod_conf).serve()
