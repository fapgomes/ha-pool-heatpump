import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "pool_heatpump", "scripts"))
import heatpump_bridge as hb


class StateRegisterMapping(unittest.TestCase):
    """reg1001 = inlet, reg1003 = outlet.

    Verified against recorder history (2026-07-07..17): with the water pump
    running and the compressor OFF both registers read equal; with the
    compressor heating, reg1003 sits 0.7-0.9 C ABOVE reg1001.  In heat mode
    the warmer sensor is by physics the exchanger outlet, so 1003 = outlet
    and 1001 = inlet (the 1.2.0 mapping had them swapped).
    """

    def test_inlet_is_1001_outlet_is_1003(self):
        b = hb.Bridge()
        b.store(1001, [251])   # inlet (pool water going in)
        b.store(1003, [261])   # outlet (heated water going out)
        s = b.state()
        self.assertEqual(s["inlet_water_c"], 25.1)
        self.assertEqual(s["outlet_water_c"], 26.1)

    def test_climate_current_temp_follows_inlet(self):
        # the thermostat should show the real pool temperature (inlet),
        # not the heated water leaving the exchanger
        b = hb.Bridge()
        b.store(1001, [251])
        b.store(1003, [261])
        self.assertEqual(b.state()["water_temp_c"], 25.1)


if __name__ == "__main__":
    unittest.main()
