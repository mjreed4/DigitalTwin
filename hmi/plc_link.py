"""Modbus TCP link from the HMI to the PLC (design doc 5.2 to 5.4).

Everything the HMI knows about the PLC's register map is in this file. The
HMI reads holding registers 0-7 (status, read-only) and 1024-1034 (settings
and commands), and writes only to 1024-1034. It never talks to the simulator.

Values are 16-bit unsigned integers; pressures and percentages are scaled x10
(1200 = 120.0 psi).
"""

import threading
import time

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

UNIT_ID = 1

# --- Design 5.2: status block, holding registers 0-7 (PLC %QW0-%QW7) ---------
STATUS_START, STATUS_COUNT = 0, 8
STATUS_NAMES = [
    "HMI_DISCH_PRESS",   # 0  psi x10
    "HMI_SUCT_PRESS",    # 1  psi x10
    "HMI_DEMAND",        # 2  % x10
    "HMI_STATUS",        # 3  bit field
    "HMI_ALARMS",        # 4  bit field
    "HMI_LEAD_PUMP",     # 5  1 = P1, 2 = P2
    "HMI_MODE_ACTIVE",   # 6  0 Off, 1 Auto, 2 Manual
    "PLC_HEARTBEAT",     # 7  +1 per scan
]

STATUS_BITS = {          # HMI_STATUS
    "p1_cmd": 0, "p1_running": 1, "p2_cmd": 2, "p2_running": 3,
    "lag_running": 4, "sim_comms_ok": 5,
}

ALARMS = [               # HMI_ALARMS: (bit, key, text shown to the operator)
    (0, "low_press", "Low discharge pressure"),
    (1, "high_press", "High discharge pressure"),
    (2, "p1_fault", "Pump P1 fault"),
    (3, "p2_fault", "Pump P2 fault"),
    (4, "sim_comm", "Simulator comm loss (PLC field I/O)"),
]
ALARM_UNACK_BIT = 15

MODES = {0: "Off", 1: "Auto", 2: "Manual"}

# --- Design 5.3: settings and commands, holding registers 1024-1034 (%MW0-%MW10)
SETTINGS_START, SETTINGS_COUNT = 1024, 11

# name: (register, scale, units, label, hint shown next to the entry box)
SETTINGS = {
    "SP_PRESSURE":     (1024, 10, "psi", "Pressure setpoint (lag start)", "80.0 to 150.0"),
    "SP_LAG_STOP":     (1025, 10, "psi", "Lag stop pressure", "at least setpoint + 10.0"),
    "SP_LOW_ALARM":    (1026, 10, "psi", "Low pressure alarm", ""),
    "SP_HIGH_ALARM":   (1027, 10, "psi", "High pressure alarm", ""),
    "LAG_START_DELAY": (1028, 1, "s", "Lag start delay", "1 to 60"),
    "LAG_STOP_DELAY":  (1029, 1, "s", "Lag stop delay", "5 to 300"),
}
CMD_MODE = 1030
CMD_LEAD_SELECT = 1031
CMD_MANUAL_P1 = 1032
CMD_MANUAL_P2 = 1033
CMD_ALARM_RESET = 1034

COMMAND_NAMES = {
    1030: "CMD_MODE", 1031: "CMD_LEAD_SELECT", 1032: "CMD_MANUAL_P1",
    1033: "CMD_MANUAL_P2", 1034: "CMD_ALARM_RESET",
}
SETTINGS_REGISTER_NAMES = [n for n, v in sorted(SETTINGS.items(), key=lambda kv: kv[1][0])] + \
    [COMMAND_NAMES[r] for r in sorted(COMMAND_NAMES)]


def bit(word: int, n: int) -> bool:
    return bool(word >> n & 1)


class PlcLink:
    """One Modbus TCP connection to the PLC, shared by the poller and operator writes.

    pymodbus' synchronous client is not safe to use from two threads at once,
    so every request goes through self.lock.
    """

    def __init__(self, host: str, port: int = 502, timeout: float = 1.0):
        self.host, self.port = host, port
        self.client = ModbusTcpClient(host, port=port, timeout=timeout, retries=0)
        self.lock = threading.Lock()

    def _ensure_connected(self) -> None:
        if not self.client.connected and not self.client.connect():
            raise ConnectionError(f"cannot reach the PLC at {self.host}:{self.port}")

    def read_block(self, start: int, count: int) -> list[int]:
        with self.lock:
            self._ensure_connected()
            try:
                rr = self.client.read_holding_registers(start, count=count, device_id=UNIT_ID)
            except ModbusException as exc:
                self.client.close()
                raise ConnectionError(str(exc)) from exc
            if rr.isError():
                raise ConnectionError(f"PLC answered with an error reading HR {start}: {rr}")
            return list(rr.registers)

    def write_register(self, address: int, value: int) -> None:
        if not SETTINGS_START <= address < SETTINGS_START + SETTINGS_COUNT:
            # Design 6: the HMI writes only HR 1024-1034. Status registers belong to the PLC.
            raise ValueError(f"HMI may not write holding register {address}")
        if not 0 <= value <= 65535:
            raise ValueError("value does not fit in a 16-bit register")
        with self.lock:
            self._ensure_connected()
            try:
                rr = self.client.write_register(address, value, device_id=UNIT_ID)
            except ModbusException as exc:
                self.client.close()
                raise ConnectionError(str(exc)) from exc
            if rr.isError():
                raise ConnectionError(f"PLC answered with an error writing HR {address}: {rr}")

    def read_status(self) -> list[int]:
        return self.read_block(STATUS_START, STATUS_COUNT)

    def read_settings(self) -> list[int]:
        return self.read_block(SETTINGS_START, SETTINGS_COUNT)

    def write_and_read_back(self, address: int, value: int, settle: float = 0.5) -> int:
        """Write a setting, give the PLC a few scans to clamp it, return what it kept.

        The PLC writes clamped values back to the same register (sim-plc README),
        so a read-back that differs from what we wrote means the PLC clamped it.
        """
        self.write_register(address, value)
        time.sleep(settle)
        return self.read_block(address, 1)[0]

    def close(self) -> None:
        with self.lock:
            self.client.close()
