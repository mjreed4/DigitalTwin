"""Tests for OpenPLC deployment address handling."""

import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plc"))

from deploy_to_openplc import resolve_ipv4  # noqa: E402


class ResolveSimulatorAddress(unittest.TestCase):
    def test_resolves_hostname_to_ipv4(self):
        with patch("deploy_to_openplc.socket.gethostbyname", return_value="10.10.1.7") as resolver:
            self.assertEqual(resolve_ipv4("pump-simulator.internal"), "10.10.1.7")
        resolver.assert_called_once_with("pump-simulator.internal")

    def test_keeps_ipv4_address(self):
        self.assertEqual(resolve_ipv4("127.0.0.1"), "127.0.0.1")

    def test_reports_unresolvable_hostname(self):
        with patch("deploy_to_openplc.socket.gethostbyname", side_effect=socket.gaierror("name not known")):
            with self.assertRaisesRegex(SystemExit, "Could not resolve simulator host"):
                resolve_ipv4("missing.internal")


if __name__ == "__main__":
    unittest.main()
