#!/usr/bin/env python3
"""Characterize current dial policy without parsing display strings or changing it."""
import os
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import unittest

from harness import NativeProcess, integration_fixture
from protocol_faults import recv_packet

ROOT = Path(__file__).resolve().parents[1]


class DialPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([os.environ.get("MOON", str(ROOT / "scripts/moon.sh")), "build", "--target", "native"], cwd=ROOT, check=True)
        binaries = list((ROOT / "_build/native/debug/build").glob("**/dial_policy_driver/dial_policy_driver.exe"))
        assert len(binaries) == 1, binaries
        cls.driver = binaries[0]
        cls.observations = []

    @classmethod
    def tearDownClass(cls):
        directory = Path(os.environ.get("MQTT_EVIDENCE_DIR", ROOT / "_build/pro-audit"))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "dial-policy-observations.json").write_text(json.dumps(cls.observations, indent=2) + "\n")

    def listener(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(4)
        return listener

    def start(self, listener, protocol, **extra):
        driver = NativeProcess([self.driver], env={**os.environ,
            "POLICY_PORT": str(listener.getsockname()[1]), "POLICY_PROTOCOL": str(protocol), **extra})
        self.addCleanup(driver.close)
        return driver

    def connect(self, listener, protocol, reason=0, context=None):
        connection = listener.accept()[0]
        self.addCleanup(connection.close)
        connection.settimeout(4)
        if context:
            connection = context.wrap_socket(connection, server_side=True)
            self.addCleanup(connection.close)
        self.assertEqual(recv_packet(connection)[0], 0x10)
        # Numeric wire values are part of each table row. MQTT311's public
        # ConnectionRefused(String) is intentionally treated as opaque.
        connection.sendall(bytes([0x20, 3 if protocol == 5 else 2, 0, reason]) + (b"\x00" if protocol == 5 else b""))
        return connection

    def test_connack_policy_by_dial_origin(self):
        for protocol, codes in ((311, (1, 3, 5)), (5, (0x87, 0x89, 0x9c))):
            for origin in ("initial", "transport", "maintenance"):
                if protocol == 311 and origin == "maintenance":
                    continue  # MQTT311 has no server DISCONNECT reason packet.
                for code in codes:
                    with self.subTest(protocol=protocol, origin=origin, wire_reason=code):
                        listener = self.listener()
                        driver = self.start(listener, protocol)
                        if origin != "initial":
                            first = self.connect(listener, protocol)
                            driver.expect("connected 1")
                            if origin == "maintenance":
                                first.sendall(b"\xe0\x01\x89")
                            else:
                                first.shutdown(socket.SHUT_RDWR)
                            first.close()
                        rejected = self.connect(listener, protocol, code)
                        rejected.close()
                        if origin == "transport":
                            final = self.connect(listener, protocol)
                            driver.expect("connected 2")
                            self.assertEqual(recv_packet(final)[0], 0xe0)
                        else:
                            expected = f"terminal BrokerRejected {code}" if protocol == 5 else "terminal ConnectionRefused"
                            driver.expect(expected)
                        result = driver.finish()
                        self.assertEqual(result.returncode, 0, result.stdout)
                        self.observations.append({"protocol": protocol, "origin": origin,
                            "wire_reason": code, "result": result.stdout.splitlines()})
                        # Process exit and an empty accept backlog bound the
                        # negative claim without waiting for a retry timeout.
                        listener.setblocking(False)
                        with self.assertRaises(BlockingIOError):
                            listener.accept()
                        driver.close()
                        listener.close()

    def test_tls_failure_by_dial_origin(self):
        fixture = integration_fixture()
        with tempfile.TemporaryDirectory(prefix="mqtt-policy-tls-") as temp:
            directory = Path(temp)
            good, bad = directory / "good", directory / "bad"
            good.mkdir(); bad.mkdir()
            fixture.write_pki(good); fixture.write_pki(bad)
            contexts = []
            for path in (good, bad):
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(path / "server.pem", path / "server.key")
                contexts.append(context)
            for origin in ("initial", "transport", "maintenance"):
                with self.subTest(origin=origin, failure="untrusted_CA"):
                    listener = self.listener()
                    driver = self.start(listener, 5, POLICY_CA=str(good / "ca.pem"))
                    if origin != "initial":
                        first = self.connect(listener, 5, context=contexts[0])
                        driver.expect("connected 1")
                        if origin == "maintenance":
                            first.sendall(b"\xe0\x01\x89")
                        else:
                            first.shutdown(socket.SHUT_RDWR)
                        first.close()
                    failed = listener.accept()[0]
                    failed.settimeout(4)
                    with failed, self.assertRaises(ssl.SSLError):
                        contexts[1].wrap_socket(failed, server_side=True)
                    if origin == "initial":
                        driver.expect("terminal TlsFailure")
                    else:
                        final = self.connect(listener, 5, context=contexts[0])
                        driver.expect("connected 2")
                        self.assertEqual(recv_packet(final)[0], 0xe0)
                    result = driver.finish()
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.observations.append({"protocol": 5, "origin": origin,
                        "tls_failure": "untrusted_CA", "result": result.stdout.splitlines()})
                    driver.close(); listener.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
