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
        self.assertIsNone(attrs["last_telemetry"])  # no block received yet

    def test_last_telemetry_attribute_set_by_block(self):
        b = make_bridge()
        b.on_frame(FakeSock(), block_frame())
        b.publish_status()
        attrs = json.loads(b.mqtt.last(f"{BASE}/bridge_status/attributes"))
        self.assertIsNotNone(attrs["last_telemetry"])
        # local ISO-8601 with UTC offset, parseable by HA's timestamp class
        self.assertRegex(attrs["last_telemetry"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

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
