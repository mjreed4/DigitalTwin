"""Pump station process model: the "field" side of the digital twin.

Pure Python, no networking, so it can be read and unit-tested on its own.
Everything here follows section 3 of design/pump-station-design.md.

    P_ss(n, d) = 154.7 - 69.6 * (d / n)^2 - 7.3 * d^2    for n = 1 or 2 pumps
    P_ss(0, d) = suction pressure (20 psi)

Discharge pressure follows P_ss through a first-order lag (tau = 3 s).
"""

import random
from dataclasses import dataclass, field

SUCTION_PSI = 20.0
SHUTOFF_HEAD_PSI = 154.7
PUMP_DROOP = 69.6
FRICTION = 7.3
PRESSURE_TAU_S = 3.0
PT101_NOISE_PSI = 0.3
PUMP_START_DELAY_S = 2.0   # motor acceleration: run feedback lags the command
PUMP_STOP_DELAY_S = 1.0    # coast down


def steady_state_pressure(pumps_running: int, demand: float) -> float:
    """Discharge pressure the header settles to, in psi.

    pumps_running: 0, 1 or 2.  demand: 0.0 to 1.0 of pipeline design flow.
    """
    if pumps_running <= 0:
        return SUCTION_PSI
    per_pump = demand / pumps_running
    return SHUTOFF_HEAD_PSI - PUMP_DROOP * per_pump ** 2 - FRICTION * demand ** 2


@dataclass
class Pump:
    """One fixed-speed centrifugal pump with a motor that takes time to start and stop."""

    name: str
    run_cmd: bool = False      # written by the PLC (coil)
    running: bool = False      # feedback to the PLC (discrete input)
    faulted: bool = False      # set and cleared by the instructor
    _timer: float = 0.0

    def step(self, dt: float) -> None:
        if self.faulted:
            # A tripped pump stops at once and ignores its command until cleared.
            self.running = False
            self._timer = 0.0
            return
        if self.run_cmd and not self.running:
            self._timer += dt
            if self._timer >= PUMP_START_DELAY_S - 1e-9:
                self.running, self._timer = True, 0.0
        elif not self.run_cmd and self.running:
            self._timer += dt
            if self._timer >= PUMP_STOP_DELAY_S - 1e-9:
                self.running, self._timer = False, 0.0
        else:
            self._timer = 0.0  # command and feedback agree: nothing pending


@dataclass
class PumpStation:
    demand: float = 0.5                      # 0.0 to 1.0
    discharge_psi: float = SUCTION_PSI       # true header pressure (no noise)
    heartbeat: int = 0
    noise_psi: float = PT101_NOISE_PSI
    p1: Pump = field(default_factory=lambda: Pump("P1"))
    p2: Pump = field(default_factory=lambda: Pump("P2"))
    rng: random.Random = field(default_factory=random.Random)

    @property
    def pumps(self) -> tuple:
        return (self.p1, self.p2)

    @property
    def pumps_running(self) -> int:
        return sum(p.running for p in self.pumps)

    def step(self, dt: float) -> None:
        """Advance the process by dt seconds (the simulator calls this at 10 Hz)."""
        for p in self.pumps:
            p.step(dt)
        target = steady_state_pressure(self.pumps_running, self.demand)
        # First-order lag: move a fraction dt/tau of the way to the target each step.
        alpha = min(1.0, dt / PRESSURE_TAU_S)
        self.discharge_psi += (target - self.discharge_psi) * alpha
        self.heartbeat = (self.heartbeat + 1) & 0xFFFF

    # --- what the transmitters report -------------------------------------
    def pt101_reading(self) -> float:
        noise = self.rng.uniform(-self.noise_psi, self.noise_psi) if self.noise_psi else 0.0
        return max(0.0, min(200.0, self.discharge_psi + noise))   # 0 to 200 psi span

    def pt100_reading(self) -> float:
        return SUCTION_PSI                                          # 0 to 100 psi span
