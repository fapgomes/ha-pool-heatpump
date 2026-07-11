# Bridge Status Sensor + Availability (1.5.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a diagnostic "Bridge status" sensor that surfaces bridge-level communication problems (registration storm, stale telemetry, pump disconnected) and make telemetry entities go "unavailable" in HA when there is a problem.

**Architecture:** All detection lives in `heatpump_bridge.py` as a pure function (`evaluate_status`) fed by three counters the pump loop already can maintain (registration streak, last-block time, disconnect time). Status is published to new retained MQTT topics; a second availability topic drives telemetry entities unavailable. `mqtt_min.py` gains MQTT LWT (last-will) so a dead add-on marks everything unavailable.

**Tech Stack:** Python 3 stdlib only (project rule: zero runtime dependencies). Tests: stdlib `unittest`, in a new repo-root `tests/` directory (not shipped in the Docker image, which copies only `scripts/`).

**Spec:** `docs/superpowers/specs/2026-07-11-bridge-status-sensor-design.md`

## Global Constraints

- No new runtime dependencies; stdlib only (both bridge and tests).
- No new add-on options; thresholds are constants in `heatpump_bridge.py`.
- Status states: exactly `ok`, `registration_storm`, `no_telemetry`, `pump_disconnected`.
- Priority: `pump_disconnected` > `registration_storm` > `no_telemetry` > `ok`.
- Thresholds: storm ≥ 10 consecutive registrations with no telemetry block between; no telemetry > 300 s while connected; disconnected > 120 s.
- Two availability topics: `heatpump/pool_heat_pump/availability` (process alive, LWT-backed; ALL entities) and `heatpump/pool_heat_pump/telemetry/availability` (status == ok; only climate + inlet/outlet/ambient + compressor + fault, with `availability_mode: "all"`).
- Bridge status / Module target / buttons must stay available when only telemetry is broken.
- Do NOT use `[skip ci]` in commit messages (repo has no `.gitlab-ci.yml`).
- Version bump to 1.5.0 + CHANGELOG happen in their own dedicated commit (last code task), not mixed with feature commits.
- Run every test command from the repo root: `/home/fapg/Documents/git/ha-pool-heatpump`.

---

### Task 1: Detection logic (`evaluate_status` + `STATUS_META`)

**Files:**
- Modify: `pool_heatpump/scripts/heatpump_bridge.py` (insert after `decode_fault`, around line 61)
- Test: `tests/test_status.py` (create; also create empty `tests/__init__.py` — not needed for `unittest discover`, skip it)

**Interfaces:**
- Produces: module-level `evaluate_status(connected: bool, disconnected_s: float, reg_streak: int, since_block_s: float) -> str` returning a `STATUS_META` key; `STATUS_META: dict[str, dict]` with `detail` and `action` string entries for the four codes; constants `REG_STREAK_STORM = 10`, `NO_TELEMETRY_S = 300`, `DISCONNECTED_S = 120`. Tasks 3–4 rely on these exact names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "pool_heatpump", "scripts"))
import heatpump_bridge as hb


class EvaluateStatus(unittest.TestCase):
    def test_ok_when_connected_and_fresh(self):
        self.assertEqual(hb.evaluate_status(True, 0, 0, 10), "ok")

    def test_single_registration_is_not_a_storm(self):
        self.assertEqual(hb.evaluate_status(True, 0, 1, 10), "ok")

    def test_storm_at_streak_threshold(self):
        self.assertEqual(hb.evaluate_status(True, 0, 10, 30),
                         "registration_storm")

    def test_no_telemetry_after_5_min(self):
        self.assertEqual(hb.evaluate_status(True, 0, 0, 301), "no_telemetry")

    def test_no_telemetry_only_while_connected(self):
        self.assertEqual(hb.evaluate_status(False, 10, 0, 999), "ok")

    def test_disconnected_after_2_min(self):
        self.assertEqual(hb.evaluate_status(False, 121, 0, 999),
                         "pump_disconnected")

    def test_short_disconnect_is_ok(self):
        self.assertEqual(hb.evaluate_status(False, 30, 0, 30), "ok")

    def test_disconnect_beats_storm(self):
        self.assertEqual(hb.evaluate_status(False, 300, 50, 999),
                         "pump_disconnected")

    def test_storm_beats_no_telemetry(self):
        self.assertEqual(hb.evaluate_status(True, 0, 50, 999),
                         "registration_storm")

    def test_meta_covers_all_codes(self):
        for code in ("ok", "registration_storm", "no_telemetry",
                     "pump_disconnected"):
            self.assertIn(code, hb.STATUS_META)
            self.assertIn("detail", hb.STATUS_META[code])
            self.assertIn("action", hb.STATUS_META[code])
        self.assertIn("Power-cycle",
                      hb.STATUS_META["registration_storm"]["action"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL/ERROR with `AttributeError: module 'heatpump_bridge' has no attribute 'evaluate_status'`

- [ ] **Step 3: Write the implementation**

In `pool_heatpump/scripts/heatpump_bridge.py`, after the `decode_fault` function (line 60) and before `POLL_QUERY`, insert:

```python
# -- bridge-level health (as opposed to pump faults in reg 1004) -------------
STATUS_META = {
    "ok": {
        "detail": "Receiving telemetry from the pump normally.",
        "action": "",
    },
    "registration_storm": {
        "detail": "The pump re-sends its registration frame every ~2 s and "
                  "ignores the bridge's replies; telemetry has stopped.",
        "action": "Power-cycle the heat pump at the breaker (~30 s off). "
                  "Rebooting the WiFi module does not fix this.",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS (10 tests, OK)

- [ ] **Step 5: Commit**

```bash
git add tests/test_status.py pool_heatpump/scripts/heatpump_bridge.py
git commit -m "Add bridge-level health evaluation (storm/no-telemetry/disconnected)"
```

---

### Task 2: MQTT last-will (LWT) support in `mqtt_min.py`

**Files:**
- Modify: `pool_heatpump/scripts/mqtt_min.py:32-81` (constructor + `_open`)
- Test: `tests/test_mqtt_min.py` (create)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `MqttClient.__init__` gains keyword args `will_topic=None, will_payload=b"", will_retain=False`; new method `_connect_packet(self) -> bytes` building the full CONNECT packet (used by `_open`). Task 4 passes the will kwargs from `mqtt_start`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mqtt_min.py`:

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "pool_heatpump", "scripts"))
from mqtt_min import MqttClient


def connect_flags(pkt):
    """CONNECT variable header: b'MQTT' + level byte + flags byte."""
    i = pkt.index(b"MQTT") + 4
    return pkt[i + 1]


class ConnectPacket(unittest.TestCase):
    def test_no_will_by_default(self):
        c = MqttClient("h", client_id="cid")
        pkt = c._connect_packet()
        self.assertEqual(pkt[0], 0x10)
        self.assertEqual(connect_flags(pkt) & 0x04, 0)

    def test_will_flags_and_payload(self):
        c = MqttClient("h", client_id="cid", username="u", password="pw",
                       will_topic="t/avail", will_payload="offline",
                       will_retain=True)
        pkt = c._connect_packet()
        flags = connect_flags(pkt)
        self.assertTrue(flags & 0x04)    # will flag
        self.assertTrue(flags & 0x20)    # will retain
        self.assertEqual(flags & 0x18, 0)  # will QoS 0
        self.assertTrue(flags & 0x80)    # username still set
        self.assertTrue(flags & 0x40)    # password still set
        self.assertIn(b"t/avail", pkt)
        self.assertIn(b"offline", pkt)
        # payload order (MQTT 3.1.1): client id, will topic, will msg, user, pass
        self.assertLess(pkt.index(b"cid"), pkt.index(b"t/avail"))
        self.assertLess(pkt.index(b"t/avail"), pkt.index(b"offline"))
        self.assertLess(pkt.index(b"offline"), pkt.index(b"u"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: ERROR — `TypeError: MqttClient.__init__() got an unexpected keyword argument 'will_topic'` (and no `_connect_packet`)

- [ ] **Step 3: Write the implementation**

In `pool_heatpump/scripts/mqtt_min.py`:

Change the constructor signature and add the will fields:

```python
    def __init__(self, host, port=1883, username=None, password=None,
                 client_id="heatpump-bridge", keepalive=30,
                 on_message=None, on_connect=None,
                 will_topic=None, will_payload=b"", will_retain=False):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.keepalive = keepalive
        self.will_topic = will_topic
        self.will_payload = will_payload
        self.will_retain = will_retain
        self.on_message = on_message      # callback(topic:str, payload:bytes)
        self.on_connect = on_connect      # callback() after each (re)connect
        self.sock = None
        self.lock = threading.Lock()
        self.connected = False
```

Add `_connect_packet` and use it in `_open` (replacing the current inline packet build, lines 55-65):

```python
    def _connect_packet(self):
        """Full CONNECT packet (MQTT 3.1.1), incl. optional last-will."""
        flags = 0x02  # clean session
        payload = _str(self.client_id)
        if self.will_topic:
            flags |= 0x04 | (0x20 if self.will_retain else 0x00)  # QoS 0
            will = self.will_payload
            if isinstance(will, str):
                will = will.encode()
            payload += _str(self.will_topic) + struct.pack(">H", len(will)) + will
        if self.username:
            flags |= 0x80
            payload += _str(self.username)
        if self.password:
            flags |= 0x40
            payload += _str(self.password)
        vh = _str("MQTT") + bytes([4, flags]) + struct.pack(">H", self.keepalive)
        pkt = vh + payload
        return bytes([0x10]) + _encode_len(len(pkt)) + pkt

    def _open(self):
        sock = socket.create_connection((self.host, self.port), timeout=10)
        sock.sendall(self._connect_packet())
        hdr = self._recv_packet(sock)
        ...  # rest of _open unchanged from the CONNACK check onward
```

(`_open` keeps everything from `hdr = self._recv_packet(sock)` onward exactly as it is today.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS (12 tests, OK)

- [ ] **Step 5: Commit**

```bash
git add tests/test_mqtt_min.py pool_heatpump/scripts/mqtt_min.py
git commit -m "mqtt_min: support MQTT 3.1.1 last-will (LWT) in CONNECT"
```

---

### Task 3: Wire detection into the Bridge (counters, status publishing)

**Files:**
- Modify: `pool_heatpump/scripts/heatpump_bridge.py` — `__init__` (76-87), `handle_pump` (147-169), `on_frame` (171-197), after `publish_state` (380-382), `_on_mqtt_connect` (255-260), `_state_loop` (489-505)
- Test: `tests/test_bridge_status.py` (create)

**Interfaces:**
- Consumes: `evaluate_status`, `STATUS_META` from Task 1.
- Produces: `Bridge._update_status()` (re-evaluate; on change: log once + publish), `Bridge.publish_status()` (publish `{BASE}/bridge_status`, `{BASE}/bridge_status/attributes` JSON, `{BASE}/telemetry/availability`), instance fields `status`, `status_since`, `reg_streak`, `last_block`, `pump_down_since`. Task 4's discovery references the same topics.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bridge_status.py`:

```python
import json
import os
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "pool_heatpump", "scripts"))
import heatpump_bridge as hb
import heatpump_proto as p


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))
        return True

    def last(self, topic):
        for t, payload, _ in reversed(self.published):
            if t == topic:
                return payload
        return None


class FakeSock:
    def sendall(self, data):
        pass


REG_FRAME = p.Frame(1, p.UNIT_TELEMETRY, p.FC_REGISTER,
                    bytes.fromhex("000000050a")
                    + bytes.fromhex("0003000334eae742e9f2"))


def block_frame():
    payload = struct.pack(">HHB", 1000, 2, 4) + struct.pack(">HH", 255, 0)
    return p.Frame(1, p.UNIT_TELEMETRY, p.FC_WRITE_MULTI, payload)


def make_bridge():
    b = hb.Bridge()
    b.mqtt = FakeMqtt()
    b.pump_sock = FakeSock()   # "connected"
    b.pump_down_since = None
    return b


BASE = hb.BASE


class BridgeStatus(unittest.TestCase):
    def test_starts_ok(self):
        b = make_bridge()
        b._update_status()
        self.assertEqual(b.status, "ok")

    def test_storm_detected_and_published(self):
        b = make_bridge()
        sock = FakeSock()
        for _ in range(hb.REG_STREAK_STORM):
            b.on_frame(sock, REG_FRAME)
        self.assertEqual(b.status, "registration_storm")
        self.assertEqual(b.mqtt.last(f"{BASE}/bridge_status"),
                         "registration_storm")
        self.assertEqual(b.mqtt.last(f"{BASE}/telemetry/availability"),
                         "offline")
        attrs = json.loads(b.mqtt.last(f"{BASE}/bridge_status/attributes"))
        self.assertIn("Power-cycle", attrs["action"])
        self.assertEqual(attrs["count"], hb.REG_STREAK_STORM)
        self.assertIn("since", attrs)

    def test_block_clears_storm(self):
        b = make_bridge()
        sock = FakeSock()
        for _ in range(hb.REG_STREAK_STORM):
            b.on_frame(sock, REG_FRAME)
        b.on_frame(sock, block_frame())
        self.assertEqual(b.status, "ok")
        self.assertEqual(b.mqtt.last(f"{BASE}/telemetry/availability"),
                         "online")

    def test_no_telemetry_when_blocks_stop(self):
        b = make_bridge()
        b.last_block = time.monotonic() - (hb.NO_TELEMETRY_S + 1)
        b._update_status()
        self.assertEqual(b.status, "no_telemetry")

    def test_pump_disconnected(self):
        b = make_bridge()
        b.pump_sock = None
        b.pump_down_since = time.monotonic() - (hb.DISCONNECTED_S + 1)
        b._update_status()
        self.assertEqual(b.status, "pump_disconnected")

    def test_status_survives_without_mqtt(self):
        b = hb.Bridge()          # no mqtt at all
        b.pump_sock = FakeSock()
        b.pump_down_since = None
        for _ in range(hb.REG_STREAK_STORM):
            b.on_frame(FakeSock(), REG_FRAME)
        self.assertEqual(b.status, "registration_storm")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: ERRORs — `AttributeError: 'Bridge' object has no attribute '_update_status'` / `reg_streak`

- [ ] **Step 3: Write the implementation**

All in `pool_heatpump/scripts/heatpump_bridge.py`.

**(a)** `__init__` — add after `self.last_publish = 0.0`:

```python
        # bridge-level health (see evaluate_status)
        self.status = "ok"
        self.status_since = time.time()
        self.reg_streak = 0                     # registrations since last block
        self.last_block = time.monotonic()
        self.pump_down_since = time.monotonic()  # None while a pump is connected
```

**(b)** `handle_pump` — set connect/disconnect markers. After `self.pump_ip = addr[0]` add:

```python
        self.pump_down_since = None
        self.reg_streak = 0
        self.last_block = time.monotonic()
        self._update_status()
```

and in the `finally` block, inside the `if self.pump_sock is sock:` guard, after `self.pump_sock = None`:

```python
                self.pump_down_since = time.monotonic()
                self._update_status()
```

**(c)** `on_frame` — telemetry branch: after `print(f"[block] ...")` add:

```python
            self.reg_streak = 0
            self.last_block = time.monotonic()
            self._update_status()
```

registration branch: replace

```python
        elif f.unit == p.UNIT_TELEMETRY and f.fc == p.FC_REGISTER:
            with self.pump_lock:
                sock.sendall(p.ack_register(f))
            print(f"[pump] registration MAC {f.payload[5:].hex()}", flush=True)
```

with

```python
        elif f.unit == p.UNIT_TELEMETRY and f.fc == p.FC_REGISTER:
            with self.pump_lock:
                sock.sendall(p.ack_register(f))
            self.reg_streak += 1
            # don't flood the log during a storm (2 s cadence for hours)
            if self.reg_streak <= 3 or self.reg_streak % 100 == 0:
                print(f"[pump] registration MAC {f.payload[5:].hex()}"
                      f" (streak {self.reg_streak})", flush=True)
            self._update_status()
```

**(d)** after `publish_state` (line 382) add the two methods:

```python
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
            "since": time.strftime("%Y-%m-%dT%H:%M:%S",
                                   time.localtime(self.status_since)),
        }
        if self.status == "registration_storm":
            attrs["count"] = self.reg_streak
        self.mqtt.publish(f"{BASE}/bridge_status", self.status, retain=True)
        self.mqtt.publish(f"{BASE}/bridge_status/attributes",
                          json.dumps(attrs), retain=True)
        self.mqtt.publish(f"{BASE}/telemetry/availability",
                          "online" if self.status == "ok" else "offline",
                          retain=True)
```

**(e)** `_on_mqtt_connect` — add as last line (fresh broker session gets current status):

```python
        self.publish_status()
```

**(f)** `_state_loop` — status must be evaluated even with no pump connected, and the retained attributes (storm `count`) refresh every 30 s. Replace the loop body:

```python
        while True:
            try:
                if self.pump_sock is not None:
                    if tick % 10 == 0:
                        self.send_raw(POLL_QUERY)
                        time.sleep(2)
                    self.publish_state()
                self._update_status()
                self.publish_status()
            except Exception as e:  # noqa: BLE001
                print(f"[state] loop error: {e}", flush=True)
            tick += 1
            time.sleep(30)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS (18 tests, OK)

- [ ] **Step 5: Commit**

```bash
git add tests/test_bridge_status.py pool_heatpump/scripts/heatpump_bridge.py
git commit -m "Bridge: detect and publish bridge status; throttle storm logging"
```

---

### Task 4: Discovery — Bridge status sensor, availability split, LWT hookup

**Files:**
- Modify: `pool_heatpump/scripts/heatpump_bridge.py` — `mqtt_start` (240-253), `publish_discovery` (262-378)
- Test: `tests/test_discovery.py` (create)

**Interfaces:**
- Consumes: topics from Task 3 (`{BASE}/bridge_status`, `{BASE}/bridge_status/attributes`, `{BASE}/telemetry/availability`); `will_*` kwargs from Task 2.
- Produces: HA discovery config `homeassistant/sensor/pool_heat_pump/bridge_status/config`; telemetry entities gain the second availability topic with `availability_mode: "all"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_discovery.py`:

```python
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "pool_heatpump", "scripts"))
import heatpump_bridge as hb
from test_bridge_status import FakeMqtt

BASE = hb.BASE
DISC = hb.DISCOVERY_PREFIX
NODE = hb.NODE


def discovery(mqtt, component, key):
    payload = mqtt.last(f"{DISC}/{component}/{NODE}/{key}/config")
    return json.loads(payload) if payload else None


class Discovery(unittest.TestCase):
    def setUp(self):
        self.b = hb.Bridge()
        self.b.mqtt = FakeMqtt()
        self.b.publish_discovery()

    def test_bridge_status_sensor(self):
        cfg = discovery(self.b.mqtt, "sensor", "bridge_status")
        self.assertEqual(cfg["state_topic"], f"{BASE}/bridge_status")
        self.assertEqual(cfg["json_attributes_topic"],
                         f"{BASE}/bridge_status/attributes")
        self.assertEqual(cfg["entity_category"], "diagnostic")
        # must NOT depend on the telemetry availability topic
        topics = [a["topic"] for a in cfg["availability"]]
        self.assertNotIn(f"{BASE}/telemetry/availability", topics)

    def test_telemetry_entities_use_both_availability_topics(self):
        for component, key in (("climate", None), ("sensor", "inlet"),
                               ("sensor", "outlet"), ("sensor", "ambient"),
                               ("sensor", "compressor"), ("sensor", "fault")):
            topic = (f"{DISC}/climate/{NODE}/config" if component == "climate"
                     else f"{DISC}/sensor/{NODE}/{key}/config")
            cfg = json.loads(self.b.mqtt.last(topic))
            topics = [a["topic"] for a in cfg["availability"]]
            self.assertIn(f"{BASE}/availability", topics, key)
            self.assertIn(f"{BASE}/telemetry/availability", topics, key)
            self.assertEqual(cfg["availability_mode"], "all", key)

    def test_buttons_and_module_target_stay_available(self):
        for component, key in (("button", "adopt"), ("button", "restore"),
                               ("button", "reboot"),
                               ("sensor", "module_target")):
            cfg = discovery(self.b.mqtt, component, key)
            topics = [a["topic"] for a in cfg["availability"]]
            self.assertEqual(topics, [f"{BASE}/availability"], key)


if __name__ == "__main__":
    unittest.main()
```

Note: `publish_discovery` today publishes `{BASE}/availability` "online" at the end — with FakeMqtt that is fine.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — bridge_status discovery is None; telemetry entities lack the second topic

- [ ] **Step 3: Write the implementation**

In `publish_discovery`, replace the single `avail` list with two:

```python
        # process-alive availability (LWT-backed): every entity uses it
        avail = [{"topic": f"{BASE}/availability"}]
        # telemetry availability: only entities whose values stop being
        # meaningful when telemetry stops; Bridge status / Module target /
        # buttons must stay visible so the user can see the error and act
        tele_avail = avail + [{"topic": f"{BASE}/telemetry/availability"}]
```

For the climate config and the inlet/outlet/ambient/compressor/fault sensor configs, change `"availability": avail,` to:

```python
            "availability": tele_avail, "availability_mode": "all",
```

(buttons, `module_target` keep `"availability": avail` unchanged.)

Add the new sensor just before the final `self.mqtt.publish(f"{BASE}/availability", "online", retain=True)`:

```python
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
```

In `mqtt_start`, pass the will to the client (LWT — broker marks everything offline if the process dies):

```python
        self.mqtt = MqttClient(
            host=c["host"], port=c.get("port", 1883),
            username=c.get("username"), password=c.get("password"),
            client_id=f"heatpump-bridge-{os.getpid()}", keepalive=30,
            on_message=self.on_mqtt, on_connect=self._on_mqtt_connect,
            will_topic=f"{BASE}/availability", will_payload="offline",
            will_retain=True,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS (21 tests, OK)

- [ ] **Step 5: Commit**

```bash
git add tests/test_discovery.py pool_heatpump/scripts/heatpump_bridge.py
git commit -m "Discovery: Bridge status sensor, telemetry availability, MQTT LWT"
```

---

### Task 5: Fake pump script + local end-to-end check

**Files:**
- Create: `tests/fake_pump.py`
- No production changes.

**Interfaces:**
- Consumes: the bridge running locally (`python3 heatpump_bridge.py` with no conf → no MQTT, logs only).
- Produces: a manual repro/verification tool, referenced by DOCS in Task 6.

- [ ] **Step 1: Write the fake pump script**

Create `tests/fake_pump.py`:

```python
#!/usr/bin/env python3
"""Replay pump behaviours against a locally-running bridge (no MQTT needed).

Terminal 1:  cd pool_heatpump/scripts && python3 heatpump_bridge.py
Terminal 2:  python3 tests/fake_pump.py storm|healthy|silent [host [port]]

storm    registration frame every 2 s, never telemetry (wedged pump,
         as seen 2026-07-10) -> bridge must log
         "[status] ok -> registration_storm" after ~10 frames
healthy  telemetry block every 30 s -> bridge stays "ok"
silent   connect and send nothing -> "[status] ok -> no_telemetry"
         after 5 min; kill it (Ctrl-C) and after 2 min the bridge logs
         "[status] ... -> pump_disconnected"
"""
import socket
import struct
import sys
import time


def frame(tid, unit, fc, payload):
    body = bytes([unit, fc]) + payload
    return struct.pack(">HHH", tid, 0, len(body)) + body


REG_PAYLOAD = (bytes.fromhex("000000050a")
               + bytes.fromhex("0003000334eae742e9f2"))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "storm"
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 8502
    s = socket.create_connection((host, port))
    print(f"connected to {host}:{port}, mode={mode}")
    tid = 1
    while True:
        if mode == "storm":
            s.sendall(frame(tid, 0x01, 0x41, REG_PAYLOAD))
            time.sleep(2)
        elif mode == "healthy":
            payload = (struct.pack(">HHB", 1000, 2, 4)
                       + struct.pack(">HH", 255, 0))
            s.sendall(frame(tid, 0x01, 0x10, payload))
            time.sleep(30)
        else:  # silent
            time.sleep(60)
        tid = (tid + 1) & 0xFFFF


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the storm scenario end-to-end**

Terminal 1 (background): `cd pool_heatpump/scripts && python3 heatpump_bridge.py`
Terminal 2: `python3 tests/fake_pump.py storm`

Expected in the bridge output within ~25 s:
- `[pump] connected from ('127.0.0.1', ...)`
- three `[pump] registration MAC ... (streak N)` lines, then silence (log throttle)
- `[status] ok -> registration_storm — Power-cycle the heat pump at the breaker (~30 s off). Rebooting the WiFi module does not fix this.`

Then kill the fake pump and the bridge.

- [ ] **Step 3: Commit**

```bash
git add tests/fake_pump.py
git commit -m "tests: fake pump replay tool (storm/healthy/silent)"
```

---

### Task 6: Docs + version bump 1.5.0 (dedicated commit)

**Files:**
- Modify: `pool_heatpump/DOCS.md` (Entities section, line 32-46)
- Modify: `pool_heatpump/CHANGELOG.md` (prepend)
- Modify: `pool_heatpump/config.yaml:2` (version)

**Interfaces:** none (documentation/release only).

- [ ] **Step 1: Document the new sensor in DOCS.md**

In the Entities list of `pool_heatpump/DOCS.md`, after the "Compressor output rate" bullet, add:

```markdown
- **Bridge status** sensor (diagnostic) — bridge-side communication health:
  `ok`, `registration_storm` (pump re-registers every ~2 s and ignores the
  bridge; power-cycle the heat pump at the breaker — rebooting the WiFi
  module does not help), `no_telemetry` (connected but silent > 5 min) or
  `pump_disconnected` (no TCP connection > 2 min). The `detail`, `action`,
  `since` and `count` attributes explain the state. While the status is not
  `ok`, the climate and telemetry sensors show as **unavailable** instead of
  keeping stale values.
```

- [ ] **Step 2: CHANGELOG entry**

Prepend to `pool_heatpump/CHANGELOG.md`:

```markdown
## 1.5.0

- New **Bridge status** diagnostic sensor: `ok` / `registration_storm` /
  `no_telemetry` / `pump_disconnected`, with `detail`, `action`, `since` and
  `count` attributes. Detects the "registration storm" wedge (pump re-sends
  its 0x41 registration every ~2 s and ignores replies — fixed only by
  power-cycling the pump at the breaker, seen 2026-07-10).
- Telemetry entities (climate, temperatures, compressor, fault) now become
  **unavailable** when telemetry stops, instead of showing stale values
  (dedicated availability topic + MQTT last-will if the add-on dies).
- Registration frames are no longer logged on every occurrence during a
  storm (first 3, then every 100th).
```

- [ ] **Step 3: Bump the version**

`pool_heatpump/config.yaml` line 2: `version: "1.4.0"` → `version: "1.5.0"`.

- [ ] **Step 4: Run the full test suite once more**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS

- [ ] **Step 5: Commit (version bump commit, separate from the feature commits)**

```bash
git add pool_heatpump/DOCS.md pool_heatpump/CHANGELOG.md pool_heatpump/config.yaml
git commit -m "Release 1.5.0: Bridge status sensor + telemetry availability"
```

---

### Task 7: Deploy to the live HA and verify

**Files:** none (operations).

**Interfaces:**
- Consumes: the released 1.5.0 on GitHub (`fapgomes/ha-pool-heatpump`), the user's HA (`ssh ha`), add-on slug `b04e1353_pool_heatpump`.

- [ ] **Step 1: Ask the user for permission to push** (hard rule: never push without an explicit instruction). The add-on installs from GitHub, so deploying requires the commits on `origin/main`.

- [ ] **Step 2: After the push, update the add-on on the HA host**

```bash
ssh ha "ha store reload && ha addons update b04e1353_pool_heatpump"
```

Expected: update to 1.5.0 (if `ha addons update` says already up to date, wait ~1 min after the reload and retry).

- [ ] **Step 3: Verify startup and status**

```bash
ssh ha "docker logs --timestamps --since 5m addon_b04e1353_pool_heatpump 2>&1 | tail -30"
```

Expected: `[mqtt] connected`, `[pump] connected from ('192.168.1.41', ...)`, telemetry `[block]` lines, and no `[status]` transitions away from `ok`.

```bash
ssh ha "docker exec addon_b04e1353_pool_heatpump sh -c 'echo get | nc 127.0.0.1 9000'"
```

Expected: JSON state with fresh `age_s`.

- [ ] **Step 4: Verify the new entity and availability in HA**

Ask the user to check the device page (Settings → Devices → Pool heat pump):
- New diagnostic sensor **Bridge status** = `ok`, with `detail`/`action`/`since` attributes.
- Then stop the add-on (`ssh ha "ha addons stop b04e1353_pool_heatpump"`), confirm climate + telemetry sensors AND Bridge status show **unavailable** (LWT), then start it again (`ha addons start ...`) and confirm everything recovers to `ok`/available.

- [ ] **Step 5: Close out** — confirm with the user that HA now surfaces problems, and update the project memory file if any detection threshold was tuned during verification.

---

## Self-Review Notes

- Spec coverage: sensor + attributes (T3/T4), detection incl. thresholds and priority (T1/T3), availability + exempt entities (T4), LWT (T2/T4), single storm log + log throttle (T3), local fake-pump verification (T5), live verification (T7), version/CHANGELOG (T6). Spec's "Bridge status must not use availability" is implemented as "must not use *telemetry* availability" — it still uses the LWT topic so a dead add-on doesn't leave a stale `ok`; this refinement is deliberate.
- No placeholders; every code step contains the full code.
- Names consistent across tasks: `evaluate_status`, `STATUS_META`, `REG_STREAK_STORM`, `NO_TELEMETRY_S`, `DISCONNECTED_S`, `_update_status`, `publish_status`, `reg_streak`, `last_block`, `pump_down_since`, topics `{BASE}/bridge_status`, `{BASE}/bridge_status/attributes`, `{BASE}/telemetry/availability`.
