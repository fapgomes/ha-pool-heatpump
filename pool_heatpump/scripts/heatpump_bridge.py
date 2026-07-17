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
import datetime
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
REG_INLET = 1003     # inlet water temperature (÷10) — the app's main reading
REG_OUTLET = 1001    # outlet water temperature (÷10)
REG_AMBIENT = 307    # ambient/air temperature (×1)
REG_FAULT = 1004     # fault code: high byte = ASCII letter, low byte = number
REG_COMPRESSOR = 1006  # compressor output rate (%, ×1): 0 stopped, 100 full

# mode register (2000) values, verified via the app: cool=1, auto=4; heat=2 from
# the baseline (reg2000 was 2 while the app showed the heat/sun icon)
HVAC_TO_REG = {"cool": 1, "heat": 2, "auto": 4}
REG_TO_HVAC = {v: k for k, v in HVAC_TO_REG.items()}


def decode_fault(v):
    """reg1004 -> fault code string. 0x5001 -> 'P01'; 0 -> 'OK'."""
    if not v:
        return "OK"
    hi, lo = v >> 8, v & 0xFF
    letter = chr(hi) if 32 <= hi < 127 else "?"
    return f"{letter}{lo:02d}"

# -- bridge-level health (as opposed to pump faults in reg 1004) -------------
STATUS_META = {
    "ok": {
        "detail": "Receiving telemetry from the pump normally.",
        "action": "",
    },
    "registration_storm": {
        "detail": "The pump's comms processor crashed and is deaf: it re-sends "
                  "its registration every ~2 s and ignores all replies (even "
                  "the manufacturer cloud's); telemetry has stopped.",
        "action": "It self-recovers after ~24 h. To clear it now, power-cycle "
                  "the heat pump at the breaker (~30 s off); rebooting the "
                  "WiFi module does not help.",
    },
    "no_telemetry": {
        "detail": "The pump is connected but has pushed no telemetry for "
                  "over 5 minutes.",
        "action": "Reboot the WiFi module; if that does not help, "
                  "power-cycle the heat pump at the breaker.",
    },
    "pump_disconnected": {
        "detail": "No TCP connection from the pump's WiFi module for over "
                  "2 minutes.",
        "action": "Check that the module is powered and on the WiFi "
                  "network; press 'Adopt module (point to HA)' if it does "
                  "not reconnect.",
    },
}

REG_STREAK_STORM = 10   # unsolicited registrations (~2 s apart) = storm
NO_TELEMETRY_S = 300    # no telemetry block while connected (normal ≈ 50 s)
DISCONNECTED_S = 120    # no pump TCP connection


def _iso(ts):
    """Epoch seconds -> local ISO-8601 with UTC offset (HA timestamp class)."""
    return (datetime.datetime.fromtimestamp(ts).astimezone()
            .isoformat(timespec="seconds"))


def evaluate_status(connected, disconnected_s, reg_streak, since_block_s):
    """Bridge-level health from raw signals -> a STATUS_META key.

    A healthy pump registers at most once, immediately followed by a full
    dump; an unsolicited registration every ~2 s with no telemetry block in
    between means the pump is not hearing our replies (serial link wedged —
    seen 2026-07-10, fixed only by power-cycling the pump).
    """
    if not connected and disconnected_s > DISCONNECTED_S:
        return "pump_disconnected"
    if reg_streak >= REG_STREAK_STORM:
        return "registration_storm"
    if connected and since_block_s > NO_TELEMETRY_S:
        return "no_telemetry"
    return "ok"


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
    def __init__(self, mqtt_conf=None, mod_conf=None, capture=False):
        self.regs = {}  # addr -> uint16
        self.lock = threading.Lock()
        self.pump_sock = None
        self.pump_ip = None  # IP of the module currently connected to us
        self.pump_lock = threading.Lock()  # serialize writes to the pump socket
        self.tid = 0x1000
        self.last_update = 0.0
        self.last_publish = 0.0
        self.mqtt = None
        self.mqtt_conf = mqtt_conf
        self.mod_conf = mod_conf or {}
        # capture mode: transparently relay pump<->cloud and log every frame
        # (diagnostic; the cloud is the master, local commands are disabled)
        self.capture = capture
        # bridge-level health (see evaluate_status)
        self.status = "ok"
        self.status_since = time.time()
        self.reg_streak = 0                     # registrations since last block
        self.last_block = time.monotonic()
        self.last_block_wall = None             # wall-clock of last telemetry
        self.pump_down_since = time.monotonic()  # None while a pump is connected
        self.polled = True  # armed (set False) per connection in handle_pump
        self.got_settings = False  # settings block (2000) seen this connection

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
                "hvac_mode": (
                    ("off" if r.get(REG_POWER) == 0
                     else REG_TO_HVAC.get(r.get(REG_MODE), "off"))
                    if REG_POWER in r else None
                ),
                "setpoint_c": r.get(REG_SETPOINT),
                # climate current temp = inlet water (the app's main reading)
                "water_temp_c": p.s16(r[REG_INLET]) / 10 if REG_INLET in r else None,
                "inlet_water_c": p.s16(r[REG_INLET]) / 10 if REG_INLET in r else None,
                "outlet_water_c": p.s16(r[REG_OUTLET]) / 10 if REG_OUTLET in r else None,
                "ambient_c": p.s16(r[REG_AMBIENT]) if REG_AMBIENT in r else None,
                "fault": decode_fault(r.get(REG_FAULT, 0)) if REG_FAULT in r else None,
                "compressor_pct": r.get(REG_COMPRESSOR) if REG_COMPRESSOR in r else None,
                "regs_2000": [r.get(2000 + i) for i in range(7)],
                "n_regs": len(r),
                "age_s": round(age, 1) if age is not None else None,
            }

    # -- commands -----------------------------------------------------------
    def _pump_send(self, frame):
        """Send raw bytes to the pump, serialized so frames never interleave."""
        sock = self.pump_sock
        if sock is None:
            raise OSError("pump not connected")
        with self.pump_lock:
            sock.sendall(frame)

    def send_write(self, addr, value):
        self.tid = (self.tid + 1) & 0xFFFF
        frame = p.cmd_write_single(self.tid, addr, value)
        try:
            self._pump_send(frame)
            return f"ok: reg{addr} <- {value} (frame {frame.hex()})"
        except OSError as e:
            return f"send error: {e}"

    def send_raw(self, frame):
        try:
            self._pump_send(frame)
            return f"ok: sent {frame.hex()}"
        except OSError as e:
            return f"send error: {e}"

    # -- pump loop ----------------------------------------------------------
    def _poll_after_first_block(self, sock):
        # At most one poll per connection, 2 s after the FIRST telemetry
        # block, and only if the settings block (2000) has not arrived by
        # itself — after a pump boot the registration is followed by a full
        # dump, so no poll is needed (and polling mid-dump is risky). The
        # post-lone-block moment is the only proven-safe one: the pump just
        # finished transmitting (quiet bus, next push ~50 s away) and its
        # comms processor is fully up. A poll at any other time can crash
        # that processor (tid resets to 1), leaving the pump deaf for ~24 h —
        # the "registration storm". Polling right after connect is NOT safe
        # either: after a power-on the module connects while the pump MCU is
        # still booting, and a poll during boot wedges it too (2026-07-17).
        time.sleep(2)
        if self.pump_sock is sock and not self.got_settings:
            try:
                self.send_raw(POLL_QUERY)
                print("[poll] sent (post-block)", flush=True)
            except OSError:
                pass

    def handle_pump(self, sock, addr):
        print(f"[pump] connected from {addr}", flush=True)
        self.pump_sock = sock
        self.pump_ip = addr[0]
        self.pump_down_since = None
        self.reg_streak = 0
        self.last_block = time.monotonic()
        self.polled = False
        self.got_settings = False
        self._update_status()
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
                self.pump_down_since = time.monotonic()
                self._update_status()
            sock.close()

    def on_frame(self, sock, f, respond=True):
        if f.unit == p.UNIT_TELEMETRY and f.fc == p.FC_WRITE_MULTI:
            start, values = p.decode_write_multi(f)
            self.store(start, values)
            if respond:
                with self.pump_lock:
                    sock.sendall(p.ack_write_multi(f))
            print(f"[block] {start} x{len(values)}", flush=True)
            if start == 2000:
                self.got_settings = True
            self.reg_streak = 0
            self.last_block = time.monotonic()
            self.last_block_wall = time.time()
            self._update_status()
            if respond and not self.polled:
                self.polled = True
                threading.Thread(target=self._poll_after_first_block,
                                 args=(sock,), daemon=True).start()
            # publish as soon as a block arrives (throttled) — some blocks (e.g.
            # ambient in block 300) are pushed only ~once per minute and are not
            # part of the poll dump, so don't wait for the 30 s timer
            now = time.monotonic()
            if now - self.last_publish >= 3:
                self.last_publish = now
                self.publish_state()
        elif f.unit == p.UNIT_TELEMETRY and f.fc == p.FC_REGISTER:
            if respond:
                with self.pump_lock:
                    sock.sendall(p.ack_register(f))
            self.reg_streak += 1
            # don't flood the log during a storm (2 s cadence for hours)
            if self.reg_streak <= 3 or self.reg_streak % 100 == 0:
                print(f"[pump] registration tid={f.tid:#06x} "
                      f"payload={f.payload.hex()}"
                      f" (streak {self.reg_streak})", flush=True)
            self._update_status()
        elif f.unit == p.UNIT_COMMAND and f.fc == p.FC_WRITE_SINGLE:
            # echo of one of our commands — the pump's confirmation
            pass
        elif f.unit == p.UNIT_COMMAND and f.fc == p.FC_REGISTER:
            # benign response to our 0x41 poll query
            pass
        else:
            print(f"[pump] unexpected frame unit={f.unit:#x} fc={f.fc:#x} "
                  f"payload={f.payload.hex(' ')}", flush=True)

    # -- cloud capture (transparent relay + frame logging) -------------------
    def handle_pump_capture(self, sock, addr):
        print(f"[pump] connected from {addr} (capture mode)", flush=True)
        self.pump_sock = sock
        self.pump_ip = addr[0]
        self.pump_down_since = None
        self.reg_streak = 0
        self.last_block = time.monotonic()
        self._update_status()
        host = self.mod_conf.get("cloud_host") or DEFAULT_CLOUD_HOST
        port = int(self.mod_conf.get("cloud_port") or DEFAULT_CLOUD_PORT)
        try:
            cloud = socket.create_connection((host, port), timeout=15)
        except OSError as e:
            print(f"[cap] cloud connect to {host}:{port} failed: {e}; "
                  f"dropping pump so the module retries", flush=True)
            if self.pump_sock is sock:
                self.pump_sock = None
                self.pump_down_since = time.monotonic()
            sock.close()
            return
        cloud.settimeout(None)
        print(f"[cap] relaying to cloud {host}:{port}", flush=True)
        threading.Thread(target=self._relay,
                         args=(cloud, sock, "<cloud", False),
                         daemon=True).start()
        try:
            self._relay(sock, cloud, ">cloud", True)
        finally:
            print("[pump] disconnected (capture mode)", flush=True)
            if self.pump_sock is sock:
                self.pump_sock = None
                self.pump_down_since = time.monotonic()
                self._update_status()
            for s in (sock, cloud):
                try:
                    s.close()
                except OSError:
                    pass

    def _relay(self, src, dst, tag, is_pump_side):
        """Forward raw bytes src->dst, logging every parsed frame."""
        buf = b""
        try:
            while True:
                data = src.recv(4096)
                if not data:
                    break
                dst.sendall(data)  # byte-transparent: forward before parsing
                buf += data
                frames, buf = p.parse_frames(buf)
                for f in frames:
                    print(f"[cap] {tag} tid={f.tid:#06x} unit={f.unit:#04x} "
                          f"fc={f.fc:#04x} payload={f.payload.hex()}",
                          flush=True)
                    if is_pump_side:
                        # passive decode: keep MQTT state fresh, never respond
                        self.on_frame(src, f, respond=False)
        except OSError as e:
            print(f"[cap] {tag} ended: {e}", flush=True)

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
    # command topics we subscribe to (NOT the state topics we publish, so we
    # never receive our own retained messages back)
    CMD_TOPICS = ("set/setpoint", "set/mode_hvac", "set/power", "set/mode",
                  "module/adopt", "module/restore", "module/reboot")

    def mqtt_start(self):
        c = self.mqtt_conf
        # unique client id per process run avoids the broker kicking a lingering
        # session with the same id (which caused a reconnect loop)
        self.mqtt = MqttClient(
            host=c["host"], port=c.get("port", 1883),
            username=c.get("username"), password=c.get("password"),
            client_id=f"heatpump-bridge-{os.getpid()}", keepalive=30,
            on_message=self.on_mqtt, on_connect=self._on_mqtt_connect,
            will_topic=f"{BASE}/availability", will_payload="offline",
            will_retain=True,
        )
        self.mqtt.start()
        threading.Thread(target=self._state_loop, daemon=True).start()
        # read the module target once at startup (not on every reconnect)
        threading.Thread(target=self.publish_module_target, daemon=True).start()

    def _on_mqtt_connect(self):
        # runs after every (re)connect: (re)publish discovery and subscribe to
        # the command topics only
        self.publish_discovery()
        for t in self.CMD_TOPICS:
            self.mqtt.subscribe(f"{BASE}/{t}")
        self.publish_status()

    def publish_discovery(self):
        dev = {
            "identifiers": [NODE],
            "name": "Pool heat pump",
            "manufacturer": "AquaTemp-family (Neoboost / DOTELS)",
            "model": "Full Inverter (local Modbus)",
        }
        # process-alive availability (LWT-backed): every entity uses it
        avail = [{"topic": f"{BASE}/availability"}]
        # telemetry availability: only entities whose values stop being
        # meaningful when telemetry stops; Bridge status / Module target /
        # buttons must stay visible so the user can see the error and act
        tele_avail = avail + [{"topic": f"{BASE}/telemetry/availability"}]
        # climate
        climate = {
            "name": "Pool heat pump",
            "unique_id": f"{NODE}_climate",
            "device": dev,
            "availability": tele_avail, "availability_mode": "all",
            "modes": ["off", "cool", "heat", "auto"],
            "mode_state_topic": f"{BASE}/state",
            "mode_state_template": "{{ value_json.hvac_mode }}",
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
        # temperature sensors
        for key, name, field in (
            ("inlet", "Inlet water temperature", "inlet_water_c"),
            ("outlet", "Outlet water temperature", "outlet_water_c"),
            ("ambient", "Ambient temperature", "ambient_c"),
        ):
            sensor = {
                "name": name,
                "unique_id": f"{NODE}_{key}",
                "device": dev,
                "availability": tele_avail, "availability_mode": "all",
                "state_topic": f"{BASE}/state",
                "value_template": f"{{{{ value_json.{field} }}}}",
                "unit_of_measurement": "°C", "device_class": "temperature",
                "state_class": "measurement",
            }
            self.mqtt.publish(
                f"{DISCOVERY_PREFIX}/sensor/{NODE}/{key}/config",
                json.dumps(sensor), retain=True)
        # compressor output rate (%)
        compressor = {
            "name": "Compressor output rate",
            "unique_id": f"{NODE}_compressor",
            "device": dev,
            "availability": tele_avail, "availability_mode": "all",
            "state_topic": f"{BASE}/state",
            "value_template": "{{ value_json.compressor_pct }}",
            "unit_of_measurement": "%", "state_class": "measurement",
            "icon": "mdi:gauge",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/sensor/{NODE}/compressor/config",
            json.dumps(compressor), retain=True)
        # fault code sensor (e.g. "P01"; "OK" when no fault)
        fault = {
            "name": "Fault code",
            "unique_id": f"{NODE}_fault",
            "device": dev,
            "availability": tele_avail, "availability_mode": "all",
            "state_topic": f"{BASE}/state",
            "value_template": "{{ value_json.fault }}",
            "icon": "mdi:alert-circle-outline",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/sensor/{NODE}/fault/config",
            json.dumps(fault), retain=True)
        # remove the old secondary-temperature sensor from previous versions
        self.mqtt.publish(f"{DISCOVERY_PREFIX}/sensor/{NODE}/temp2/config", "")
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
        reboot = {
            "name": "Reboot module",
            "unique_id": f"{NODE}_reboot",
            "device": dev, "availability": avail,
            "command_topic": f"{BASE}/module/reboot",
            "icon": "mdi:restart",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/button/{NODE}/reboot/config",
            json.dumps(reboot), retain=True)
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
        # bridge-level health (registration storm, stale telemetry, …)
        status = {
            "name": "Bridge status",
            "unique_id": f"{NODE}_bridge_status",
            "device": dev, "availability": avail,
            "entity_category": "diagnostic",
            "state_topic": f"{BASE}/bridge_status",
            "json_attributes_topic": f"{BASE}/bridge_status/attributes",
            "icon": "mdi:lan-check",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/sensor/{NODE}/bridge_status/config",
            json.dumps(status), retain=True)
        # timestamp of the last valid telemetry block ("X minutes ago" in HA);
        # 'None' is MQTT sensor PAYLOAD_NONE -> state "unknown" until the
        # first block arrives
        last_tele = {
            "name": "Last telemetry",
            "unique_id": f"{NODE}_last_telemetry",
            "device": dev, "availability": avail,
            "entity_category": "diagnostic",
            "state_topic": f"{BASE}/bridge_status/attributes",
            "value_template": "{{ value_json.last_telemetry or 'None' }}",
            "device_class": "timestamp",
            "icon": "mdi:clock-check-outline",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/sensor/{NODE}/last_telemetry/config",
            json.dumps(last_tele), retain=True)
        self.mqtt.publish(f"{BASE}/availability", "online", retain=True)

    def publish_state(self):
        if self.mqtt:
            self.mqtt.publish(f"{BASE}/state", json.dumps(self.state()), retain=True)

    # -- bridge status / availability ----------------------------------------
    def _update_status(self):
        now = time.monotonic()
        connected = self.pump_sock is not None
        down_s = 0 if connected else now - (self.pump_down_since or now)
        new = evaluate_status(connected, down_s, self.reg_streak,
                              now - self.last_block)
        if new == self.status:
            return
        meta = STATUS_META[new]
        msg = f"[status] {self.status} -> {new}"
        if meta["action"]:
            msg += f" — {meta['action']}"
        print(msg, flush=True)
        self.status = new
        self.status_since = time.time()
        self.publish_status()

    def publish_status(self):
        if not self.mqtt:
            return
        meta = STATUS_META[self.status]
        attrs = {
            "detail": meta["detail"],
            "action": meta["action"],
            "since": _iso(self.status_since),
            "last_telemetry": (_iso(self.last_block_wall)
                               if self.last_block_wall else None),
        }
        if self.status == "registration_storm":
            attrs["count"] = self.reg_streak
        self.mqtt.publish(f"{BASE}/bridge_status", self.status, retain=True)
        self.mqtt.publish(f"{BASE}/bridge_status/attributes",
                          json.dumps(attrs), retain=True)
        self.mqtt.publish(f"{BASE}/telemetry/availability",
                          "online" if self.status == "ok" else "offline",
                          retain=True)

    def on_mqtt(self, topic, payload):
        # runs in the MQTT reader thread — never block or do I/O here; hand work
        # to a short-lived thread so the reader keeps servicing the socket
        val = payload.decode(errors="replace").strip()
        print(f"[mqtt] cmd {topic} = {val}", flush=True)
        threading.Thread(target=self._handle_cmd, args=(topic, val),
                         daemon=True).start()

    def _handle_cmd(self, topic, val):
        if topic.endswith("/module/adopt"):
            self.module_adopt()
            return
        if topic.endswith("/module/restore"):
            self.module_restore()
            return
        if topic.endswith("/module/reboot"):
            self.module_reboot()
            return
        if self.capture:
            # the cloud is the master while capturing; injecting writes would
            # interleave with its traffic
            print(f"[cap] command ignored in capture mode: {topic} = {val}",
                  flush=True)
            return
        if topic.endswith("/set/setpoint"):
            self.send_write(REG_SETPOINT, int(round(float(val))))
        elif topic.endswith("/set/mode_hvac"):
            if val == "off":
                self.send_write(REG_POWER, 0)
            else:
                reg_mode = HVAC_TO_REG.get(val)
                if reg_mode is not None:
                    self.send_write(REG_MODE, reg_mode)
                self.send_write(REG_POWER, 1)
        elif topic.endswith("/set/power"):
            self.send_write(REG_POWER, int(val))
        elif topic.endswith("/set/mode"):
            self.send_write(REG_MODE, int(val))
        # no poll here: the pump re-pushes the settings block on change, and a
        # poll would suppress the ambient block

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

    def module_reboot(self):
        """Reboot the WiFi module (AT+Z). Useful to force a reconnect."""
        ip = self._find_module_ip()
        if not ip:
            print("[module] reboot: no module found on the LAN", flush=True)
            return
        print(f"[module] reboot: {ip}", flush=True)
        try:
            mod.at(ip, "AT+Z")
        except OSError as e:
            print(f"[module] reboot failed: {e}", flush=True)

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
        # Publish state every ~30 s. NO periodic poll: see _poll_on_connect —
        # mid-session polls can crash the pump's comms processor. The pump
        # pushes all telemetry on its own and re-pushes the settings block
        # (2000) whenever a value changes.
        while True:
            try:
                if self.pump_sock is not None:
                    self.publish_state()
                self._update_status()
                self.publish_status()
            except Exception as e:  # noqa: BLE001
                print(f"[state] loop error: {e}", flush=True)
            time.sleep(30)

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
        handler = self.handle_pump_capture if self.capture else self.handle_pump
        if self.capture:
            print("[cap] capture mode ON: relaying pump<->cloud, "
                  "local commands disabled", flush=True)
        self._accept_loop(LISTEN_PUMP, handler, "pump")

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
            return data.get("mqtt"), data.get("module", {}), data.get("capture", False)
    return None, {}, False


if __name__ == "__main__":
    mqtt_conf, mod_conf, capture = load_conf()
    Bridge(mqtt_conf=mqtt_conf, mod_conf=mod_conf, capture=capture).serve()
