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

    def test_last_telemetry_sensor(self):
        cfg = discovery(self.b.mqtt, "sensor", "last_telemetry")
        self.assertEqual(cfg["state_topic"], f"{BASE}/bridge_status/attributes")
        self.assertEqual(cfg["value_template"],
                         "{{ value_json.last_telemetry or 'None' }}")
        self.assertEqual(cfg["device_class"], "timestamp")
        self.assertEqual(cfg["entity_category"], "diagnostic")
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
