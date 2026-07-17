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
        self.assertIn("power-cycle",
                      hb.STATUS_META["registration_storm"]["action"].lower())
        self.assertIn("24", hb.STATUS_META["registration_storm"]["action"])


if __name__ == "__main__":
    unittest.main()
