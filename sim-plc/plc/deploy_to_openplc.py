"""Load the pump station program into an OpenPLC Runtime, the same steps a student clicks through.

    1. Log in to the OpenPLC web interface (default user openplc / openplc)
    2. Slave Devices: add the simulator as a Generic Modbus TCP device (design 5.1)
    3. Programs: upload pump_station.st and wait for it to compile
    4. Dashboard: Start PLC

Standard library only, so it runs on the engineering workstation without installs.

    python deploy_to_openplc.py                              # lab defaults
    python deploy_to_openplc.py --plc http://127.0.0.1:8080 --sim-ip 127.0.0.1
"""

import argparse
import http.cookiejar
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def resolve_ipv4(host: str) -> str:
    for attempt in range(60):
        try:
            return socket.gethostbyname(host)
        except socket.gaierror as exc:
            if attempt == 59:
                sys.exit(
                    f"Could not resolve simulator host {host!r} to an IPv4 address "
                    f"after 60 attempts: {exc}"
                )
            time.sleep(2)


class OpenPLC:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, path: str) -> str:
        with self.http.open(f"{self.base}/{path}", timeout=30) as r:
            return r.read().decode("utf-8", "replace")

    def post(self, path: str, fields: dict, files: dict | None = None) -> str:
        """POST as multipart/form-data, which is what every OpenPLC form uses."""
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (filename, data) in (files or {}).items():
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{filename}"\r\n'
                          f'Content-Type: application/octet-stream\r\n\r\n').encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{self.base}/{path}", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with self.http.open(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")

    def login(self, user: str, password: str) -> None:
        page = self.post("login", {"username": user, "password": password})
        if "Bad credentials" in page:
            sys.exit("Login to OpenPLC failed: check --user / --password")

    def ensure_slave_device(self, name: str, ip: str, port: int, unit: int) -> None:
        devices_page = self.get("modbus")
        existing = re.search(
            rf"<tr onclick=\"document\.location='modbus-edit-device\?table_id=(\d+)'\">"
            rf"\s*<td>{re.escape(name)}</td>",
            devices_page,
        )
        fields = {
            "device_name": name, "device_protocol": "TCP", "device_id": str(unit),
            "device_ip": ip, "device_port": str(port),
            "device_cport": "", "device_baud": "", "device_parity": "", "device_data": "",
            "device_stop": "", "device_pause": "0",
            "di_start": "0", "di_size": "4",     # P1/P2 running, P1/P2 fault -> %IX100.0-3
            "do_start": "0", "do_size": "2",     # P1/P2 run command           -> %QX100.0-1
            "ai_start": "0", "ai_size": "4",     # PT-101, PT-100, demand, hb  -> %IW100-103
            "aor_start": "0", "aor_size": "0",
            "aow_start": "0", "aow_size": "0",
        }
        if existing:
            fields["db_dev_id"] = existing.group(1)
            self.post("modbus-edit-device", fields)
            print(f"updated slave device '{name}' to {ip}:{port} unit {unit}")
        else:
            self.post("add-modbus-device", fields)
            print(f"added slave device '{name}' at {ip}:{port} unit {unit}")

    def upload_and_compile(self, st_path: Path, name: str) -> None:
        page = self.post("upload-program", {}, {"file": (st_path.name, st_path.read_bytes())})
        m = re.search(r"value='(\d+\.st)' id='prog_file'", page)
        if not m:
            sys.exit("Upload failed: OpenPLC did not return a program file name")
        self.post("upload-program-action", {
            "prog_name": name, "prog_descr": "Lead/lag pump station control (design doc section 4)",
            "prog_file": m.group(1), "epoch_time": str(int(time.time())),
        })
        self.get(f"compile-program?file={m.group(1)}")
        for _ in range(180):
            logs = self.get("compilation-logs")
            if "Compilation finished successfully" in logs:
                print("program compiled")
                return
            if "finished with errors" in logs:
                sys.exit("Compilation failed:\n" + logs)
            time.sleep(1)
        sys.exit("Timed out waiting for compilation")

    def start(self) -> None:
        self.get("start_plc")
        print("PLC started")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plc", default="http://10.10.0.20:8080", help="OpenPLC web interface URL")
    ap.add_argument("--user", default="openplc")
    ap.add_argument("--password", default="openplc")
    ap.add_argument("--sim-ip", "--sim-host", dest="sim_host", default="10.10.0.10",
                    help="simulator IPv4 address or host name")
    ap.add_argument("--sim-port", type=int, default=5020)
    ap.add_argument("--program", default=str(HERE / "pump_station.st"))
    args = ap.parse_args()

    plc = OpenPLC(args.plc)
    plc.login(args.user, args.password)
    simulator_ip = resolve_ipv4(args.sim_host)
    plc.ensure_slave_device("PumpStationSim", simulator_ip, args.sim_port, 1)
    plc.upload_and_compile(Path(args.program), "Pump Station Lead-Lag")
    plc.start()


if __name__ == "__main__":
    main()
