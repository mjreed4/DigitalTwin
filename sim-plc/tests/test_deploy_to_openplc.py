"""Tests for OpenPLC deployment address handling."""

import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plc"))

from deploy_to_openplc import OpenPLC, resolve_ipv4  # noqa: E402


class ResolveSimulatorAddress(unittest.TestCase):
    def test_resolves_hostname_to_ipv4(self):
        with patch("deploy_to_openplc.socket.gethostbyname", return_value="10.10.1.7") as resolver:
            self.assertEqual(resolve_ipv4("pump-simulator.internal"), "10.10.1.7")
        resolver.assert_called_once_with("pump-simulator.internal")

    def test_retries_hostname_resolution(self):
        with (
            patch(
                "deploy_to_openplc.socket.gethostbyname",
                side_effect=[socket.gaierror("name not known"), "10.10.1.7"],
            ) as resolver,
            patch("deploy_to_openplc.time.sleep") as sleep,
        ):
            self.assertEqual(
                resolve_ipv4("pump-simulator.internal"),
                "10.10.1.7",
            )

        self.assertEqual(resolver.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_keeps_ipv4_address(self):
        self.assertEqual(resolve_ipv4("127.0.0.1"), "127.0.0.1")

    def test_reports_unresolvable_hostname(self):
        with patch("deploy_to_openplc.socket.gethostbyname", side_effect=socket.gaierror("name not known")):
            with patch("deploy_to_openplc.time.sleep"):
                with self.assertRaisesRegex(SystemExit, "Could not resolve simulator host.*60 attempts"):
                    resolve_ipv4("missing.internal")


class ConfigureSlaveDevice(unittest.TestCase):
    def test_updates_existing_device_endpoint(self):
        page = (
            '<tr onclick="document.location=\'modbus-edit-device?table_id=7\'">'
            "<td>PumpStationSim</td><td>TCP</td></tr>"
        )
        with patch.object(OpenPLC, "get", return_value=page), patch.object(OpenPLC, "post") as post:
            OpenPLC("http://localhost:8080").ensure_slave_device(
                "PumpStationSim", "10.10.1.7", 5020, 1
            )

        args, _ = post.call_args
        self.assertEqual(args[0], "modbus-edit-device")
        self.assertEqual(args[1]["db_dev_id"], "7")
        self.assertEqual(args[1]["device_ip"], "10.10.1.7")
        self.assertEqual(args[1]["device_port"], "5020")

    def test_adds_device_when_missing(self):
        with patch.object(OpenPLC, "get", return_value="<html></html>"), patch.object(OpenPLC, "post") as post:
            OpenPLC("http://localhost:8080").ensure_slave_device(
                "PumpStationSim", "10.10.1.7", 5020, 1
            )

        args, _ = post.call_args
        self.assertEqual(args[0], "add-modbus-device")


if __name__ == "__main__":
    unittest.main()
