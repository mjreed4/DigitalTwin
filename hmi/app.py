"""Pump station HMI: a small Flask web app that supervises the PLC over Modbus TCP.

A background thread polls the PLC once a second (design 5.4), keeps a trend
history and an event log, and the browser page reads that state as JSON.
Operator actions (mode, lead pump, manual start/stop, setpoints, alarm reset)
become single Modbus register writes to HR 1024-1034.

Run:  python app.py --plc 10.10.0.20             (serves http://0.0.0.0:8000)
      python app.py --help
"""

import argparse
import collections
import logging
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template, request

import plc_link as pl

POLL_S = 1.0             # design 5.4: read HR 0-7 and 1024-1034 once per second
TREND_SECONDS = 900      # 15 minutes of history, one sample per poll
EVENT_LOG_SIZE = 200
HEARTBEAT_STALE_S = 3.0  # PLC heartbeat unchanged this long = PLC not scanning

log = logging.getLogger("hmi")


def now_text() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Hmi:
    """Holds the latest PLC data, the trend, and the event log. Shared by all browsers."""

    def __init__(self, link: pl.PlcLink):
        self.link = link
        self.lock = threading.Lock()
        self.status: list[int] | None = None
        self.settings: list[int] | None = None
        self.link_ok = False
        self.link_error = "not polled yet"
        self.last_poll = 0.0
        self.heartbeat = None
        self.heartbeat_changed_at = time.time()
        self.trend = collections.deque(maxlen=int(TREND_SECONDS / POLL_S))
        self.events = collections.deque(maxlen=EVENT_LOG_SIZE)
        self.prev_alarms: int | None = None
        self.prev_mode = self.prev_lead = None
        self.poll_lock = threading.Lock()   # poller thread and operator writes both poll

    # ---- polling ---------------------------------------------------------
    def poll_forever(self) -> None:
        while True:
            t0 = time.time()
            self.poll_once()
            time.sleep(max(0.0, POLL_S - (time.time() - t0)))

    def poll_once(self) -> None:
        with self.poll_lock:
            self._poll()

    def _poll(self) -> None:
        try:
            status = self.link.read_status()
            settings = self.link.read_settings()
        except (ConnectionError, OSError) as exc:
            with self.lock:
                if self.link_ok:
                    self.event("comm", f"Lost communication with the PLC: {exc}")
                self.link_ok, self.link_error = False, str(exc)
            return

        t = time.time()
        with self.lock:
            if not self.link_ok:
                self.event("comm", f"Communication with the PLC at {self.link.host}:{self.link.port} OK")
            self.link_ok, self.link_error = True, ""
            if status[7] != self.heartbeat:
                self.heartbeat, self.heartbeat_changed_at = status[7], t
            self.log_changes(status)
            self.status, self.settings, self.last_poll = status, settings, t
            self.trend.append({
                "t": t,
                "disch": status[0] / 10,
                "demand": status[2] / 10,
                "sp": settings[0] / 10,
                "lag_stop": settings[1] / 10,
                "p1": pl.bit(status[3], pl.STATUS_BITS["p1_running"]),
                "p2": pl.bit(status[3], pl.STATUS_BITS["p2_running"]),
            })

    def log_changes(self, status: list[int]) -> None:
        """Turn alarm-bit and mode changes into event log lines (caller holds the lock)."""
        alarms, mode, lead = status[4], status[6], status[5]
        if self.prev_alarms is None:
            for b, _key, text in pl.ALARMS:
                if pl.bit(alarms, b):
                    self.event("alarm", f"ALARM  {text} (already latched when the HMI connected)")
        else:
            for b, _key, text in pl.ALARMS:
                was, now = pl.bit(self.prev_alarms, b), pl.bit(alarms, b)
                if now and not was:
                    self.event("alarm", f"ALARM  {text}")
                elif was and not now:
                    self.event("clear", f"Cleared  {text}")
            if pl.bit(self.prev_alarms, pl.ALARM_UNACK_BIT) and not pl.bit(alarms, pl.ALARM_UNACK_BIT):
                self.event("ack", "Alarms acknowledged")
        if self.prev_mode is not None and mode != self.prev_mode:
            self.event("info", f"PLC mode is now {pl.MODES.get(mode, mode)}")
        if self.prev_lead is not None and lead != self.prev_lead:
            self.event("info", f"Lead pump is now P{lead}")
        self.prev_alarms, self.prev_mode, self.prev_lead = alarms, mode, lead

    def event(self, kind: str, text: str) -> None:
        self.events.appendleft({"time": now_text(), "kind": kind, "text": text})
        log.info("%s", text)

    # ---- state for the browser -------------------------------------------
    def snapshot(self, since: float) -> dict:
        with self.lock:
            out = {
                "link_ok": self.link_ok,
                "link_error": self.link_error,
                "plc": f"{self.link.host}:{self.link.port}",
                "age_s": round(time.time() - self.last_poll, 1) if self.last_poll else None,
                "plc_scanning": time.time() - self.heartbeat_changed_at < HEARTBEAT_STALE_S,
                "events": list(self.events)[:50],
                "trend": [p for p in self.trend if p["t"] > since],
            }
            if self.status is None:
                return out
            s, st = self.status, self.settings
            out["status"] = {
                "disch_psi": s[0] / 10,
                "suct_psi": s[1] / 10,
                "demand_pct": s[2] / 10,
                **{k: pl.bit(s[3], b) for k, b in pl.STATUS_BITS.items()},
                "lead_pump": s[5],
                "mode": s[6],
                "mode_text": pl.MODES.get(s[6], f"? ({s[6]})"),
                "heartbeat": s[7],
            }
            out["alarms"] = [
                {"key": key, "text": text, "active": pl.bit(s[4], b)} for b, key, text in pl.ALARMS
            ]
            out["unacked"] = pl.bit(s[4], pl.ALARM_UNACK_BIT)
            out["settings"] = {
                name: {"value": st[reg - pl.SETTINGS_START] / scale, "units": units,
                       "label": label, "hint": hint}
                for name, (reg, scale, units, label, hint) in pl.SETTINGS.items()
            }
            out["commands"] = {
                "mode": st[pl.CMD_MODE - pl.SETTINGS_START],
                "lead_select": st[pl.CMD_LEAD_SELECT - pl.SETTINGS_START],
                "manual_p1": st[pl.CMD_MANUAL_P1 - pl.SETTINGS_START],
                "manual_p2": st[pl.CMD_MANUAL_P2 - pl.SETTINGS_START],
            }
            out["raw"] = {"hr0_7": s, "hr1024_1034": st,
                          "names0_7": pl.STATUS_NAMES, "names1024_1034": pl.SETTINGS_REGISTER_NAMES}
            return out

    # ---- operator actions -------------------------------------------------
    def set_setting(self, name: str, value: float) -> dict:
        reg, scale, units, label, _hint = pl.SETTINGS[name]
        raw = round(value * scale)
        if not 0 <= raw <= 65535:
            raise ValueError(f"{label}: {value:g} {units} cannot be sent; a register holds 0 to {65535 / scale:g} {units}")
        kept = self.link.write_and_read_back(reg, raw)
        kept_eng = kept / scale
        if kept != raw:
            msg = f"{label}: asked for {value:g} {units}, PLC clamped it to {kept_eng:g} {units}"
        else:
            msg = f"{label} set to {kept_eng:g} {units}"
        with self.lock:
            self.event("operator", msg)
        # SP_PRESSURE can push SP_LAG_STOP up too, so refresh everything right away.
        self.poll_once()
        return {"ok": True, "clamped": kept != raw, "value": kept_eng, "message": msg}

    def command(self, what: str, value: int) -> dict:
        if what == "mode" and value in pl.MODES:
            reg, msg = pl.CMD_MODE, f"Operator selected {pl.MODES[value]} mode"
        elif what == "lead" and value in (1, 2):
            reg, msg = pl.CMD_LEAD_SELECT, f"Operator selected P{value} as lead"
        elif what in ("manual_p1", "manual_p2") and value in (0, 1):
            reg = pl.CMD_MANUAL_P1 if what == "manual_p1" else pl.CMD_MANUAL_P2
            msg = f"Operator {'started' if value else 'stopped'} {what[-2:].upper()} (manual)"
        elif what == "reset":
            reg, value, msg = pl.CMD_ALARM_RESET, 1, "Operator pressed Reset"
        else:
            raise ValueError(f"unknown command {what}={value}")
        self.link.write_register(reg, value)
        with self.lock:
            self.event("operator", msg)
        time.sleep(0.3)   # a few PLC scans, so the page shows the result on its next refresh
        self.poll_once()
        return {"ok": True, "message": msg}


def create_app(hmi: Hmi) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html", settings=pl.SETTINGS)

    @app.get("/api/state")
    def state():
        return jsonify(hmi.snapshot(since=float(request.args.get("since", 0))))

    @app.post("/api/setting")
    def setting():
        body = request.get_json(force=True)
        name = body.get("name")
        if name not in pl.SETTINGS:
            return jsonify(ok=False, message=f"unknown setting {name!r}"), 400
        try:
            return jsonify(hmi.set_setting(name, float(body.get("value"))))
        except (TypeError, ValueError) as exc:
            return jsonify(ok=False, message=str(exc)), 400
        except (ConnectionError, OSError) as exc:
            return jsonify(ok=False, message=f"write failed: {exc}"), 502

    @app.post("/api/command")
    def command():
        body = request.get_json(force=True)
        try:
            return jsonify(hmi.command(str(body.get("command")), int(body.get("value", 0))))
        except (TypeError, ValueError) as exc:
            return jsonify(ok=False, message=str(exc)), 400
        except (ConnectionError, OSError) as exc:
            return jsonify(ok=False, message=f"write failed: {exc}"), 502

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Pump station web HMI")
    ap.add_argument("--plc", default=os.getenv("PLC_HOST", "10.10.0.20"),
                    help="PLC address (OpenPLC Modbus server)")
    ap.add_argument("--plc-port", type=int, default=int(os.getenv("PLC_PORT", "502")))
    ap.add_argument("--host", default="0.0.0.0", help="address the web page is served on")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)   # we report comm loss ourselves

    hmi = Hmi(pl.PlcLink(args.plc, args.plc_port))
    threading.Thread(target=hmi.poll_forever, daemon=True).start()
    log.info("HMI on http://%s:%d, polling PLC %s:%d", args.host, args.port, args.plc, args.plc_port)
    create_app(hmi).run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
