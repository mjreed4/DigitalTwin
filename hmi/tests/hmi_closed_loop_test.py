"""End-to-end test: HMI web API -> real OpenPLC runtime -> simulator.

Starts its own simulator (in-process, so it can change demand and trip pumps
like the instructor console), starts the HMI's poller, and drives everything
through the HMI's HTTP API exactly as the browser page does.

Prerequisites (same as sim-plc/tests/closed_loop_test.py)
  * OpenPLC Runtime running pump_station.st, slave device pointing at this
    machine's simulator port (sim-plc/plc/deploy_to_openplc.py).
  * Nothing else listening on the simulator port.

    python tests/hmi_closed_loop_test.py --plc 127.0.0.1 --sim-host 127.0.0.1

Takes about two minutes. Each step prints PASS or FAIL.
"""

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "sim-plc" / "tests"))
sys.path.insert(0, str(HERE.parent.parent / "sim-plc" / "simulator"))

import plc_link as pl                       # noqa: E402
from app import Hmi, create_app             # noqa: E402
from closed_loop_test import SimThread      # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   ({detail})" if detail else ""))
    failures += 0 if ok else 1


class Page:
    """Stands in for the browser: same endpoints, same JSON."""

    def __init__(self, app):
        self.c = app.test_client()

    def state(self):
        return self.c.get("/api/state").get_json()

    def cmd(self, command, value=0):
        return self.c.post("/api/command", json={"command": command, "value": value})

    def setting(self, name, value):
        return self.c.post("/api/setting", json={"name": name, "value": value})

    def wait(self, cond, timeout):
        end = time.time() + timeout
        while time.time() < end:
            s = self.state()
            if s.get("status") and cond(s):
                return s
            time.sleep(1.0)
        return self.state()


def events(s):
    return [e["text"] for e in s["events"]]


def alarm(s, key):
    return next(a["active"] for a in s["alarms"] if a["key"] == key)


def fmt(s):
    st = s["status"]
    return (f"P={st['disch_psi']:.1f} psi, demand={st['demand_pct']:.0f}%, mode={st['mode_text']}, "
            f"P1 {'run' if st['p1_running'] else 'stop'}, P2 {'run' if st['p2_running'] else 'stop'}, "
            f"lead=P{st['lead_pump']}, alarms={[a['key'] for a in s['alarms'] if a['active']]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="127.0.0.1")
    ap.add_argument("--plc-port", type=int, default=502)
    ap.add_argument("--sim-host", default="127.0.0.1")
    ap.add_argument("--sim-port", type=int, default=5020)
    args = ap.parse_args()
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)   # comm-loss steps are noisy

    simt = SimThread(args.sim_host, args.sim_port)
    sim = simt.sim
    hmi = Hmi(pl.PlcLink(args.plc, args.plc_port))
    threading.Thread(target=hmi.poll_forever, daemon=True).start()
    page = Page(create_app(hmi))

    print("1. Page and link")
    check("index page renders", b"PS-1 Booster Pump Station" in page.c.get("/").data)
    page.cmd("mode", 0)
    page.cmd("reset")
    for name, v in [("SP_PRESSURE", 120), ("SP_LAG_STOP", 145), ("SP_LOW_ALARM", 110),
                    ("SP_HIGH_ALARM", 160), ("LAG_START_DELAY", 5), ("LAG_STOP_DELAY", 5)]:
        page.setting(name, v)       # known state; 5 s lag stop delay keeps the test short
    page.cmd("lead", 1)
    s = page.wait(lambda s: not s["status"]["p1_running"] and not s["status"]["p2_running"], 10)
    check("PLC link OK and PLC scanning", s["link_ok"] and s["plc_scanning"])
    check("field I/O OK reported by the PLC", s["status"]["sim_comms_ok"], fmt(s))
    check("Off: both pumps stopped", s["status"]["mode"] == 0 and not s["status"]["p1_running"], fmt(s))

    print("2. Auto start-up from the HMI")
    page.cmd("mode", 1)
    s = page.wait(lambda s: s["status"]["p1_running"] and not s["status"]["p2_cmd"]
                  and abs(s["status"]["disch_psi"] - 135.5) < 2, 70)
    check("Auto: P1 lead running, lag off, ~135.5 psi at 50 %", s["status"]["p1_running"]
          and not s["status"]["p2_cmd"] and abs(s["status"]["disch_psi"] - 135.5) < 2, fmt(s))
    check("event log shows the mode change", "PLC mode is now Auto" in events(s))
    check("trend is collecting samples", len(s["trend"]) >= 10, f"{len(s['trend'])} samples")
    page.cmd("reset")    # clear any start-up leftovers before the alarm checks

    print("3. Settings: accepted, clamped, and the cross-check")
    j = page.setting("SP_PRESSURE", 125.5).get_json()
    check("125.5 psi accepted as-is", j["ok"] and not j["clamped"] and j["value"] == 125.5, j["message"])
    j = page.setting("SP_PRESSURE", 200).get_json()
    check("200 psi clamped by the PLC to 150 and reported", j["clamped"] and j["value"] == 150.0, j["message"])
    s = page.state()
    check("lag stop pushed up to setpoint + 10 (160 psi) shows on the page",
          s["settings"]["SP_LAG_STOP"]["value"] == 160.0, str(s["settings"]["SP_LAG_STOP"]))
    j = page.setting("LAG_START_DELAY", 0).get_json()
    check("lag start delay 0 s clamped to 1 s", j["clamped"] and j["value"] == 1, j["message"])
    page.setting("SP_PRESSURE", 120)
    page.setting("SP_LAG_STOP", 145)
    page.setting("LAG_START_DELAY", 5)
    s = page.state()
    check("settings restored", s["settings"]["SP_PRESSURE"]["value"] == 120 and
          s["settings"]["SP_LAG_STOP"]["value"] == 145)
    r = page.setting("SP_PRESSURE", -5)
    check("negative value refused by the HMI before any Modbus write", r.status_code == 400, r.get_json()["message"])
    r = page.setting("PLC_HEARTBEAT", 1)
    check("unknown setting name refused", r.status_code == 400)
    try:
        hmi.link.write_register(0, 999)
        check("HMI refuses to write status register HR 0", False)
    except ValueError:
        check("HMI refuses to write status register HR 0", True)

    print("4. Demand 75 %: lag pump starts")
    sim.set_demand(75)
    s = page.wait(lambda s: s["status"]["p2_running"] and s["status"]["disch_psi"] > 135, 45)
    check("lag P2 running, pressure recovered", s["status"]["p2_running"] and s["status"]["lag_running"], fmt(s))

    print("5. Lead pump trip: failover, alarm, reset")
    sim.set_fault("p1", True)
    s = page.wait(lambda s: s["status"]["lead_pump"] == 2 and alarm(s, "p1_fault"), 20)
    check("P1 fault alarm active and unacknowledged", alarm(s, "p1_fault") and s["unacked"], fmt(s))
    check("lead moved to P2", s["status"]["lead_pump"] == 2, fmt(s))
    check("event log: ALARM Pump P1 fault", "ALARM  Pump P1 fault" in events(s), str(events(s)[:5]))
    check("event log: lead change", "Lead pump is now P2" in events(s))
    page.cmd("reset")
    s = page.wait(lambda s: not s["unacked"], 5)
    check("Reset acknowledges; P1 fault stays while tripped", not s["unacked"] and alarm(s, "p1_fault"), fmt(s))
    sim.set_fault("p1", False)
    time.sleep(1)
    page.cmd("reset")
    s = page.wait(lambda s: s["status"]["p1_running"], 20)
    page.wait(lambda s: s["status"]["disch_psi"] > 135, 20)
    page.cmd("reset")
    s = page.wait(lambda s: not any(a["active"] for a in s["alarms"]), 10)
    check("trip cleared: P1 back as lag, all alarms clear", s["status"]["p1_running"]
          and not any(a["active"] for a in s["alarms"]), fmt(s))
    check("event log: P1 fault cleared", "Cleared  Pump P1 fault" in events(s))

    print("6. Manual mode")
    page.cmd("mode", 2)
    s = page.wait(lambda s: s["status"]["mode"] == 2, 5)
    check("Manual is bumpless: both pumps still running",
          s["status"]["p1_running"] and s["status"]["p2_running"], fmt(s))
    page.cmd("manual_p1", 0)
    s = page.wait(lambda s: not s["status"]["p1_running"], 10)
    check("Manual stop P1 obeyed", not s["status"]["p1_cmd"] and not s["status"]["p1_running"], fmt(s))
    page.cmd("manual_p1", 1)
    s = page.wait(lambda s: s["status"]["p1_running"], 10)
    check("Manual start P1 obeyed", s["status"]["p1_running"], fmt(s))
    page.cmd("mode", 1)

    print("7. Field I/O loss (simulator stopped)")
    sim.set_demand(50)
    simt.stop()
    s = page.wait(lambda s: alarm(s, "sim_comm"), 15)
    check("simulator comm loss alarm on the HMI", alarm(s, "sim_comm"), fmt(s))
    check("field I/O indicator goes bad", not s["status"]["sim_comms_ok"])
    check("HMI to PLC link still OK", s["link_ok"])
    simt.start()
    page.wait(lambda s: s["status"]["sim_comms_ok"], 15)
    page.cmd("reset")
    s = page.wait(lambda s: not alarm(s, "sim_comm"), 10)
    check("comm loss clears after the simulator returns and Reset", not alarm(s, "sim_comm"), fmt(s))

    print("8. HMI to PLC link loss")
    dead = Hmi(pl.PlcLink(args.plc, 1))     # nothing listens on port 1
    dead.poll_once()
    d = Page(create_app(dead)).state()
    check("unreachable PLC reported, no status shown", not d["link_ok"] and "status" not in d, d["link_error"])
    r = Page(create_app(dead)).cmd("mode", 1)
    check("command to unreachable PLC fails cleanly (HTTP 502)", r.status_code == 502, r.get_json()["message"])

    page.setting("LAG_STOP_DELAY", 30)
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
