"""Scripted demand profiles for the instructor console (design doc section 3.4).

Each profile is a function of elapsed seconds that returns demand as a
fraction (0.0 to 1.0).
"""

import math


def step_profile(t: float, hold_s: float = 30.0) -> float:
    """50 % for hold_s seconds, then a step to 75 % that stays.

    75 % is above what one pump can hold (about 67 %), so the lag pump
    has to start: the clearest single demonstration of lead/lag.
    """
    return 0.50 if t < hold_s else 0.75


def ramp_profile(t: float, period_s: float = 600.0) -> float:
    """Slow ramp 30 -> 100 -> 30 % over period_s (default 10 minutes), then repeats."""
    phase = (t % period_s) / period_s          # 0..1
    tri = 1.0 - abs(2.0 * phase - 1.0)         # 0 -> 1 -> 0
    return 0.30 + 0.70 * tri


def sine_profile(t: float, period_s: float = 600.0) -> float:
    """Daily-style demand: a compressed day (default 10 minutes), 25 % at night to 85 % at peak.

    Starts at the overnight low so the class sees the lag pump come on as the
    "morning" peak builds and drop off again in the "evening".
    """
    return 0.55 - 0.30 * math.cos(2.0 * math.pi * t / period_s)


PROFILES = {
    "step": step_profile,
    "ramp": ramp_profile,
    "sine": sine_profile,
}
