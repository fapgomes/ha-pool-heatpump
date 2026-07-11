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
