(*
  Pump station lead/lag control: OpenPLC Structured Text program.

  Specification: design/pump-station-design.md, section 4 (logic) and 5 (register map).

  Field I/O (%IX100, %QX100, %IW100) comes from the process simulator, which
  OpenPLC polls as a Modbus "slave device" (Generic TCP, 10.10.0.10:5020).
  The HMI reads the status block %QW0..%QW7 (holding registers 0..7) and
  writes settings to %MW0..%MW10 (holding registers 1024..1034).

  Pressures are UINT scaled x10: 1200 means 120.0 psi.

  Scan order, the same as every PLC: inputs are copied in, this program runs
  top to bottom once, outputs are copied out. Then it starts again.
*)
PROGRAM PumpStation
  VAR
    (* ---- Field inputs from the simulator (design 5.1) ---- *)
    P1_RUNNING    AT %IX100.0 : BOOL;
    P2_RUNNING    AT %IX100.1 : BOOL;
    P1_FAULT      AT %IX100.2 : BOOL;
    P2_FAULT      AT %IX100.3 : BOOL;
    PT101_DISCH   AT %IW100 : UINT;   (* discharge pressure, psi x10 *)
    PT100_SUCT    AT %IW101 : UINT;   (* suction pressure, psi x10 *)
    DEMAND        AT %IW102 : UINT;   (* pipeline demand, % x10 *)
    SIM_HEARTBEAT AT %IW103 : UINT;   (* +1 every simulator step *)

    (* ---- Field outputs to the simulator ---- *)
    P1_RUN_CMD AT %QX100.0 : BOOL;
    P2_RUN_CMD AT %QX100.1 : BOOL;

    (* ---- Status for the HMI: holding registers 0..7 (design 5.2) ---- *)
    HMI_DISCH_PRESS AT %QW0 : UINT;
    HMI_SUCT_PRESS  AT %QW1 : UINT;
    HMI_DEMAND      AT %QW2 : UINT;
    HMI_STATUS      AT %QW3 : UINT;
    HMI_ALARMS      AT %QW4 : UINT;
    HMI_LEAD_PUMP   AT %QW5 : UINT;
    HMI_MODE_ACTIVE AT %QW6 : UINT;
    PLC_HEARTBEAT   AT %QW7 : UINT;

    (* ---- Settings and commands from the HMI: holding registers 1024..1034 (design 5.3) ---- *)
    SP_PRESSURE     AT %MW0 : UINT;   (* lag start pressure, psi x10 *)
    SP_LAG_STOP     AT %MW1 : UINT;   (* lag stop pressure, psi x10 *)
    SP_LOW_ALARM    AT %MW2 : UINT;
    SP_HIGH_ALARM   AT %MW3 : UINT;
    LAG_START_DELAY AT %MW4 : UINT;   (* seconds *)
    LAG_STOP_DELAY  AT %MW5 : UINT;   (* seconds *)
    CMD_MODE        AT %MW6 : UINT;   (* 0 Off, 1 Auto, 2 Manual *)
    CMD_LEAD_SELECT AT %MW7 : UINT;   (* 1 = P1, 2 = P2 *)
    CMD_MANUAL_P1   AT %MW8 : UINT;   (* Manual mode: 1 run, 0 stop *)
    CMD_MANUAL_P2   AT %MW9 : UINT;
    CMD_ALARM_RESET AT %MW10 : UINT;  (* HMI writes 1; the PLC clears it *)
  END_VAR

  VAR
    (* Control state *)
    Mode       : UINT := 0;
    LastMode   : UINT := 0;
    ActiveLead : UINT := 1;        (* lead pump after any failover: 1 or 2 *)
    LagRun     : BOOL := FALSE;    (* Auto logic wants the lag pump running *)
    Handover   : BOOL := FALSE;    (* lead change in progress: keep the old lead on *)
    OldLead    : UINT := 0;
    P1Cmd      : BOOL := FALSE;
    P2Cmd      : BOOL := FALSE;
    LeadRunning : BOOL;
    LagAvail   : BOOL;
    LeadAvail  : BOOL;

    (* Pump failure latches: fault feedback or failed to start. A failed pump
       is locked out (never commanded) until the operator presses Reset and
       the simulator no longer reports a fault. *)
    P1Failed : BOOL := FALSE;
    P2Failed : BOOL := FALSE;
    P1StartTmr : TON;   (* run commanded but no running feedback *)
    P2StartTmr : TON;

    (* Lead/lag timers: the time delays that stop short-cycling *)
    LagStartTmr : TON;
    LagStopTmr  : TON;

    (* Alarms (design 4.3) *)
    LowArmTmr   : TON;   (* start-up bypass for the low-pressure alarm *)
    LowPressTmr : TON;
    HighPressTmr : TON;
    CommLossTmr : TON;
    LastSimHb   : UINT := 0;
    AlmLow   : BOOL := FALSE;
    AlmHigh  : BOOL := FALSE;
    AlmComm  : BOOL := FALSE;
    AlmUnack : BOOL := FALSE;
    AnyAlarm : BOOL;
    PrevAnyAlarm : BOOL := FALSE;
    ResetReq : BOOL;
    CommOk   : BOOL;
    LagRunning : BOOL;
  END_VAR

  (* =====================================================================
     1. First scan: load defaults (design 4.4).
        %MW memory is not kept across a runtime restart, so a zero setpoint
        means "never configured".
     ===================================================================== *)
  IF SP_PRESSURE = 0 THEN
    SP_PRESSURE     := 1200;   (* 120.0 psi *)
    SP_LAG_STOP     := 1450;   (* 145.0 psi *)
    SP_LOW_ALARM    := 1100;   (* 110.0 psi *)
    SP_HIGH_ALARM   := 1600;   (* 160.0 psi *)
    LAG_START_DELAY := 5;
    LAG_STOP_DELAY  := 30;
    CMD_MODE        := 1;      (* Auto *)
    CMD_LEAD_SELECT := 1;      (* P1 *)
    CMD_MANUAL_P1   := 0;
    CMD_MANUAL_P2   := 0;
    CMD_ALARM_RESET := 0;
  END_IF;

  (* =====================================================================
     2. Clamp HMI settings to their allowed ranges (design 5.3).
        The clamped value is written back, so the HMI reads back what the
        PLC is actually using.
     ===================================================================== *)
  SP_PRESSURE := LIMIT(800, SP_PRESSURE, 1500);
  IF SP_LAG_STOP < SP_PRESSURE + 100 THEN
    SP_LAG_STOP := SP_PRESSURE + 100;   (* hysteresis of at least 10 psi *)
  END_IF;
  LAG_START_DELAY := LIMIT(1, LAG_START_DELAY, 60);
  LAG_STOP_DELAY  := LIMIT(5, LAG_STOP_DELAY, 300);
  CMD_MODE        := MIN(CMD_MODE, 2);
  CMD_LEAD_SELECT := LIMIT(1, CMD_LEAD_SELECT, 2);
  CMD_MANUAL_P1   := MIN(CMD_MANUAL_P1, 1);
  CMD_MANUAL_P2   := MIN(CMD_MANUAL_P2, 1);
  Mode := CMD_MODE;

  ResetReq := CMD_ALARM_RESET <> 0;

  (* =====================================================================
     3. Pump health. Fault feedback, or no running feedback 5 s after a run
        command, fails the pump (design 4.2 step 4 and 4.3).
     ===================================================================== *)
  P1StartTmr(IN := P1_RUN_CMD AND NOT P1_RUNNING, PT := T#5s);
  P2StartTmr(IN := P2_RUN_CMD AND NOT P2_RUNNING, PT := T#5s);
  IF P1_FAULT OR P1StartTmr.Q THEN P1Failed := TRUE; END_IF;
  IF P2_FAULT OR P2StartTmr.Q THEN P2Failed := TRUE; END_IF;
  IF ResetReq AND NOT P1_FAULT THEN P1Failed := FALSE; END_IF;
  IF ResetReq AND NOT P2_FAULT THEN P2Failed := FALSE; END_IF;

  (* =====================================================================
     4. Pump control by mode (design 4.1, 4.2)
     ===================================================================== *)
  IF Mode = 1 THEN
    (* ---------------- Auto: lead/lag ---------------- *)

    (* Entering Auto: take the operator's lead choice, and keep whatever
       is already running so the switch causes no bump. *)
    IF LastMode <> 1 THEN
      ActiveLead := CMD_LEAD_SELECT;
      IF ActiveLead = 1 THEN LagRun := P2Cmd; ELSE LagRun := P1Cmd; END_IF;
      Handover := FALSE;
    END_IF;

    (* Failover: the lead pump has failed and the other one is healthy, so
       promote it. The HMI lead selection follows, so the station does not
       swap back on its own once the failed pump is reset. *)
    IF ((ActiveLead = 1) AND P1Failed AND NOT P2Failed) OR
       ((ActiveLead = 2) AND P2Failed AND NOT P1Failed) THEN
      ActiveLead := 3 - ActiveLead;
      CMD_LEAD_SELECT := ActiveLead;
      LagRun := FALSE;
      Handover := FALSE;
    END_IF;

    (* Operator changed the lead pump: applied only while the lag pump is
       stopped (design 4.2 step 5). The old lead keeps running until the new
       one reports running, so pressure does not dip during the swap. *)
    IF (CMD_LEAD_SELECT <> ActiveLead) AND NOT LagRun AND NOT Handover THEN
      IF ((CMD_LEAD_SELECT = 1) AND NOT P1Failed) OR
         ((CMD_LEAD_SELECT = 2) AND NOT P2Failed) THEN
        OldLead := ActiveLead;
        ActiveLead := CMD_LEAD_SELECT;
        Handover := TRUE;
      END_IF;
    END_IF;

    IF ActiveLead = 1 THEN
      LeadAvail := NOT P1Failed;  LagAvail := NOT P2Failed;  LeadRunning := P1_RUNNING;
    ELSE
      LeadAvail := NOT P2Failed;  LagAvail := NOT P1Failed;  LeadRunning := P2_RUNNING;
    END_IF;

    IF Handover AND (LeadRunning OR NOT LeadAvail) THEN
      Handover := FALSE;
    END_IF;

    (* Lag start: pressure below setpoint for the start delay (step 2) *)
    LagStartTmr(IN := (PT101_DISCH < SP_PRESSURE) AND NOT LagRun AND LagAvail AND NOT Handover,
                PT := T#1s * UINT_TO_DINT(LAG_START_DELAY));
    IF LagStartTmr.Q THEN
      LagRun := TRUE;
    END_IF;

    (* Lag stop: pressure above the lag stop pressure for the stop delay (step 3).
       The gap between 120 and 145 psi is the hysteresis that stops cycling. *)
    LagStopTmr(IN := LagRun AND (PT101_DISCH > SP_LAG_STOP),
               PT := T#1s * UINT_TO_DINT(LAG_STOP_DELAY));
    IF LagStopTmr.Q OR NOT LagAvail THEN
      LagRun := FALSE;
    END_IF;

    (* Lead always runs (step 1); lag runs when called for *)
    IF ActiveLead = 1 THEN
      P1Cmd := TRUE;
      P2Cmd := LagRun OR (Handover AND OldLead = 2);
    ELSE
      P2Cmd := TRUE;
      P1Cmd := LagRun OR (Handover AND OldLead = 1);
    END_IF;

  ELSIF Mode = 2 THEN
    (* ---------------- Manual: operator runs each pump ---------------- *)
    P1Cmd := CMD_MANUAL_P1 = 1;
    P2Cmd := CMD_MANUAL_P2 = 1;
    LagRun := FALSE;
    Handover := FALSE;

  ELSE
    (* ---------------- Off ---------------- *)
    P1Cmd := FALSE;
    P2Cmd := FALSE;
    LagRun := FALSE;
    Handover := FALSE;
  END_IF;

  (* Interlock in every mode: a failed pump is never commanded *)
  IF P1Failed THEN P1Cmd := FALSE; END_IF;
  IF P2Failed THEN P2Cmd := FALSE; END_IF;
  P1_RUN_CMD := P1Cmd;
  P2_RUN_CMD := P2Cmd;

  (* Outside Manual, the manual commands track the pumps, so switching to
     Manual leaves them as they are (bumpless transfer). *)
  IF Mode <> 2 THEN
    CMD_MANUAL_P1 := BOOL_TO_UINT(P1Cmd);
    CMD_MANUAL_P2 := BOOL_TO_UINT(P2Cmd);
  END_IF;
  LastMode := Mode;

  (* =====================================================================
     5. Alarms (design 4.3). Each one latches until the operator presses
        Reset AND its condition has cleared.
     ===================================================================== *)

  (* Low pressure is armed once the station has been out of Off for 15 s,
     so a normal start-up (pressure rising from 20 psi) does not alarm. *)
  LowArmTmr(IN := Mode <> 0, PT := T#15s);
  LowPressTmr(IN := LowArmTmr.Q AND (PT101_DISCH < SP_LOW_ALARM), PT := T#5s);
  HighPressTmr(IN := PT101_DISCH > SP_HIGH_ALARM, PT := T#5s);

  (* Simulator comm loss: heartbeat has not changed for 3 s *)
  CommLossTmr(IN := SIM_HEARTBEAT = LastSimHb, PT := T#3s);
  LastSimHb := SIM_HEARTBEAT;
  CommOk := NOT CommLossTmr.Q;

  IF LowPressTmr.Q  THEN AlmLow  := TRUE; END_IF;
  IF HighPressTmr.Q THEN AlmHigh := TRUE; END_IF;
  IF CommLossTmr.Q  THEN AlmComm := TRUE; END_IF;

  IF ResetReq THEN
    IF NOT LowPressTmr.Q  THEN AlmLow  := FALSE; END_IF;
    IF NOT HighPressTmr.Q THEN AlmHigh := FALSE; END_IF;
    IF NOT CommLossTmr.Q  THEN AlmComm := FALSE; END_IF;
    AlmUnack := FALSE;          (* Reset also acknowledges *)
    CMD_ALARM_RESET := 0;       (* handled *)
  END_IF;

  (* A newly raised alarm is unacknowledged until the next Reset *)
  AnyAlarm := AlmLow OR AlmHigh OR P1Failed OR P2Failed OR AlmComm;
  IF AnyAlarm AND NOT PrevAnyAlarm THEN AlmUnack := TRUE; END_IF;
  PrevAnyAlarm := AnyAlarm;

  (* =====================================================================
     6. Status block for the HMI (design 5.2)
     ===================================================================== *)
  IF ActiveLead = 1 THEN LagRunning := P2_RUNNING; ELSE LagRunning := P1_RUNNING; END_IF;

  HMI_DISCH_PRESS := PT101_DISCH;
  HMI_SUCT_PRESS  := PT100_SUCT;
  HMI_DEMAND      := DEMAND;
  HMI_STATUS := BOOL_TO_UINT(P1_RUN_CMD)
              + BOOL_TO_UINT(P1_RUNNING) * 2
              + BOOL_TO_UINT(P2_RUN_CMD) * 4
              + BOOL_TO_UINT(P2_RUNNING) * 8
              + BOOL_TO_UINT(LagRunning) * 16
              + BOOL_TO_UINT(CommOk)     * 32;
  HMI_ALARMS := BOOL_TO_UINT(AlmLow)
              + BOOL_TO_UINT(AlmHigh)  * 2
              + BOOL_TO_UINT(P1Failed) * 4
              + BOOL_TO_UINT(P2Failed) * 8
              + BOOL_TO_UINT(AlmComm)  * 16
              + BOOL_TO_UINT(AlmUnack) * 32768;
  HMI_LEAD_PUMP   := ActiveLead;
  HMI_MODE_ACTIVE := Mode;

  IF PLC_HEARTBEAT = 65535 THEN
    PLC_HEARTBEAT := 0;
  ELSE
    PLC_HEARTBEAT := PLC_HEARTBEAT + 1;
  END_IF;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK MainTask(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM Inst0 WITH MainTask : PumpStation;
  END_RESOURCE
END_CONFIGURATION
