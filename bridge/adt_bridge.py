import os
import time
import logging
from azure.identity import DefaultAzureCredential
from azure.digitaltwins.core import DigitalTwinsClient
from pymodbus.client import ModbusTcpClient

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Configuration from Environment Variables
ADT_HOST_NAME = os.getenv("ADT_HOST_NAME", "pump-station-adt-lab.api.eus.digitaltwins.azure.net")
ADT_SERVICE_URL = f"https://{ADT_HOST_NAME}"
PLC_HOST = os.getenv("PLC_HOST", "pump-station-openplc")
PLC_PORT = int(os.getenv("PLC_PORT", 502))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 2.0))

def main():
    logging.info(f"Starting OT to Azure Digital Twins Telemetry Bridge targeting {ADT_SERVICE_URL}...")
    
    # 1. Authenticate with Azure Digital Twins using Managed Identity / Credentials
    credential = DefaultAzureCredential()
    adt_client = DigitalTwinsClient(ADT_SERVICE_URL, credential)

    # 2. Connect to OpenPLC Modbus TCP Server
    modbus_client = ModbusTcpClient(PLC_HOST, port=PLC_PORT)

    while True:
        try:
            if not modbus_client.connect():
                logging.warning(f"Connecting to OpenPLC at {PLC_HOST}:{PLC_PORT}...")
                time.sleep(POLL_INTERVAL)
                continue

            # Read Holding Registers 0 through 7 (Status block exposed by OpenPLC)
            rr = modbus_client.read_holding_registers(address=0, count=8, device_id=1)
            if rr.isError():
                logging.error(f"Modbus read error: {rr}")
                time.sleep(POLL_INTERVAL)
                continue

            registers = rr.registers
            disch_press = registers[0] / 10.0
            suct_press  = registers[1] / 10.0
            demand      = registers[2] / 10.0
            status_bits = registers[3]
            alarm_bits  = registers[4]

            # Decode bitfields per system design map
            p1_running = bool(status_bits & (1 << 1))
            p2_running = bool(status_bits & (1 << 3))
            p1_faulted = bool(alarm_bits & (1 << 2))
            p2_faulted = bool(alarm_bits & (1 << 3))

            # Patch PumpStation_1 Twin
            adt_client.update_digital_twin("PumpStation_1", [
                {"op": "replace", "path": "/PT100_SuctionPressure", "value": suct_press},
                {"op": "replace", "path": "/PT101_DischargePressure", "value": disch_press},
                {"op": "replace", "path": "/DemandFlow", "value": demand}
            ])

            # Patch Pump_P1 Twin
            adt_client.update_digital_twin("Pump_P1", [
                {"op": "replace", "path": "/IsRunning", "value": p1_running},
                {"op": "replace", "path": "/IsFaulted", "value": p1_faulted}
            ])

            # Patch Pump_P2 Twin
            adt_client.update_digital_twin("Pump_P2", [
                {"op": "replace", "path": "/IsRunning", "value": p2_running},
                {"op": "replace", "path": "/IsFaulted", "value": p2_faulted}
            ])

            logging.info(f"Synced ADT -> Suct: {suct_press} psi | Disch: {disch_press} psi | Demand: {demand}% | P1: [Run={p1_running}, Fault={p1_faulted}] | P2: [Run={p2_running}, Fault={p2_faulted}]")

        except Exception as e:
            logging.error(f"Sync error: {e}")
        
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()