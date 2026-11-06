"""Pump station process simulator: Modbus TCP server + 10 Hz physics + instructor console.

The PLC (OpenPLC) polls this server as a "slave device" to read the field
instruments and write the pump run commands. Register map: design doc 5.1.

    Coil 0..1            P1_RUN_CMD, P2_RUN_CMD            (written by the PLC)
    Discrete input 0..3  P1_RUNNING, P2_RUNNING, P1_FAULT, P2_FAULT
    Input register 0..3  PT101_DISCH (psi x10), PT100_SUCT (psi x10),
                         DEMAND (% x10), SIM_HEARTBEAT

Run:  python pump_sim.py                 (listens on 0.0.0.0:5020, console on stdin)
      python pump_sim.py --help
"""

import argparse
import asyncio
import cmd
import logging
import threading
import time

from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ServerAsyncStop, StartAsyncTcpServer

from process_model import PumpStation
from profiles import PROFILES

STEP_S = 0.1          # 10 Hz, design doc 3.3
UNIT_ID = 1

# Modbus function codes, used to address the four data tables in the datastore.
FC_COILS, FC_DISCRETE_INPUTS, FC_INPUT_REGISTERS = 1, 2, 4

log = logging.getLogger("pump_sim")


def scale10(value: float) -> int:
    """Engineering value -> 16-bit register scaled x10 (120.0 psi -> 1200)."""
    return max(0, min(65535, round(value * 10)))


class Simulator:
    def __init__(self, demand_pct: float = 50.0, profile: str | None = None, seed: int | None = None):
        self.station = PumpStation(demand=demand_pct / 100.0)
        if seed is not None:
            self.station.rng.seed(seed)
        self.lock = threading.Lock()
        self.profile_name: str | None = None
        self.profile_period: float | None = None
        self.profile_t0 = 0.0
        if profile:
            self.set_profile(profile)

        # pymodbus' device context adds 1 to every address it is given (a Modicon
        # numbering leftover), so each block starts at 1 and protocol address 0
        # lands on its first entry. Code below always uses protocol addresses.
        self.device = ModbusDeviceContext(
            co=ModbusSequentialDataBlock(1, [0] * 2),
            di=ModbusSequentialDataBlock(1, [0] * 4),
            ir=ModbusSequentialDataBlock(1, [0] * 4),
            # Link A has no holding registers; pymodbus insists on a block, so park
            # one out of reach and every holding-register request is refused.
            hr=ModbusSequentialDataBlock(65000, [0]),
        )
        self.context = ModbusServerContext(devices={UNIT_ID: self.device}, single=False)

    # --- instructor actions (called from the console thread) ---------------
    def set_demand(self, pct: float) -> None:
        with self.lock:
            self.profile_name = None
            self.station.demand = max(0.0, min(100.0, pct)) / 100.0

    def set_profile(self, name: str, period_s: float | None = None) -> None:
        if name not in PROFILES:
            raise ValueError(f"unknown profile {name!r}; choose from {', '.join(PROFILES)}")
        with self.lock:
            self.profile_name, self.profile_period = name, period_s
            self.profile_t0 = time.monotonic()

    def set_fault(self, pump: str, faulted: bool) -> None:
        with self.lock:
            getattr(self.station, pump.lower()).faulted = faulted

    def set_noise(self, on: bool) -> None:
        with self.lock:
            self.station.noise_psi = 0.3 if on else 0.0

    # --- the 10 Hz scan --------------------------------------------------
    def step(self) -> None:
        st = self.station
        with self.lock:
            # 1. Read the run commands the PLC last wrote.
            p1_cmd, p2_cmd = self.device.getValues(FC_COILS, 0, 2)
            st.p1.run_cmd, st.p2.run_cmd = bool(p1_cmd), bool(p2_cmd)

            # 2. Update demand from the active profile, then the physics.
            if self.profile_name:
                fn = PROFILES[self.profile_name]
                t = time.monotonic() - self.profile_t0
                st.demand = fn(t, self.profile_period) if self.profile_period else fn(t)
            st.step(STEP_S)

            # 3. Publish the instruments for the PLC to read.
            self.device.setValues(FC_DISCRETE_INPUTS, 0, [
                st.p1.running, st.p2.running, st.p1.faulted, st.p2.faulted,
            ])
            self.device.setValues(FC_INPUT_REGISTERS, 0, [
                scale10(st.pt101_reading()),
                scale10(st.pt100_reading()),
                scale10(st.demand * 100.0),
                st.heartbeat,
            ])

    async def run(self) -> None:
        next_t = time.monotonic()
        while True:
            self.step()
            next_t += STEP_S
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))

    def status_line(self) -> str:
        with self.lock:
            st = self.station

            def pump(p):
                state = "TRIP" if p.faulted else ("RUN " if p.running else "stop")
                return f"{p.name} cmd={'1' if p.run_cmd else '0'} {state}"

            src = f"profile {self.profile_name}" if self.profile_name else "manual"
            return (f"PT-101 {st.discharge_psi:6.1f} psi | demand {st.demand * 100:5.1f} % ({src}) | "
                    f"{pump(st.p1)} | {pump(st.p2)} | hb {st.heartbeat}")


class InstructorConsole(cmd.Cmd):
    intro = ("Pump station simulator console. Type 'help' for commands, 'status' to see the process.\n"
             "Demand and pump faults are set here only; the PLC cannot change them.")
    prompt = "sim> "

    def __init__(self, sim: Simulator, loop: asyncio.AbstractEventLoop, stop: asyncio.Event):
        super().__init__()
        self.sim, self.loop, self.stop = sim, loop, stop

    def emptyline(self):
        pass

    def do_status(self, _arg):
        """status: one line showing pressure, demand and both pumps."""
        print(self.sim.status_line())

    def do_watch(self, arg):
        """watch [seconds]: print status once a second (default 10 s)."""
        secs = int(arg) if arg.strip() else 10
        for _ in range(secs):
            print(self.sim.status_line())
            time.sleep(1.0)

    def do_demand(self, arg):
        """demand <percent>: set demand by hand (0 to 100); stops any profile."""
        try:
            self.sim.set_demand(float(arg))
        except ValueError:
            print("usage: demand <percent>, e.g. demand 75")
            return
        print(self.sim.status_line())

    def do_profile(self, arg):
        """profile step|ramp|sine [period_s] | profile off
        step: 50 % for 30 s, then 75 %       ramp: 30 -> 100 -> 30 % over 10 min
        sine: compressed day, 25 % to 85 %   off:  hold the current demand"""
        parts = arg.split()
        if not parts:
            print(f"profiles: {', '.join(PROFILES)}, off")
            return
        if parts[0] == "off":
            with self.sim.lock:
                self.sim.profile_name = None
            print("profile off; demand held at its current value")
            return
        try:
            period = float(parts[1]) if len(parts) > 1 else None
            self.sim.set_profile(parts[0], period)
        except ValueError as e:
            print(e)
            return
        print(f"profile {parts[0]} started")

    def do_fault(self, arg):
        """fault p1|p2: trip a pump (it stops and reports a fault regardless of its command)."""
        pump = arg.strip().lower()
        if pump not in ("p1", "p2"):
            print("usage: fault p1  or  fault p2")
            return
        self.sim.set_fault(pump, True)
        print(f"{pump.upper()} tripped")

    def do_clear(self, arg):
        """clear p1|p2|all: clear a pump trip (the pump restarts only if the PLC still commands it)."""
        pump = arg.strip().lower() or "all"
        if pump not in ("p1", "p2", "all"):
            print("usage: clear p1 | clear p2 | clear all")
            return
        for p in (("p1", "p2") if pump == "all" else (pump,)):
            self.sim.set_fault(p, False)
        print(f"cleared {pump}")

    def do_noise(self, arg):
        """noise on|off: PT-101 measurement noise (+/-0.3 psi). Off makes trends easier to read."""
        self.sim.set_noise(arg.strip().lower() != "off")

    def do_quit(self, _arg):
        """quit: stop the simulator."""
        self.loop.call_soon_threadsafe(self.stop.set)
        return True

    do_exit = do_quit
    do_EOF = do_quit


async def main_async(args) -> None:
    sim = Simulator(args.demand, args.profile, args.seed)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    server = asyncio.create_task(StartAsyncTcpServer(context=sim.context, address=(args.host, args.port)))
    physics = asyncio.create_task(sim.run())
    print(f"Simulator listening on {args.host}:{args.port} (Modbus TCP, unit {UNIT_ID})")

    if args.no_console:
        async def reporter():
            while True:
                print(sim.status_line(), flush=True)
                await asyncio.sleep(args.report)
        tasks = [asyncio.create_task(reporter())] if args.report else []
        try:
            await stop.wait()   # runs until killed
        finally:
            for t in tasks:
                t.cancel()
    else:
        console = InstructorConsole(sim, loop, stop)
        threading.Thread(target=console.cmdloop, daemon=True).start()
        await stop.wait()

    physics.cancel()
    await ServerAsyncStop()
    server.cancel()


def main() -> None:
    ap = argparse.ArgumentParser(description="Pump station process simulator (Modbus TCP server).")
    ap.add_argument("--host", default="0.0.0.0", help="address to listen on (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=5020, help="Modbus TCP port (default 5020)")
    ap.add_argument("--demand", type=float, default=50.0, help="starting demand in %% (default 50)")
    ap.add_argument("--profile", choices=sorted(PROFILES), help="start with a scripted demand profile")
    ap.add_argument("--no-console", action="store_true",
                    help="run without the interactive console (for containers/services)")
    ap.add_argument("--report", type=float, default=0.0,
                    help="with --no-console, print a status line every N seconds")
    ap.add_argument("--seed", type=int, help="random seed for repeatable PT-101 noise")
    ap.add_argument("-v", "--verbose", action="store_true", help="log Modbus traffic")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
