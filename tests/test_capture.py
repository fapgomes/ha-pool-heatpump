import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "pool_heatpump", "scripts"))
import heatpump_bridge as hb
from test_bridge_status import FakeMqtt, REG_FRAME, block_frame


class RecordingSock:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)


class CaptureMode(unittest.TestCase):
    def test_observe_does_not_respond_but_decodes(self):
        b = hb.Bridge(capture=True)
        b.mqtt = FakeMqtt()
        b.pump_sock = RecordingSock()
        b.pump_down_since = None
        sock = RecordingSock()
        b.on_frame(sock, block_frame(), respond=False)
        b.on_frame(sock, REG_FRAME, respond=False)
        self.assertEqual(sock.sent, [])          # never acks in capture
        self.assertEqual(b.regs.get(1000), 255)  # still decodes telemetry
        self.assertEqual(b.reg_streak, 1)        # still tracks status signals

    def test_on_frame_default_still_responds(self):
        b = hb.Bridge()
        b.mqtt = FakeMqtt()
        b.pump_sock = RecordingSock()
        b.pump_down_since = None
        sock = RecordingSock()
        b.on_frame(sock, REG_FRAME)
        self.assertEqual(len(sock.sent), 1)      # local mode keeps acking

    def test_conf_plumbs_capture_flag(self):
        # load_conf returns (mqtt, module, capture); default False shape
        self.assertEqual(len(hb.load_conf()), 3)


if __name__ == "__main__":
    unittest.main()
