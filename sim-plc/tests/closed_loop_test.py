"""Closed-loop test: real OpenPLC runtime running pump_station.st against the simulator.

This script starts its own simulator (in-process, so it can change demand and
inject faults the way the instructor console does), then talks to the PLC's
Modbus server exactly as the HMI will: it reads HR 0-7 and writes HR 1024-1034.

Prerequisites
  * OpenPLC Runtime running with pump_station.st loaded and the slave device
    pointing at this machine's simulator port (see plc/deploy_to_openplc.py).
  * Nothing else listening on the simulator port.

    python tests/closed_loop_test.py --plc 127.0.0.1 --sim-host 127.0.0.1

Takes about four minutes. Each step prints PASS or FAIL.
"""

import argparse
import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulator"))

from pymodbus.client import ModbusTcpClient                     # noqa: E402
from pymodbus.server import ServerAsyncStop, StartAsyncTcpServer  # noqa: E402

from pump_sim import Simulator                                  # noqa: E402

# PLC holding registers (design 5.2 / 5.3)
DISCH, SUCT, DEMAND, STATUS, ALARMS, LEAD, MODE, HB = range(8)
SP_PRESSURE, SP_LAG_STOP, SP_LOW, SP_HIGH, LAG_START, LAG_STOP, CMD_MODE, CMD_LEAD, MAN_P1, MAN_P2, RESET = range(1024, 1035)

ST_P1_CMD, ST_P1_RUN, ST_P2_CMD, ST_P2_RUN, ST_LAG_RUN, ST_COMMS = (1 << i for i in range(6))
AL_LOW, AL_HIGH, AL_P1, AL_P2, AL_COMM, AL_UNACK = 1, 2, 4, 8, 16, 1 << 15

failures = 0


class SimThread:
    """Runs the simulator's Modbus server and 10 Hz loop on a background asyncio loop."""

    def __init__(self, host, port):
        self.sim = Simulator(demand_pct=50.0, seed=7)
        self.host, self.port = host, port
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.start()

    def start(self):
        async def go():
            self.server = asyncio.create_task(
                StartAsyncTcpServer(context=self.sim.context, address=(self.host, self.port)))
            self.physics = asyncio.create_task(self.sim.run())
        asyncio.run_coroutine_threadsafe(go(), self.loop).result()

    def stop(self):
        async def halt():
            self.physics.cancel()
            await ServerAsyncStop()
        asyncio.run_coroutine_threadsafe(halt(), self.loop).result()


class Plc:
    def __init__(self, host, port):
        self.c = ModbusTcpClient(host, port=port)
        if not self.c.connect():
            sys.exit(f"cannot reach the PLC at {host}:{port}")

    def status(self):
        return self.c.read_holding_registers(0, count=8, device_id=1).registers

    def settings(self):
        return self.c.read_holding_registers(1024, count=11, device_id=1).registers

    def write(self, reg, value):
        self.c.write_register(reg, value, device_id=1)


def check(name, ok, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   ({detail})" if detail else ""))
    failures += 0 if ok else 1


def wait_for(plc, cond, timeout):
    """Poll the PLC status block once a second (like the HMI) until cond(status) is true."""
    end = time.time() + timeout
    while time.time() < end:
        s = plc.status()
        if cond(s):
            return s
        time.sleep(1.0)
    return plc.status()


def fmt(s):
    return (f"P={s[DISCH] / 10:.1f} psi, demand={s[DEMAND] / 10:.1f}%, status=0b{s[STATUS]:06b}, "
            f"alarms=0x{s[ALARMS]:04x}, lead=P{s[LEAD]}, mode={s[MODE]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="127.0.0.1")
    ap.add_argument("--plc-port", type=int, default=502)
    ap.add_argument("--sim-host", default="127.0.0.1")
    ap.add_argument("--sim-port", type=int, default=5020)
    args = ap.parse_args()

    simt = SimThread(args.sim_host, args.sim_port)
    sim = simt.sim
    plc = Plc(args.plc, args.plc_port)

    print("1. Defaults and start-up")
    # Start from a known state: Off, then reset so leftovers from earlier runs clear.
    plc.write(CMD_MODE, 0)
    plc.write(RESET, 1)
    s = wait_for(plc, lambda s: s[STATUS] & (ST_P1_RUN | ST_P2_RUN) == 0, 10)
    st = plc.settings()
    check("setpoints hold design defaults", st[:4] == [1200, 1450, 1100, 1600], str(st[:6]))
    check("simulator comms OK bit set", bool(s[STATUS] & ST_COMMS), fmt(s))
    plc.write(LAG_STOP, 5)        # shorten the 30 s stop delay so the test runs faster
    plc.write(CMD_LEAD, 1)
    plc.write(RESET, 1)
    plc.write(CMD_MODE, 1)
    s = wait_for(plc, lambda s: s[STATUS] & ST_P1_RUN and s[DISCH] > 1300, 40)
    check("Auto: lead P1 runs and pressure builds above 130 psi", s[STATUS] & ST_P1_RUN and s[DISCH] > 1300, fmt(s))
    s = wait_for(plc, lambda s: not s[STATUS] & ST_P2_CMD and abs(s[DISCH] - 1355) < 20, 60)
    check("at 50 % demand one pump is enough: lag off, ~135.5 psi", not s[STATUS] & ST_P2_CMD and abs(s[DISCH] - 1355) < 20, fmt(s))
    check("no alarms after a normal start-up", s[ALARMS] == 0, fmt(s))

    print("2. Demand step 50 -> 75 %: lag pump starts")
    sim.set_demand(75)
    t0 = time.time()
    s = wait_for(plc, lambda s: s[STATUS] & ST_P2_CMD, 30)
    check("lag P2 commanded after pressure stays below 120 psi", bool(s[STATUS] & ST_P2_CMD),
          f"{time.time() - t0:.0f} s after the step; {fmt(s)}")
    s = wait_for(plc, lambda s: s[DISCH] > 1350, 30)
    check("two pumps recover pressure (~139 psi at 75 %)", 1350 < s[DISCH] < 1430, fmt(s))
    time.sleep(10)
    s = plc.status()
    check("lag stays on: below 145 psi, so no short-cycling", bool(s[STATUS] & ST_P2_RUN), fmt(s))

    print("3. Demand back to 50 %: lag pump stops after the stop delay")
    sim.set_demand(50)
    s = wait_for(plc, lambda s: not s[STATUS] & ST_P2_CMD, 40)
    check("lag P2 stopped once pressure > 145 psi for 5 s", not s[STATUS] & ST_P2_CMD, fmt(s))

    print("4. Lead pump fault at 75 % demand: failover to P2")
    sim.set_demand(75)
    time.sleep(2)
    sim.set_fault("p1", True)
    s = wait_for(plc, lambda s: s[LEAD] == 2 and s[STATUS] & ST_P2_RUN, 20)
    check("P2 promoted to lead and running", s[LEAD] == 2 and bool(s[STATUS] & ST_P2_RUN), fmt(s))
    check("P1 fault alarm + unacknowledged bit", s[ALARMS] & AL_P1 and s[ALARMS] & AL_UNACK, fmt(s))
    check("P1 no longer commanded", not s[STATUS] & ST_P1_CMD, fmt(s))
    check("HMI lead selection followed the failover", plc.settings()[CMD_LEAD - 1024] == 2)
    # Losing the lead at 75 % demand also dips pressure below 110 psi while P2
    # starts, and one pump only holds ~111 psi here, so a low alarm is expected too.
    plc.write(RESET, 1)
    time.sleep(2)
    s = plc.status()
    check("Reset while P1 still tripped: acknowledged, P1 alarm stays",
          s[ALARMS] & AL_P1 and not s[ALARMS] & AL_UNACK, fmt(s))
    check("PLC cleared the reset command", plc.settings()[RESET - 1024] == 0)

    print("5. Clear the trip: P1 becomes available as lag, then Reset clears every alarm")
    sim.set_fault("p1", False)
    time.sleep(1)                 # let the PLC poll the cleared fault before resetting
    plc.write(RESET, 1)
    s = wait_for(plc, lambda s: s[STATUS] & ST_P1_RUN and s[STATUS] & ST_LAG_RUN, 30)
    check("P1 restarts as lag (one pump holds only ~111 psi at 75 %)", s[STATUS] & ST_P1_RUN and s[STATUS] & ST_LAG_RUN, fmt(s))
    check("P2 stays lead", s[LEAD] == 2, fmt(s))
    s = wait_for(plc, lambda s: s[DISCH] > 1350, 20)
    plc.write(RESET, 1)
    s = wait_for(plc, lambda s: s[ALARMS] == 0, 10)
    check("all alarms clear", s[ALARMS] == 0, fmt(s))

    print("6. Manual mode: bumpless, then operator stops P1")
    before = plc.status()[STATUS] & (ST_P1_CMD | ST_P2_CMD)
    plc.write(CMD_MODE, 2)
    time.sleep(1.5)
    s = plc.status()
    check("switching to Manual leaves both pumps as they were",
          s[MODE] == 2 and s[STATUS] & (ST_P1_CMD | ST_P2_CMD) == before, fmt(s))
    plc.write(MAN_P1, 0)
    s = wait_for(plc, lambda s: not s[STATUS] & ST_P1_RUN, 10)
    check("Manual stop of P1 obeyed", not s[STATUS] & ST_P1_CMD and not s[STATUS] & ST_P1_RUN, fmt(s))

    print("7. Setting clamps")
    plc.write(SP_PRESSURE, 2000)
    plc.write(SP_LAG_STOP, 1000)
    plc.write(LAG_START, 0)
    time.sleep(1.5)
    st = plc.settings()
    check("SP_PRESSURE 2000 clamped to 1500", st[0] == 1500, str(st))
    check("SP_LAG_STOP raised to SP + 100", st[1] == 1600, str(st))
    check("LAG_START_DELAY 0 clamped to 1", st[4] == 1, str(st))
    for reg, v in ((SP_PRESSURE, 1200), (SP_LAG_STOP, 1450), (LAG_START, 5)):
        plc.write(reg, v)

    print("8. Off mode")
    plc.write(CMD_MODE, 0)
    s = wait_for(plc, lambda s: s[STATUS] & 0b1111 == 0, 10)
    check("both pumps commanded off and stopped", s[STATUS] & 0b1111 == 0, fmt(s))

    print("9. Low-pressure alarm: Auto with both pumps tripped")
    sim.set_fault("p1", True)
    sim.set_fault("p2", True)
    plc.write(CMD_MODE, 1)
    s = wait_for(plc, lambda s: s[ALARMS] & AL_LOW, 40)
    check("low pressure alarm after 15 s arming + 5 s below 110 psi", bool(s[ALARMS] & AL_LOW), fmt(s))
    check("both pump fault alarms", s[ALARMS] & (AL_P1 | AL_P2) == AL_P1 | AL_P2, fmt(s))
    sim.set_fault("p1", False)
    sim.set_fault("p2", False)
    time.sleep(1)
    plc.write(RESET, 1)
    s = wait_for(plc, lambda s: s[DISCH] > 1200, 40)
    plc.write(RESET, 1)
    s = wait_for(plc, lambda s: s[ALARMS] == 0, 10)
    check("station recovers and alarms reset", s[ALARMS] == 0 and s[DISCH] > 1200, fmt(s))

    print("10. Simulator comm loss")
    simt.stop()
    s = wait_for(plc, lambda s: s[ALARMS] & AL_COMM, 15)
    check("comm loss alarm and comms-OK bit cleared", s[ALARMS] & AL_COMM and not s[STATUS] & ST_COMMS, fmt(s))

    plc.write(LAG_STOP, 30)       # put the default back
    print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
