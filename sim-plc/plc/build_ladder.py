"""Build the ladder logic (LD) version of the pump station program as an OpenPLC Editor project.

    python3 build_ladder.py            # writes pump_station_ld/plc.xml and pump_station_ld/beremiz.xml

The ladder is written out rung by rung in RUNGS below, and this script lays the rungs
out on the editor's grid and writes PLCopen XML (the file format OpenPLC Editor saves).
Students do not need this script: they open the pump_station_ld folder in OpenPLC Editor
and edit the ladder there. It is kept so the instructor can regenerate a clean copy.

The ladder does exactly what pump_station.st does, with the same variable names and
addresses (design doc sections 4 and 5), and passes the same closed-loop test. Where
ladder needs a different shape than ST, the rung comments say so. The main differences:

- Comparisons (pressure below setpoint, mode is Auto, ...) are decoded once into BOOL
  bits by compare rungs, and the logic rungs use contacts on those bits.
- The UINT ActiveLead (1 or 2) becomes the BOOL LeadIsP2, so it can drive contacts.
- "Entering Auto" and "a new alarm" use rising-edge contacts instead of LastMode and
  PrevAnyAlarm.
- IF ... THEN x := TRUE / FALSE becomes a set (S) or reset (R) coil.
- Number assignments are MOVE, LIMIT, MIN, MAX, ADD and SEL blocks whose EN input is the
  rung condition: when EN is off the block does nothing and its output is left alone.

Rung order is execution order: OpenPLC Editor runs coils and output variables top to
bottom, and computes each block when the rung that uses its output runs.
"""

from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "pump_station_ld"


# ============================================================================
#  Rung description
# ============================================================================

class Contact:
    def __init__(self, var, negated=False, edge=None):
        self.var, self.negated, self.edge = var, negated, edge


def NO(var):
    """Normally open contact: passes power when var is TRUE."""
    return Contact(var)


def NC(var):
    """Normally closed contact: passes power when var is FALSE."""
    return Contact(var, negated=True)


def P(var):
    """Rising-edge contact: passes power for one scan when var goes FALSE -> TRUE."""
    return Contact(var, edge="rising")


class Ser(list):
    """Contacts in series (AND)."""
    def __init__(self, *items):
        super().__init__(items)


class Par(list):
    """Branches in parallel (OR)."""
    def __init__(self, *items):
        super().__init__(items)


class Coil:
    """Output coil. kind '' = normal, 'S' = set (latch), 'R' = reset (unlatch).
    pre is an optional contact network in this output's own branch."""
    def __init__(self, var, kind="", pre=None):
        self.var, self.kind, self.pre = var, kind, pre


# Pin names of the standard functions used here
FUNC_PINS = {
    "MOVE": ["IN"], "LIMIT": ["MN", "IN", "MX"], "MIN": ["IN1", "IN2"], "MAX": ["IN1", "IN2"],
    "ADD": ["IN1", "IN2"], "SEL": ["G", "IN0", "IN1"], "UINT_TO_TIME": ["IN"],
    "BOOL_TO_UINT": ["IN"], "EQ": ["IN1", "IN2"], "NE": ["IN1", "IN2"],
    "LT": ["IN1", "IN2"], "GT": ["IN1", "IN2"],
}


class Op:
    """A function block whose EN input is the rung condition; OUT is written to `out`."""
    def __init__(self, func, args, out, pre=None):
        assert len(args) == len(FUNC_PINS[func]), func
        self.func, self.args, self.out, self.pre = func, list(args), out, pre


class Rung:
    """Contact network (None = straight from the left rail) driving one or more outputs."""
    def __init__(self, comment, logic, *outputs):
        self.comment, self.logic, self.outputs = comment, logic, outputs


class Compare:
    """Comparison block (EQ, NE, LT, GT) whose BOOL result drives a coil."""
    def __init__(self, comment, func, a, b, coil):
        self.comment, self.func, self.a, self.b, self.coil = comment, func, a, b, coil


class Timer:
    """TON on-delay timer: IN from a contact network, Q drives a coil.
    en (optional) is a contact network on EN: while EN is off the timer is not run
    at all, which matches a timer call inside an IF in the ST program."""
    def __init__(self, comment, inst, logic, pt, q_coil, en=None):
        self.comment, self.inst, self.logic, self.pt, self.q_coil, self.en = comment, inst, logic, pt, q_coil, en


class Section:
    def __init__(self, text):
        self.text = text


# ============================================================================
#  Variables (same names and addresses as pump_station.st)
# ============================================================================

LOCATED = [
    # name, address, type, documentation
    ("P1_RUNNING", "%IX100.0", "BOOL", "P1 running feedback (simulator DI 0)"),
    ("P2_RUNNING", "%IX100.1", "BOOL", "P2 running feedback (simulator DI 1)"),
    ("P1_FAULT", "%IX100.2", "BOOL", "P1 fault / trip (simulator DI 2)"),
    ("P2_FAULT", "%IX100.3", "BOOL", "P2 fault / trip (simulator DI 3)"),
    ("PT101_DISCH", "%IW100", "UINT", "Discharge pressure PT-101, psi x10"),
    ("PT100_SUCT", "%IW101", "UINT", "Suction pressure PT-100, psi x10"),
    ("DEMAND", "%IW102", "UINT", "Pipeline demand, % x10"),
    ("SIM_HEARTBEAT", "%IW103", "UINT", "Simulator heartbeat, +1 every simulator step"),
    ("P1_RUN_CMD", "%QX100.0", "BOOL", "P1 run command (simulator coil 0)"),
    ("P2_RUN_CMD", "%QX100.1", "BOOL", "P2 run command (simulator coil 1)"),
    ("HMI_DISCH_PRESS", "%QW0", "UINT", "HR 0: discharge pressure, psi x10"),
    ("HMI_SUCT_PRESS", "%QW1", "UINT", "HR 1: suction pressure, psi x10"),
    ("HMI_DEMAND", "%QW2", "UINT", "HR 2: demand, % x10"),
    ("HMI_STATUS", "%QW3", "UINT", "HR 3: status bits (design 5.2)"),
    ("HMI_ALARMS", "%QW4", "UINT", "HR 4: alarm bits (design 5.2)"),
    ("HMI_LEAD_PUMP", "%QW5", "UINT", "HR 5: active lead pump, 1 or 2"),
    ("HMI_MODE_ACTIVE", "%QW6", "UINT", "HR 6: active mode, 0 Off 1 Auto 2 Manual"),
    ("PLC_HEARTBEAT", "%QW7", "UINT", "HR 7: +1 every PLC scan"),
    ("SP_PRESSURE", "%MW0", "UINT", "HR 1024: lag start pressure, psi x10"),
    ("SP_LAG_STOP", "%MW1", "UINT", "HR 1025: lag stop pressure, psi x10"),
    ("SP_LOW_ALARM", "%MW2", "UINT", "HR 1026: low pressure alarm, psi x10"),
    ("SP_HIGH_ALARM", "%MW3", "UINT", "HR 1027: high pressure alarm, psi x10"),
    ("LAG_START_DELAY", "%MW4", "UINT", "HR 1028: lag start delay, s"),
    ("LAG_STOP_DELAY", "%MW5", "UINT", "HR 1029: lag stop delay, s"),
    ("CMD_MODE", "%MW6", "UINT", "HR 1030: mode command, 0 Off 1 Auto 2 Manual"),
    ("CMD_LEAD_SELECT", "%MW7", "UINT", "HR 1031: lead pump select, 1 = P1, 2 = P2"),
    ("CMD_MANUAL_P1", "%MW8", "UINT", "HR 1032: Manual mode P1, 1 run 0 stop"),
    ("CMD_MANUAL_P2", "%MW9", "UINT", "HR 1033: Manual mode P2, 1 run 0 stop"),
    ("CMD_ALARM_RESET", "%MW10", "UINT", "HR 1034: alarm reset, HMI writes 1, PLC clears it"),
]

INTERNAL = [
    # name, type, documentation
    ("LoadDefaults", "BOOL", "SP_PRESSURE is 0: settings were never written, load defaults"),
    ("MinLagStop", "UINT", "SP_PRESSURE + 10 psi, the lowest allowed lag stop pressure"),
    ("ModeAuto", "BOOL", "CMD_MODE = 1"),
    ("ModeManual", "BOOL", "CMD_MODE = 2"),
    ("SelP2", "BOOL", "CMD_LEAD_SELECT = 2 (operator wants P2 as lead)"),
    ("ManP1", "BOOL", "CMD_MANUAL_P1 = 1"),
    ("ManP2", "BOOL", "CMD_MANUAL_P2 = 1"),
    ("ResetReq", "BOOL", "Operator pressed Reset (CMD_ALARM_RESET <> 0)"),
    ("PressBelowSP", "BOOL", "Discharge pressure below the lag start pressure"),
    ("PressAboveLagStop", "BOOL", "Discharge pressure above the lag stop pressure"),
    ("PressBelowLowAlm", "BOOL", "Discharge pressure below the low alarm limit"),
    ("PressAboveHighAlm", "BOOL", "Discharge pressure above the high alarm limit"),
    ("P1StartTmr", "TON", "P1 commanded but not running"),
    ("P2StartTmr", "TON", "P2 commanded but not running"),
    ("P1StartFail", "BOOL", "P1 failed to start within 5 s"),
    ("P2StartFail", "BOOL", "P2 failed to start within 5 s"),
    ("P1Failed", "BOOL", "P1 locked out (fault or failed to start) until Reset"),
    ("P2Failed", "BOOL", "P2 locked out (fault or failed to start) until Reset"),
    ("EnterAuto", "BOOL", "First scan in Auto"),
    ("LeadIsP2", "BOOL", "Active lead pump: FALSE = P1, TRUE = P2 (ActiveLead in the ST program)"),
    ("LagPumpCmd", "BOOL", "The pump that is now lag is commanded to run"),
    ("FailToP2", "BOOL", "Lead P1 has failed and P2 is healthy: make P2 lead"),
    ("FailToP1", "BOOL", "Lead P2 has failed and P1 is healthy: make P1 lead"),
    ("LeadChange", "BOOL", "Apply the operator's new lead selection this scan"),
    ("LagRun", "BOOL", "Auto logic wants the lag pump running"),
    ("Handover", "BOOL", "Lead change in progress: old lead keeps running until the new one runs"),
    ("LeadAvail", "BOOL", "Lead pump is not locked out"),
    ("LagAvail", "BOOL", "Lag pump is not locked out"),
    ("LeadRunning", "BOOL", "Lead pump reports running"),
    ("LagStartPT", "TIME", "Lag start delay as TIME"),
    ("LagStopPT", "TIME", "Lag stop delay as TIME"),
    ("LagStartTmr", "TON", "Pressure low long enough to start the lag pump"),
    ("LagStopTmr", "TON", "Pressure high long enough to stop the lag pump"),
    ("LagStartDone", "BOOL", "LagStartTmr.Q"),
    ("LagStopDone", "BOOL", "LagStopTmr.Q"),
    ("LowArmTmr", "TON", "Start-up bypass for the low pressure alarm"),
    ("LowPressTmr", "TON", "Low pressure alarm delay"),
    ("HighPressTmr", "TON", "High pressure alarm delay"),
    ("CommLossTmr", "TON", "Simulator heartbeat frozen"),
    ("LowArmed", "BOOL", "Low pressure alarm armed (15 s out of Off)"),
    ("LowPressOn", "BOOL", "Pressure low for 5 s"),
    ("HighPressOn", "BOOL", "Pressure high for 5 s"),
    ("SimHbFrozen", "BOOL", "Simulator heartbeat unchanged since the last scan"),
    ("LastSimHb", "UINT", "Simulator heartbeat seen last scan"),
    ("CommLost", "BOOL", "Simulator heartbeat frozen for 3 s"),
    ("CommOk", "BOOL", "Simulator link healthy"),
    ("AlmLow", "BOOL", "Low discharge pressure alarm (latched)"),
    ("AlmHigh", "BOOL", "High discharge pressure alarm (latched)"),
    ("AlmComm", "BOOL", "Simulator comm loss alarm (latched)"),
    ("AlmUnack", "BOOL", "A new alarm has not been acknowledged"),
    ("AnyAlarm", "BOOL", "Any alarm or pump lockout active"),
    ("LagRunning", "BOOL", "Lag pump reports running"),
    ("StatusWord", "UINT", "HMI_STATUS being built"),
    ("AlarmWord", "UINT", "HMI_ALARMS being built"),
    ("HbWrap", "BOOL", "PLC_HEARTBEAT is at 65535 and wraps to 0"),
]


# ============================================================================
#  The ladder
# ============================================================================

RUNGS = [
    Section("PUMP STATION LEAD/LAG CONTROL, ladder logic version of pump_station.st. "
            "Specification: design/pump-station-design.md sections 4 (logic) and 5 (register map). "
            "Pressures are UINT scaled x10: 1200 means 120.0 psi. Rungs run top to bottom once per 100 ms scan."),

    # ---------------------------------------------------------------- 1
    Section("1. FIRST SCAN DEFAULTS (design 4.4). %MW memory is not kept across a runtime restart, "
            "so a zero setpoint means the settings were never written."),
    Compare(None, "EQ", "SP_PRESSURE", "0", "LoadDefaults"),
    Rung("Load the default settings", NO("LoadDefaults"),
         Op("MOVE", ["1200"], "SP_PRESSURE"),
         Op("MOVE", ["1450"], "SP_LAG_STOP"),
         Op("MOVE", ["1100"], "SP_LOW_ALARM"),
         Op("MOVE", ["1600"], "SP_HIGH_ALARM"),
         Op("MOVE", ["5"], "LAG_START_DELAY"),
         Op("MOVE", ["30"], "LAG_STOP_DELAY"),
         Op("MOVE", ["1"], "CMD_MODE"),
         Op("MOVE", ["1"], "CMD_LEAD_SELECT"),
         Op("MOVE", ["0"], "CMD_MANUAL_P1"),
         Op("MOVE", ["0"], "CMD_MANUAL_P2"),
         Op("MOVE", ["0"], "CMD_ALARM_RESET")),

    # ---------------------------------------------------------------- 2
    Section("2. CLAMP HMI SETTINGS to their allowed ranges (design 5.3). The clamped value is written "
            "back, so the HMI reads back what the PLC is using. Then decode the commands into bits."),
    Rung("Setpoints. The lag stop pressure stays at least 10 psi above the lag start pressure.", None,
         Op("LIMIT", ["800", "SP_PRESSURE", "1500"], "SP_PRESSURE"),
         Op("ADD", ["SP_PRESSURE", "100"], "MinLagStop"),
         Op("MAX", ["SP_LAG_STOP", "MinLagStop"], "SP_LAG_STOP"),
         Op("LIMIT", ["1", "LAG_START_DELAY", "60"], "LAG_START_DELAY"),
         Op("LIMIT", ["5", "LAG_STOP_DELAY", "300"], "LAG_STOP_DELAY")),
    Rung("Commands", None,
         Op("MIN", ["CMD_MODE", "2"], "CMD_MODE"),
         Op("LIMIT", ["1", "CMD_LEAD_SELECT", "2"], "CMD_LEAD_SELECT"),
         Op("MIN", ["CMD_MANUAL_P1", "1"], "CMD_MANUAL_P1"),
         Op("MIN", ["CMD_MANUAL_P2", "1"], "CMD_MANUAL_P2")),
    Compare("Mode bits. Off is neither Auto nor Manual.", "EQ", "CMD_MODE", "1", "ModeAuto"),
    Compare(None, "EQ", "CMD_MODE", "2", "ModeManual"),
    Compare("Lead selection and manual commands", "EQ", "CMD_LEAD_SELECT", "2", "SelP2"),
    Compare(None, "EQ", "CMD_MANUAL_P1", "1", "ManP1"),
    Compare(None, "EQ", "CMD_MANUAL_P2", "1", "ManP2"),
    Compare(None, "NE", "CMD_ALARM_RESET", "0", "ResetReq"),
    Compare("Pressure tests used by the lead/lag and alarm rungs",
            "LT", "PT101_DISCH", "SP_PRESSURE", "PressBelowSP"),
    Compare(None, "GT", "PT101_DISCH", "SP_LAG_STOP", "PressAboveLagStop"),
    Compare(None, "LT", "PT101_DISCH", "SP_LOW_ALARM", "PressBelowLowAlm"),
    Compare(None, "GT", "PT101_DISCH", "SP_HIGH_ALARM", "PressAboveHighAlm"),

    # ---------------------------------------------------------------- 3
    Section("3. PUMP HEALTH (design 4.2 step 4 and 4.3). Fault feedback, or no running feedback 5 s after "
            "a run command, locks the pump out until the operator presses Reset and the fault has cleared."),
    Timer("P1 commanded but not running", "P1StartTmr",
          Ser(NO("P1_RUN_CMD"), NC("P1_RUNNING")), "T#5s", "P1StartFail"),
    Timer("P2 commanded but not running", "P2StartTmr",
          Ser(NO("P2_RUN_CMD"), NC("P2_RUNNING")), "T#5s", "P2StartFail"),
    Rung("Lock out a pump that faults or fails to start", Par(NO("P1_FAULT"), NO("P1StartFail")),
         Coil("P1Failed", "S")),
    Rung(None, Par(NO("P2_FAULT"), NO("P2StartFail")), Coil("P2Failed", "S")),
    Rung("Reset releases a lockout once the fault has cleared", Ser(NO("ResetReq"), NC("P1_FAULT")),
         Coil("P1Failed", "R")),
    Rung(None, Ser(NO("ResetReq"), NC("P2_FAULT")), Coil("P2Failed", "R")),

    # ---------------------------------------------------------------- 4
    Section("4. AUTO MODE: LEAD/LAG (design 4.1, 4.2). The lead pump always runs; the lag pump starts when "
            "pressure stays below SP_PRESSURE and stops when it stays above SP_LAG_STOP."),
    Rung("Entering Auto (rising edge of ModeAuto)", P("ModeAuto"), Coil("EnterAuto")),
    Rung("Entering Auto: take the operator's lead choice", NO("EnterAuto"),
         Coil("LeadIsP2", "S", pre=NO("SelP2")),
         Coil("LeadIsP2", "R", pre=NC("SelP2"))),
    Rung("Is the pump that is now lag already commanded to run?",
         Par(Ser(NC("LeadIsP2"), NO("P2_RUN_CMD")), Ser(NO("LeadIsP2"), NO("P1_RUN_CMD"))),
         Coil("LagPumpCmd")),
    Rung("Entering Auto: keep whatever is already running, so the switch causes no bump", NO("EnterAuto"),
         Coil("LagRun", "S", pre=NO("LagPumpCmd")),
         Coil("LagRun", "R", pre=NC("LagPumpCmd")),
         Coil("Handover", "R")),

    Rung("Failover: the lead pump has failed and the other pump is healthy",
         Ser(NO("ModeAuto"), NC("LeadIsP2"), NO("P1Failed"), NC("P2Failed")), Coil("FailToP2")),
    Rung(None, Ser(NO("ModeAuto"), NO("LeadIsP2"), NO("P2Failed"), NC("P1Failed")), Coil("FailToP1")),
    Rung("Promote the healthy pump. The HMI lead selection follows, so the station does not swap back "
         "by itself after Reset.", NO("FailToP2"),
         Coil("LeadIsP2", "S"), Coil("SelP2", "S"), Op("MOVE", ["2"], "CMD_LEAD_SELECT")),
    Rung(None, NO("FailToP1"),
         Coil("LeadIsP2", "R"), Coil("SelP2", "R"), Op("MOVE", ["1"], "CMD_LEAD_SELECT")),
    Rung(None, Par(NO("FailToP2"), NO("FailToP1")), Coil("LagRun", "R"), Coil("Handover", "R")),

    Rung("Operator changed the lead pump: applied only while the lag pump is stopped (design 4.2 step 5) "
         "and the new lead is healthy",
         Ser(NO("ModeAuto"), NC("LagRun"), NC("Handover"),
             Par(Ser(NO("SelP2"), NC("LeadIsP2"), NC("P2Failed")),
                 Ser(NC("SelP2"), NO("LeadIsP2"), NC("P1Failed")))),
         Coil("LeadChange")),
    Rung("Make-before-break: the old lead keeps running (Handover) until the new lead reports running. "
         "The old lead is always the other pump, so the ST program's OldLead is not needed.",
         NO("LeadChange"),
         Coil("LeadIsP2", "S", pre=NO("SelP2")),
         Coil("LeadIsP2", "R", pre=NC("SelP2")),
         Coil("Handover", "S")),

    Rung("Lead and lag pump health and feedback",
         Par(Ser(NC("LeadIsP2"), NC("P1Failed")), Ser(NO("LeadIsP2"), NC("P2Failed"))), Coil("LeadAvail")),
    Rung(None, Par(Ser(NC("LeadIsP2"), NC("P2Failed")), Ser(NO("LeadIsP2"), NC("P1Failed"))), Coil("LagAvail")),
    Rung(None, Par(Ser(NC("LeadIsP2"), NO("P1_RUNNING")), Ser(NO("LeadIsP2"), NO("P2_RUNNING"))),
         Coil("LeadRunning")),
    Rung("Handover ends when the new lead runs, or cannot run",
         Ser(NO("ModeAuto"), NO("Handover"), Par(NO("LeadRunning"), NC("LeadAvail"))),
         Coil("Handover", "R")),

    Rung("Lag delays as TIME (OpenPLC converts an integer to TIME as seconds)", None,
         Op("UINT_TO_TIME", ["LAG_START_DELAY"], "LagStartPT"),
         Op("UINT_TO_TIME", ["LAG_STOP_DELAY"], "LagStopPT")),
    Timer("Lag start: pressure below SP_PRESSURE for LAG_START_DELAY (step 2). EN = ModeAuto: the timer "
          "only runs in Auto.", "LagStartTmr",
          Ser(NO("PressBelowSP"), NC("LagRun"), NO("LagAvail"), NC("Handover")),
          "LagStartPT", "LagStartDone", en=NO("ModeAuto")),
    Rung(None, Ser(NO("ModeAuto"), NO("LagStartDone")), Coil("LagRun", "S")),
    Timer("Lag stop: pressure above SP_LAG_STOP for LAG_STOP_DELAY (step 3). The gap between the two "
          "pressures is the hysteresis that stops short-cycling.", "LagStopTmr",
          Ser(NO("LagRun"), NO("PressAboveLagStop")),
          "LagStopPT", "LagStopDone", en=NO("ModeAuto")),
    Rung(None, Ser(NO("ModeAuto"), Par(NO("LagStopDone"), NC("LagAvail"))), Coil("LagRun", "R")),
    Rung("Manual and Off: no lag request, no handover", NC("ModeAuto"),
         Coil("LagRun", "R"), Coil("Handover", "R")),

    Section("PUMP COMMANDS. Auto: the lead runs, the lag runs when called for, and the old lead runs during "
            "a handover. Manual: the operator's command. Off: stopped. A locked-out pump is never commanded."),
    Rung("P1 run command",
         Ser(NC("P1Failed"),
             Par(Ser(NO("ModeAuto"), Par(NC("LeadIsP2"), NO("LagRun"), NO("Handover"))),
                 Ser(NO("ModeManual"), NO("ManP1")))),
         Coil("P1_RUN_CMD")),
    Rung("P2 run command",
         Ser(NC("P2Failed"),
             Par(Ser(NO("ModeAuto"), Par(NO("LeadIsP2"), NO("LagRun"), NO("Handover"))),
                 Ser(NO("ModeManual"), NO("ManP2")))),
         Coil("P2_RUN_CMD")),
    Rung("Outside Manual the manual commands track the pumps, so switching to Manual is bumpless",
         NC("ModeManual"),
         Op("BOOL_TO_UINT", ["P1_RUN_CMD"], "CMD_MANUAL_P1"),
         Op("BOOL_TO_UINT", ["P2_RUN_CMD"], "CMD_MANUAL_P2")),

    # ---------------------------------------------------------------- 5
    Section("5. ALARMS (design 4.3). Each alarm latches until the operator presses Reset AND its condition "
            "has cleared. Reset also acknowledges."),
    Timer("Low pressure is armed 15 s after leaving Off, so a normal start-up does not alarm", "LowArmTmr",
          Par(NO("ModeAuto"), NO("ModeManual")), "T#15s", "LowArmed"),
    Timer("Low pressure for 5 s", "LowPressTmr", Ser(NO("LowArmed"), NO("PressBelowLowAlm")),
          "T#5s", "LowPressOn"),
    Timer("High pressure for 5 s", "HighPressTmr", NO("PressAboveHighAlm"), "T#5s", "HighPressOn"),
    Compare("Simulator comm loss: heartbeat has not changed for 3 s", "EQ", "SIM_HEARTBEAT", "LastSimHb",
            "SimHbFrozen"),
    Timer(None, "CommLossTmr", NO("SimHbFrozen"), "T#3s", "CommLost"),
    Rung(None, None, Op("MOVE", ["SIM_HEARTBEAT"], "LastSimHb")),
    Rung(None, NC("CommLost"), Coil("CommOk")),
    Rung("Latch the alarms", NO("LowPressOn"), Coil("AlmLow", "S")),
    Rung(None, NO("HighPressOn"), Coil("AlmHigh", "S")),
    Rung(None, NO("CommLost"), Coil("AlmComm", "S")),
    Rung("Reset: clear each alarm whose condition has gone, acknowledge, and clear the reset command",
         NO("ResetReq"),
         Coil("AlmLow", "R", pre=NC("LowPressOn")),
         Coil("AlmHigh", "R", pre=NC("HighPressOn")),
         Coil("AlmComm", "R", pre=NC("CommLost")),
         Coil("AlmUnack", "R"),
         Op("MOVE", ["0"], "CMD_ALARM_RESET")),
    Rung("A newly raised alarm is unacknowledged until the next Reset",
         Par(NO("AlmLow"), NO("AlmHigh"), NO("P1Failed"), NO("P2Failed"), NO("AlmComm")),
         Coil("AnyAlarm")),
    Rung(None, P("AnyAlarm"), Coil("AlmUnack", "S")),

    # ---------------------------------------------------------------- 6
    Section("6. STATUS FOR THE HMI, holding registers 0..7 (design 5.2)"),
    Rung("Lag pump running feedback",
         Par(Ser(NC("LeadIsP2"), NO("P2_RUNNING")), Ser(NO("LeadIsP2"), NO("P1_RUNNING"))),
         Coil("LagRunning")),
    Rung("Values", None,
         Op("MOVE", ["PT101_DISCH"], "HMI_DISCH_PRESS"),
         Op("MOVE", ["PT100_SUCT"], "HMI_SUCT_PRESS"),
         Op("MOVE", ["DEMAND"], "HMI_DEMAND"),
         Op("SEL", ["LeadIsP2", "1", "2"], "HMI_LEAD_PUMP"),
         Op("MOVE", ["CMD_MODE"], "HMI_MODE_ACTIVE")),
    Rung("Status word: add each bit's value when the bit is on", None,
         Op("MOVE", ["0"], "StatusWord"),
         Op("ADD", ["StatusWord", "1"], "StatusWord", pre=NO("P1_RUN_CMD")),
         Op("ADD", ["StatusWord", "2"], "StatusWord", pre=NO("P1_RUNNING")),
         Op("ADD", ["StatusWord", "4"], "StatusWord", pre=NO("P2_RUN_CMD")),
         Op("ADD", ["StatusWord", "8"], "StatusWord", pre=NO("P2_RUNNING")),
         Op("ADD", ["StatusWord", "16"], "StatusWord", pre=NO("LagRunning")),
         Op("ADD", ["StatusWord", "32"], "StatusWord", pre=NO("CommOk")),
         Op("MOVE", ["StatusWord"], "HMI_STATUS")),
    Rung("Alarm word, the same way", None,
         Op("MOVE", ["0"], "AlarmWord"),
         Op("ADD", ["AlarmWord", "1"], "AlarmWord", pre=NO("AlmLow")),
         Op("ADD", ["AlarmWord", "2"], "AlarmWord", pre=NO("AlmHigh")),
         Op("ADD", ["AlarmWord", "4"], "AlarmWord", pre=NO("P1Failed")),
         Op("ADD", ["AlarmWord", "8"], "AlarmWord", pre=NO("P2Failed")),
         Op("ADD", ["AlarmWord", "16"], "AlarmWord", pre=NO("AlmComm")),
         Op("ADD", ["AlarmWord", "32768"], "AlarmWord", pre=NO("AlmUnack")),
         Op("MOVE", ["AlarmWord"], "HMI_ALARMS")),
    Compare("PLC heartbeat: +1 every scan, 65535 wraps to 0", "EQ", "PLC_HEARTBEAT", "65535", "HbWrap"),
    Rung(None, None,
         Op("ADD", ["PLC_HEARTBEAT", "1"], "PLC_HEARTBEAT", pre=NC("HbWrap")),
         Op("MOVE", ["0"], "PLC_HEARTBEAT", pre=NO("HbWrap"))),
]


# ============================================================================
#  Layout and PLCopen XML
# ============================================================================

RAIL_X = 40          # left power rail
RAIL_OUT = RAIL_X + 10
CELL = 140           # width of one contact cell
ROW = 60             # vertical pitch between parallel branches
GAP = 30             # space between rungs


def comment_h(text, w):
    """Height of a comment box: the editor wraps its text at about 10 px per character."""
    per_line = (w - 20) // 10
    return 20 * (-(-len(text) // per_line) + 1)


def snap(v):
    return (v + 9) // 10 * 10


def text_w(s, minimum=40):
    return snap(max(len(s) * 8 + 20, minimum))


def block_size(func, in_names, out_names, inst=None):
    lines = max(len(in_names), len(out_names))
    longest = max(len(n) for n in in_names) + max(len(n) for n in out_names)
    w = snap(max(len(func) * 9 + 30, longest * 8 + 30, len(inst or "") * 8))
    return w, 20 + 40 * lines


class Src:
    """A point power or data can be taken from: an element output, or the left rail."""
    def __init__(self, lid, param, x, y):
        self.lid, self.param, self.x, self.y = lid, param, x, y


RAIL = "rail"


class Body:
    def __init__(self):
        self.xml = []
        self.next_id = 1
        self.rail_ids = {}        # left rail segment id by connection y
        self.right_conns = []     # (y, src)

    def new_id(self):
        self.next_id += 1
        return self.next_id - 1

    # ---- wiring --------------------------------------------------------
    def conns(self, tx, ty, sources, bus_x=None):
        """<connection> elements from the input point (tx, ty) back to each source."""
        out = []
        for s in sources:
            if s is RAIL:
                if ty not in self.rail_ids:
                    self.rail_ids[ty] = self.new_id()
                out.append((self.rail_ids[ty], None, [(tx, ty), (RAIL_OUT, ty)]))
                continue
            if s.y == ty:
                pts = [(tx, ty), (s.x, s.y)]
            else:
                bx = bus_x if bus_x is not None else tx - 20
                pts = [(tx, ty), (bx, ty), (bx, s.y), (s.x, s.y)]
            out.append((s.lid, s.param, pts))
        return out

    @staticmethod
    def conn_xml(conns, indent):
        lines = []
        for lid, param, pts in conns:
            fp = f' formalParameter="{param}"' if param else ""
            lines.append(f'{indent}<connection refLocalId="{lid}"{fp}>')
            for x, y in pts:
                lines.append(f'{indent}  <position x="{x}" y="{y}"/>')
            lines.append(f'{indent}</connection>')
        return "\n".join(lines)

    # ---- elements ------------------------------------------------------
    def contact(self, c, x, y, sources, bus_x=None):
        lid = self.new_id()
        attrs = f'negated="{str(c.negated).lower()}"'
        if c.edge:
            attrs += f' edge="{c.edge}"'
        self.xml.append(f'''<contact localId="{lid}" height="20" width="30" {attrs}>
  <position x="{x}" y="{y - 10}"/>
  <connectionPointIn>
    <relPosition x="0" y="10"/>
{self.conn_xml(self.conns(x, y, sources, bus_x), "    ")}
  </connectionPointIn>
  <connectionPointOut>
    <relPosition x="30" y="10"/>
  </connectionPointOut>
  <variable>{escape(c.var)}</variable>
</contact>''')
        return Src(lid, None, x + 30, y)

    def coil(self, cl, x, y, sources, bus_x=None):
        lid = self.new_id()
        storage = {"S": ' storage="set"', "R": ' storage="reset"'}.get(cl.kind, "")
        self.xml.append(f'''<coil localId="{lid}" height="20" width="30" negated="false"{storage}>
  <position x="{x}" y="{y - 10}"/>
  <connectionPointIn>
    <relPosition x="0" y="10"/>
{self.conn_xml(self.conns(x, y, sources, bus_x), "    ")}
  </connectionPointIn>
  <connectionPointOut>
    <relPosition x="30" y="10"/>
  </connectionPointOut>
  <variable>{escape(cl.var)}</variable>
</coil>''')
        self.right_conns.append((y, Src(lid, None, x + 30, y)))

    def in_var(self, expr, right_x, y):
        lid = self.new_id()
        w = text_w(expr)
        self.xml.append(f'''<inVariable localId="{lid}" height="40" width="{w}" negated="false">
  <position x="{right_x - w}" y="{y - 20}"/>
  <connectionPointOut>
    <relPosition x="{w}" y="20"/>
  </connectionPointOut>
  <expression>{escape(expr)}</expression>
</inVariable>''')
        return Src(lid, None, right_x, y)

    def out_var(self, expr, x, y, src):
        lid = self.new_id()
        w = text_w(expr)
        self.xml.append(f'''<outVariable localId="{lid}" height="40" width="{w}" negated="false">
  <position x="{x}" y="{y - 20}"/>
  <connectionPointIn>
    <relPosition x="0" y="20"/>
{self.conn_xml(self.conns(x, y, [src]), "    ")}
  </connectionPointIn>
  <expression>{escape(expr)}</expression>
</outVariable>''')

    def block(self, typ, x, top, ins, outs, inst=None):
        """ins: list of (pin, sources, bus_x); outs: list of pin names. Returns {pin: Src}."""
        lid = self.new_id()
        w, h = block_size(typ, [p for p, _, _ in ins], outs, inst)
        iname = f' instanceName="{inst}"' if inst else ""
        parts = [f'<block localId="{lid}" typeName="{typ}"{iname} height="{h}" width="{w}">',
                 f'  <position x="{x}" y="{top}"/>', '  <inputVariables>']
        for i, (pin, sources, bus_x) in enumerate(ins):
            py = top + 40 + 40 * i
            parts.append(f'    <variable formalParameter="{pin}">\n      <connectionPointIn>\n'
                         f'        <relPosition x="0" y="{40 + 40 * i}"/>')
            if sources:
                parts.append(self.conn_xml(self.conns(x, py, sources, bus_x), "        "))
            parts.append('      </connectionPointIn>\n    </variable>')
        parts.append('  </inputVariables>\n  <inOutVariables/>\n  <outputVariables>')
        result = {}
        for i, pin in enumerate(outs):
            parts.append(f'    <variable formalParameter="{pin}">\n      <connectionPointOut>\n'
                         f'        <relPosition x="{w}" y="{40 + 40 * i}"/>\n'
                         f'      </connectionPointOut>\n    </variable>')
            result[pin] = Src(lid, pin, x + w, top + 40 + 40 * i)
        parts.append('  </outputVariables>\n</block>')
        self.xml.append("\n".join(parts))
        return result, w, h

    def comment(self, text, x, y, w, h):
        lid = self.new_id()
        self.xml.append(f'''<comment localId="{lid}" height="{h}" width="{w}">
  <position x="{x}" y="{y}"/>
  <content>
    <xhtml:p><![CDATA[{text}]]></xhtml:p>
  </content>
</comment>''')


# ---- contact networks ---------------------------------------------------

def net_size(node):
    """(width, rows) of a contact network."""
    if node is None:
        return 0, 1
    if isinstance(node, Contact):
        return CELL, 1
    if isinstance(node, Ser):
        sizes = [net_size(n) for n in node]
        return sum(w for w, _ in sizes), max(r for _, r in sizes)
    if isinstance(node, Par):
        sizes = [net_size(n) for n in node]
        return max(w for w, _ in sizes), sum(r for _, r in sizes)
    raise TypeError(node)


def place_net(body, node, x, y, sources):
    """Place a network with its first contact cell starting at x on line y. Returns the sinks."""
    if node is None:
        return sources
    if isinstance(node, Contact):
        return [body.contact(node, x + 60, y, sources, bus_x=x + 30)]
    if isinstance(node, Ser):
        for n in node:
            sources = place_net(body, n, x, y, sources)
            x += net_size(n)[0]
        return sources
    if isinstance(node, Par):
        sinks, row_y = [], y
        for n in node:
            sinks += place_net(body, n, x, row_y, sources)
            row_y += net_size(n)[1] * ROW
        return sinks
    raise TypeError(node)


# ---- rungs --------------------------------------------------------------

def op_size(op):
    pins = ["EN"] + FUNC_PINS[op.func]
    bw, bh = block_size(op.func, pins, ["ENO", "OUT"])
    in_w = max(text_w(a) for a in op.args)
    return bw, bh, in_w


def output_height(o):
    if isinstance(o, Coil):
        return ROW
    return op_size(o)[1] + 10


def rung_layout_width(r):
    """Width needed from the left rail to the right rail."""
    if isinstance(r, Section):
        return 0
    if isinstance(r, Compare):
        bw, _ = block_size(r.func, ["IN1", "IN2"], ["OUT"])
        return 30 + max(text_w(r.a), text_w(r.b)) + 20 + bw + 60 + 30 + 40
    if isinstance(r, Timer):
        bw, _ = block_size("TON", ["EN", "IN", "PT"], ["ENO", "Q", "ET"], r.inst)
        nw = max(net_size(r.logic)[0], net_size(r.en)[0] if r.en else 0)
        return nw + 40 + text_w(r.pt) + 20 + bw + 60 + 30 + 40
    nw = net_size(r.logic)[0]
    best = 0
    for o in r.outputs:
        pw = net_size(o.pre)[0] if o.pre else 0
        if isinstance(o, Coil):
            best = max(best, pw + 40 + 30 + 80)
        else:
            bw, _, in_w = op_size(o)
            best = max(best, pw + 40 + in_w + 20 + bw + 20 + text_w(o.out) + 40)
    return nw + best


def place_rung(body, r, y, coil_x, right_x):
    """Place one rung whose first line is y. Returns the y below it."""
    if isinstance(r, Section):
        w = right_x - RAIL_X - 30
        h = comment_h(r.text, w)
        body.comment(r.text, RAIL_X + 20, y, w, h)
        return y + h + GAP
    if r.comment:
        w = right_x - RAIL_X - 30
        h = comment_h(r.comment, w)
        body.comment(r.comment, RAIL_X + 20, y, w, h)
        y += h + 20
    y += 10  # room for the labels above the first contacts
    # Blocks reach 40 above their first pin, and a timer's instance name 20 more
    if isinstance(r, Timer):
        y += 50
    elif isinstance(r, Compare) or isinstance(r.outputs[0], Op):
        y += 30

    if isinstance(r, Compare):
        in_w = max(text_w(r.a), text_w(r.b))
        bx = snap(RAIL_OUT + 30 + in_w + 20)
        a = body.in_var(r.a, bx - 20, y)
        b = body.in_var(r.b, bx - 20, y + 40)
        outs, bw, bh = body.block(r.func, bx, y - 40, [("IN1", [a], None), ("IN2", [b], None)], ["OUT"])
        body.coil(Coil(r.coil), coil_x, y, [outs["OUT"]])
        return y - 40 + bh + GAP

    if isinstance(r, Timer):
        rows = 0
        ins = []
        nw = max(net_size(r.logic)[0], net_size(r.en)[0] if r.en else 0)
        pt_w = text_w(r.pt)
        bx = snap(RAIL_OUT + nw + 40 + pt_w + 20)
        line = y
        if r.en is not None:
            en_sinks = place_net(body, r.en, RAIL_OUT, line, [RAIL])
            ins.append(("EN", en_sinks, RAIL_OUT + nw + 20))
            line += 40
            rows += 40
        in_sinks = place_net(body, r.logic, RAIL_OUT, line, [RAIL])
        ins.append(("IN", in_sinks, RAIL_OUT + nw + 20))
        pt = body.in_var(r.pt, bx - 20, line + 40)
        ins.append(("PT", [pt], None))
        outs_names = (["ENO"] if r.en is not None else []) + ["Q", "ET"]
        outs, bw, bh = body.block("TON", bx, y - 40, ins, outs_names, inst=r.inst)
        body.coil(Coil(r.q_coil), coil_x, line, [outs["Q"]], bus_x=bx + bw + 20)
        if r.en is not None:
            body.right_conns.append((y, outs["ENO"]))
        net_rows = net_size(r.logic)[1] * ROW + rows
        return y + max(bh - 40, net_rows) + GAP

    # Contact network driving outputs
    sinks = place_net(body, r.logic, RAIL_OUT, y, [RAIL])
    nw = net_size(r.logic)[0]
    out_x = RAIL_OUT + nw
    line = y
    bottom = y + net_size(r.logic)[1] * ROW
    for o in r.outputs:
        if o.pre is not None:
            o_sinks = place_net(body, o.pre, out_x, line, sinks)
            pw = net_size(o.pre)[0]
            bus = out_x + pw + 20
        else:
            o_sinks, pw = sinks, 0
            bus = out_x + 30 if out_x > RAIL_OUT else None
        if isinstance(o, Coil):
            body.coil(o, coil_x, line, o_sinks, bus_x=bus)
            line += ROW
        else:
            bw, bh, in_w = op_size(o)
            bx = snap(out_x + pw + 40 + in_w + 20)
            top = line - 40
            ins = [("EN", o_sinks, bus)]
            for i, (pin, arg) in enumerate(zip(FUNC_PINS[o.func], o.args)):
                ins.append((pin, [body.in_var(arg, bx - 20, line + 40 * (i + 1))], None))
            outs, bw, bh = body.block(o.func, bx, top, ins, ["ENO", "OUT"])
            body.out_var(o.out, bx + bw + 20, line + 40, outs["OUT"])
            body.right_conns.append((line, outs["ENO"]))
            line = top + bh + 10 + 40
        bottom = max(bottom, line)
    return bottom - 20 + GAP


def rail_segments(points, x, left):
    """One power rail element per connection point.

    The editor spaces a rail's connection points evenly along it, so a single tall rail
    cannot hold the uneven rung spacing. Each point gets its own rail element instead,
    tall enough to reach the next one so the rail still reads as one line."""
    tag = "leftPowerRail" if left else "rightPowerRail"
    ys = sorted(points)
    out = []
    for i, py in enumerate(ys):
        lid, inner = points[py]
        top = py - 20
        bottom = ys[i + 1] - 20 if i + 1 < len(ys) else py + 20
        out.append(f'<{tag} localId="{lid}" height="{bottom - top}" width="10">\n'
                   f'  <position x="{x}" y="{top}"/>\n{inner}\n</{tag}>')
    return out


def build_body():
    body = Body()
    right_x = snap(max(rung_layout_width(r) for r in RUNGS) + RAIL_OUT + 80)
    coil_x = right_x - 110   # room for long coil names, which are centered over the coil
    y = 20
    for r in RUNGS:
        y = place_rung(body, r, y, coil_x, right_x)

    left = {py: (lid, f'  <connectionPointOut formalParameter="">\n    <relPosition x="10" y="20"/>\n'
                      f'  </connectionPointOut>')
            for py, lid in body.rail_ids.items()}
    right = {}
    for py, src in body.right_conns:
        assert py not in right, py
        pts = [(right_x, py), (src.x, src.y)] if src.y == py else \
              [(right_x, py), (right_x - 20, py), (right_x - 20, src.y), (src.x, src.y)]
        right[py] = (body.new_id(), f'  <connectionPointIn>\n    <relPosition x="0" y="20"/>\n'
                                    f'{Body.conn_xml([(src.lid, src.param, pts)], "    ")}\n  </connectionPointIn>')
    return "\n".join(rail_segments(left, RAIL_X, True) + rail_segments(right, right_x, False) + body.xml)


def var_xml(name, typ, doc, address=None):
    addr = f' address="{address}"' if address else ""
    t = f'<derived name="{typ}"/>' if typ == "TON" else f"<{typ}/>"
    return (f'<variable name="{name}"{addr}>\n  <type>\n    {t}\n  </type>\n'
            f'  <documentation>\n    <xhtml:p><![CDATA[{doc}]]></xhtml:p>\n  </documentation>\n</variable>')


def indent(text, n):
    pad = " " * n
    return "\n".join(pad + line if line else line for line in text.splitlines())


def build_project():
    stamp = "2026-10-06T00:00:00"
    variables = [var_xml(n, t, d, a) for n, a, t, d in LOCATED] + [var_xml(n, t, d) for n, t, d in INTERNAL]
    return f'''<?xml version='1.0' encoding='utf-8'?>
<project xmlns:ns1="http://www.plcopen.org/xml/tc6.xsd" xmlns:xhtml="http://www.w3.org/1999/xhtml" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns="http://www.plcopen.org/xml/tc6_0201">
  <fileHeader companyName="Digital twin lab" productName="Pump station" productVersion="1" creationDateTime="{stamp}"/>
  <contentHeader name="Pump station (ladder)" modificationDateTime="{stamp}">
    <coordinateInfo>
      <fbd>
        <scaling x="10" y="10"/>
      </fbd>
      <ld>
        <scaling x="10" y="10"/>
      </ld>
      <sfc>
        <scaling x="10" y="10"/>
      </sfc>
    </coordinateInfo>
  </contentHeader>
  <types>
    <dataTypes/>
    <pous>
      <pou name="PumpStation" pouType="program">
        <interface>
          <localVars>
{indent(chr(10).join(variables), 12)}
          </localVars>
        </interface>
        <body>
          <LD>
{indent(build_body(), 12)}
          </LD>
        </body>
        <documentation>
          <xhtml:p><![CDATA[Lead/lag pump station control in ladder logic. Same behavior, variables and addresses as pump_station.st.]]></xhtml:p>
        </documentation>
      </pou>
    </pous>
  </types>
  <instances>
    <configurations>
      <configuration name="Config0">
        <resource name="Res0">
          <task name="MainTask" priority="0" interval="T#100ms">
            <pouInstance name="Inst0" typeName="PumpStation"/>
          </task>
        </resource>
      </configuration>
    </configurations>
  </instances>
</project>
'''


BEREMIZ_XML = '''<?xml version='1.0' encoding='utf-8'?>
<BeremizRoot xmlns:xsd="http://www.w3.org/2001/XMLSchema" URI_location="LOCAL://">
  <TargetType/>
</BeremizRoot>
'''


def main():
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "plc.xml").write_text(build_project(), encoding="utf-8")
    (OUT_DIR / "beremiz.xml").write_text(BEREMIZ_XML, encoding="utf-8")
    print(f"wrote {OUT_DIR / 'plc.xml'}")


if __name__ == "__main__":
    main()
