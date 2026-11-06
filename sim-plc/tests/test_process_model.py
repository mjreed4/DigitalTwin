"""Unit tests for the process model: checks it against the design doc's tables.

    python -m unittest discover -s tests      (from the sim-plc folder)
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulator"))

from process_model import PumpStation, steady_state_pressure  # noqa: E402
from profiles import ramp_profile, sine_profile, step_profile  # noqa: E402


class SteadyState(unittest.TestCase):
    def test_design_table(self):
        # design doc 3.2 (the table rounds to 0.1 psi and 1 % demand)
        for n, d, psi in [(1, 0.60, 127.0), (1, 0.67, 120.0), (1, 0.70, 117.0),
                          (2, 0.60, 145.8), (2, 0.67, 143.5), (2, 1.00, 130.0)]:
            self.assertAlmostEqual(steady_state_pressure(n, d), psi, delta=0.25, msg=f"n={n} d={d}")

    def test_no_pumps_is_suction(self):
        self.assertEqual(steady_state_pressure(0, 0.5), 20.0)


class Dynamics(unittest.TestCase):
    def run_for(self, st, seconds):
        for _ in range(round(seconds / 0.1)):
            st.step(0.1)

    def test_pump_start_and_stop_delays(self):
        st = PumpStation(noise_psi=0.0)
        st.p1.run_cmd = True
        self.run_for(st, 1.9)
        self.assertFalse(st.p1.running)
        self.run_for(st, 0.1)
        self.assertTrue(st.p1.running, "running feedback 2 s after the command")
        st.p1.run_cmd = False
        self.run_for(st, 0.9)
        self.assertTrue(st.p1.running)
        self.run_for(st, 0.1)
        self.assertFalse(st.p1.running, "stopped 1 s after the command drops")

    def test_first_order_lag(self):
        st = PumpStation(demand=0.6, noise_psi=0.0)
        st.p1.running = True
        st.p1.run_cmd = True
        self.run_for(st, 3.0)   # one time constant: ~63 % of the way from 20 to 127
        self.assertAlmostEqual(st.discharge_psi, 20 + 0.632 * (127.0 - 20), delta=1.5)
        self.run_for(st, 30.0)
        self.assertAlmostEqual(st.discharge_psi, 127.0, delta=0.1)

    def test_fault_overrides_command(self):
        st = PumpStation(noise_psi=0.0)
        st.p1.run_cmd = True
        self.run_for(st, 3.0)
        st.p1.faulted = True
        st.step(0.1)
        self.assertFalse(st.p1.running)
        st.p1.faulted = False
        self.run_for(st, 2.0)
        self.assertTrue(st.p1.running, "restarts after the trip clears if still commanded")

    def test_heartbeat_wraps(self):
        st = PumpStation()
        st.heartbeat = 65535
        st.step(0.1)
        self.assertEqual(st.heartbeat, 0)

    def test_noise_bounds(self):
        st = PumpStation(discharge_psi=120.0)
        readings = [st.pt101_reading() for _ in range(2000)]
        self.assertTrue(all(119.7 <= r <= 120.3 for r in readings))


class Profiles(unittest.TestCase):
    def test_step(self):
        self.assertEqual(step_profile(0), 0.50)
        self.assertEqual(step_profile(60), 0.75)

    def test_ramp(self):
        self.assertAlmostEqual(ramp_profile(0), 0.30)
        self.assertAlmostEqual(ramp_profile(300), 1.00)
        self.assertAlmostEqual(ramp_profile(600), 0.30)

    def test_sine_range(self):
        values = [sine_profile(t) for t in range(0, 600, 5)]
        self.assertAlmostEqual(min(values), 0.25, places=2)
        self.assertAlmostEqual(max(values), 0.85, places=2)


if __name__ == "__main__":
    unittest.main()
