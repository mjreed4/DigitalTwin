# Pump station: process simulator and PLC logic

Thread 2 of the digital twin build. The contract is `../design/pump-station-design.md`; this code follows its register map (section 5) exactly.

```
sim-plc/
  simulator/
    process_model.py     pump curves, motor delays, pressure lag (design 3.2, 3.3); no networking
    profiles.py          scripted demand: step, ramp, sine (design 3.4)
    pump_sim.py          Modbus TCP server :5020 + 10 Hz loop + instructor console
    requirements.txt     pymodbus==3.12.1
  plc/
    pump_station.st      Structured Text program for OpenPLC (design 4, 5)
    pump_station_ld/     the same program in ladder logic, as an OpenPLC Editor project
    pump_station_ld.st   the ladder compiled to ST by OpenPLC Editor's generator, ready to upload
    build_ladder.py      regenerates pump_station_ld/ (instructor use only)
    deploy_to_openplc.py loads the program and slave device into an OpenPLC Runtime
  tests/
    test_process_model.py   unit tests against the design doc's pressure table
    closed_loop_test.py     simulator + real OpenPLC runtime, 28 checks
```

## Run the simulator

```bash
cd simulator
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python pump_sim.py                     # listens on 0.0.0.0:5020, console on stdin
.venv/bin/python pump_sim.py --no-console --report 5   # for a container: status line every 5 s
```

pymodbus is pinned to 3.12.1 because 3.13 and later replaced the datastore API the simulator uses to update its registers each step.

### Instructor console

| Command | Effect |
|---|---|
| `status` / `watch [s]` | Pressure, demand, both pumps, heartbeat |
| `demand 75` | Set demand by hand (stops any profile) |
| `profile step` | 50 % for 30 s, then 75 %: forces the lag pump on |
| `profile ramp [period_s]` | 30 → 100 → 30 % over 10 min, repeating |
| `profile sine [period_s]` | Compressed day, 25 % overnight to 85 % peak |
| `profile off` | Hold demand where it is |
| `fault p1` / `clear p1` | Trip a pump / clear the trip (`clear all` too) |
| `noise off` | Remove the ±0.3 psi PT-101 noise for clean trends |
| `quit` | Stop |

Demand and faults can only be changed here. Over Modbus the simulator accepts writes to its two coils and nothing else.

## Load the PLC program

In the OpenPLC web UI (`http://<plc>:8080`, default login openplc / openplc):

1. **Slave Devices → Add new device**: Generic Modbus TCP Device, slave ID 1, IP 10.10.0.10, port 5020. Discrete inputs start 0 size 4, coils start 0 size 2, input registers start 0 size 4, both holding-register blocks size 0.
2. **Programs → Upload program**: `plc/pump_station.st`, then let it compile.
3. **Dashboard → Start PLC**.

`plc/deploy_to_openplc.py` does the same three steps over HTTP (standard library only): `python3 deploy_to_openplc.py --plc http://10.10.0.20:8080 --sim-ip 10.10.0.10`.
The deploy script also accepts a DNS name (including the simulator's Container Apps FQDN) and resolves it to IPv4 before configuring the Modbus device; OpenPLC's TCP master requires a numeric IPv4 address. It retries DNS resolution for up to two minutes to allow Container Apps DNS records to propagate during startup.

In the Azure deployment, the simulator uses external TCP ingress on port 5020 so the OpenPLC app in the separate control Container Apps environment can reach it. "External" here means external to the app's environment: the field environment has an internal load balancer and is not internet-facing. OpenPLC uses the field environment's internal static IP because the generated app FQDN does not resolve across these separate environments. The VNet NSG permits this port from the control subnet only.

The program can also be opened in OpenPLC Editor for the engineering workstation exercise: create a Structured Text POU and paste the PROGRAM block.

## Inert adversary-container placeholder

The deployment includes a manually toggleable placeholder in the control Container Apps environment. Its image is built from `attacker/idle.py` only; the `attack_setpoint.py` file is not copied into the image, and the app has no ingress or PLC connection settings. The process only logs that it is idle.

To start one placeholder replica, manually run the **Deploy Azure Pump Station Digital Twin** GitHub Actions workflow and enable **Start one inert placeholder replica**. Run it again with the option disabled to scale the placeholder back to zero. Push deployments to `main` leave it disabled. These workflow runs deploy the rest of the configured applications as well.

## Ladder logic version

`plc/pump_station_ld/` is the same control program drawn as ladder logic (63 rungs in one LD POU named `PumpStation`). Open it in OpenPLC Editor with **File → Open** and pick the `pump_station_ld` folder. It uses the same variable names and addresses as `pump_station.st`, so the HMI, the simulator and the lab guide's register numbers are unchanged.

To run it, compile it in the editor (**Generate program for OpenPLC Runtime**) and upload the `.st` file the editor writes, or upload the ready-made `plc/pump_station_ld.st`, which is the editor's own code generator output for this ladder:

```bash
python3 deploy_to_openplc.py --plc http://10.10.0.20:8080 --sim-ip 10.10.0.10 --program pump_station_ld.st
```

The ladder passes all 28 checks of `tests/closed_loop_test.py`. It follows the ST program section by section, with these changes of shape that ladder needs (each is noted in a rung comment):

- Comparisons are decoded once into BOOL bits by compare rungs (`ModeAuto`, `SelP2`, `PressBelowSP`, ...), and the logic rungs use contacts on those bits.
- `ActiveLead` (1 or 2) becomes the BOOL `LeadIsP2`. `OldLead` is dropped: during a handover the old lead is always the other pump.
- "Entering Auto" and "a new alarm" are rising-edge contacts, replacing `LastMode` and `PrevAnyAlarm`.
- `IF ... THEN x := TRUE/FALSE` becomes a set or reset coil; number assignments are MOVE, LIMIT, MIN, MAX, ADD and SEL blocks with the rung condition on EN.
- The lag start and stop timers have `ModeAuto` on EN, so like the ST program they only run in Auto.
- `HMI_STATUS` and `HMI_ALARMS` are built by adding each bit's value when its contact is on.

Rungs execute top to bottom. `build_ladder.py` writes the project from a rung list and is only needed to regenerate a clean copy; students edit the ladder in the editor.

## Test

```bash
python3 -m unittest discover -s tests -p "test_*.py"          # model only, no network
python3 tests/closed_loop_test.py --plc 127.0.0.1 --sim-host 127.0.0.1
```

The closed-loop test starts its own simulator on the slave-device port, so stop any other simulator first. It drives the PLC only through holding registers 0–7 and 1024–1034, as the HMI will. It runs startup, the 50 → 75 → 50 % lead/lag cycle, lead failover, reset, manual mode, setting clamps, off mode, the low-pressure alarm and simulator comm loss. It takes about four minutes. All 28 checks pass against OpenPLC Runtime v3 (built from the GitHub master on 2026-10-06).

## Behavior the design doc left open

These are choices made in `pump_station.st`; each is commented there.

- **Clamped settings are written back** to their `%MW` register, so the HMI reads back the value the PLC actually uses.
- **The low-pressure alarm arms 15 s after leaving Off.** Without this, every start-up alarms, because pressure starts at 20 psi and takes about 8 s to pass 110 psi.
- **Failover updates the lead selection.** When the lead trips, the other pump becomes lead and `CMD_LEAD_SELECT` changes to match, so the station does not swap back by itself after Reset.
- **Lead changes are make-before-break.** The old lead keeps running until the new lead reports running, so pressure does not dip during the swap.
- **Switching to Manual is bumpless.** Outside Manual, `CMD_MANUAL_P1/P2` track the pump commands.
- **A failed pump is locked out in every mode** until Reset with its fault cleared. Failed means fault feedback, or no running feedback 5 s after a run command.
- **Reset also acknowledges** (clears bit 15) and is cleared to 0 by the PLC in the same scan.

## Things worth showing students

- **Cold start runs both pumps.** In Auto from rest, pressure is below 120 psi for longer than the 5 s lag delay, so the lag starts too. It then stops after 30 s above 145 psi, as long as demand is below about 63 %.
- **The lag stops below about 63 % demand, not 67 %.** With two pumps, pressure falls below 145 psi at 62.7 % demand (the design doc says about 67 %). Between 63 and 67 % both pumps stay on, which is the hysteresis working as intended: one pump would hold 120–124 psi there.
- **Losing the lead at high demand also raises a low-pressure alarm.** At 75 % demand, pressure dips below 110 psi while the other pump starts. One pump then holds only about 111 psi.
