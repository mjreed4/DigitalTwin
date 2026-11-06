# Pump station: operator HMI

Thread 3 of the digital twin build. A small Python web HMI (Flask + pymodbus) that supervises the PLC over Modbus TCP. The contract is `../design/pump-station-design.md`, sections 5.2 to 5.4. The HMI reads holding registers 0–7 and 1024–1034 once a second and writes only to 1024–1034. It never talks to the simulator.

```
hmi/
  plc_link.py      the register map and the Modbus client: everything the HMI knows about the PLC
  app.py           poller thread, trend history, event log, JSON API, web server on :8000
  templates/index.html, static/hmi.js, static/hmi.css    the operator screen (no external files: the lab net is offline)
  tests/hmi_closed_loop_test.py   HMI API -> real OpenPLC -> simulator, 32 checks
  requirements.txt  flask, pymodbus==3.12.1 (same pin as the simulator)
  screenshot.png   the screen during a P1 trip at 75 % demand, raw registers open
```

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py --plc 10.10.0.20          # then open http://<hmi>:8000
.venv/bin/python app.py --plc 127.0.0.1 --port 8000   # everything on one machine
```

Options: `--plc-port` (default 502), `--host` / `--port` for the web page (default 0.0.0.0:8000). The PLC must be running `pump_station.st` (see `../sim-plc/README.md`).

## What the screen shows

| Area | Contents | Registers |
|---|---|---|
| Header | PLC link, PLC scanning (heartbeat HR 7 changed in the last 3 s), field I/O OK (status bit 5), active mode | HR 3, 6, 7 |
| Overview | P&ID-style view: PT-100 suction, both pumps with state and lead/lag role, PT-101 discharge with setpoints, demand | HR 0–6 |
| Alarms | The five PLC alarms, the unacknowledged flag, Reset | HR 4, writes HR 1034 |
| Control | Mode Off/Auto/Manual, lead pump, manual start/stop (enabled only in Manual) | writes HR 1030–1033 |
| Settings | Each setting as the PLC holds it, with an entry box | reads and writes HR 1024–1029 |
| Trend | Discharge pressure, setpoint, lag stop, demand, and P1/P2 run bars; 2, 5 or 15 minutes | history kept by the HMI |
| Event log | Alarms raised and cleared, acknowledgements, mode and lead changes, every operator write | kept by the HMI, last 200 |
| Raw Modbus registers | All 19 registers in decimal, hex and binary (collapsed by default) | HR 0–7, 1024–1034 |

Buttons highlight what the PLC reports, not what was clicked, so if the PLC refuses or overrides a command the screen shows that.

## Behavior worth knowing

- **Clamped settings are reported.** After a setting write the HMI waits half a second, reads the register back and compares. If the PLC clamped it, a yellow notice says so ("asked for 200 psi, PLC clamped it to 150 psi") and the event log records it. The HMI does no range checking of its own beyond what fits in a register; the PLC is the authority, which is what lets students see clamping happen.
- **Alarms in the list are the PLC's latched alarms.** One stays in the list after its condition has gone, until Reset. The red discharge box is separate: it follows the live pressure against the alarm limits.
- **The HMI can lose two different links.** "PLC LINK LOST" means the HMI can't reach the PLC; the screen freezes with a yellow banner. "FIELD I/O LOST" means the PLC can't reach the simulator; the HMI still works and the PLC raises its comm-loss alarm.
- **One shared state for all browsers.** The trend and event log live in the HMI server, so every browser on the lab network sees the same history. Restarting the HMI clears them; alarms already latched in the PLC are logged when the HMI reconnects.
- **Colours follow the high-performance HMI idea (ISA-101).** The screen is grey; a running pump is a dark fill and a stopped one is light, with the state also written out. Red and amber are kept for alarms, so they stand out.

## Things worth showing students

- **The HMI is only one Modbus client.** Anyone on the lab network can write the same registers with mbpoll or the pymodbus REPL, and the PLC will obey; the HMI buttons will then move to match. Modbus has no authentication, which is why the lab network is isolated.
- **Open the raw register panel while clicking a button** to see one register change, and watch HR 3 and HR 4 as bit fields while a pump trips.
- **Stop the PLC from its web page** and the HMI shows "PLC LINK LOST" within a second or two, because OpenPLC's Modbus server stops with the runtime. Start it again and the HMI reconnects by itself and logs the alarms the PLC latched meanwhile.

## Test

```bash
python3 tests/hmi_closed_loop_test.py --plc 127.0.0.1 --sim-host 127.0.0.1
```

Same prerequisites as `../sim-plc/tests/closed_loop_test.py`: an OpenPLC Runtime running `pump_station.st` with its slave device pointed at this machine, and no other simulator on port 5020. The test starts its own simulator and drives everything through the HMI's HTTP API, as the browser does. It covers start-up, setting writes and clamps, the 50 → 75 % lag start, lead failover with alarms and Reset, Manual mode, simulator comm loss, and an unreachable PLC. It takes about two and a half minutes. All 32 checks pass against OpenPLC Runtime v3 (GitHub master, built 2026-10-06), and the page was checked in Chromium at desktop and phone widths with no script errors.

## Not done here

- Login and HTTPS. The web page has no authentication; on the isolated lab network that matches the Modbus link, and it is a talking point rather than an oversight.
- A production web server. `app.py` uses Flask's built-in server, which is fine for a classroom.
- Packaging (Docker Compose) and the lab guide belong to thread 4.
