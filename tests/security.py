#!/usr/bin/env python3
"""Additional hostile-input production peers; TLS and floods retain their own oracles."""
import socket
import unittest

import mqtt5_runtime as runtime
import reason_reconnect as reconnect
from mqtt5_runtime import connack, frame, text, variable
from reason_reconnect import packet


class HostileFrames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.RuntimePeers.setUpClass()
        cls.driver = runtime.RuntimePeers.driver

    run_peer = runtime.RuntimePeers.run_peer

    def test_declared_length_and_property_boundaries_fail_before_payload(self):
        # The 268MB declaration has no body: rejection must happen at framing,
        # without allocating or waiting to receive the declared bytes.
        duplicate_expiry = b"\x02\x00\x00\x00\x01" * 2
        for attack in (
            b"\x30\xff\xff\xff\x7f",
            frame(0x30, text("security/input") + b"\x7f"),
            frame(0x30, text("security/input") + variable(len(duplicate_expiry)) + duplicate_expiry),
        ):
            with self.subTest(attack=attack.hex()):
                def peer(connection, _):
                    connack(connection)
                    connection.sendall(attack)
                    try:
                        self.assertEqual(connection.recv(1), b"")
                    except ConnectionResetError:
                        pass
                result = self.run_peer("disconnect-reason", peer, success=False)
                self.assertIn("ProtocolError", result.stdout + result.stderr)


class HostileReasonText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reconnect.ReasonReconnect.setUpClass()
        cls.driver = reconnect.ReasonReconnect.driver

    run_handler = reconnect.ReasonReconnect.run_handler

    def test_control_characters_and_large_reason_cannot_change_numeric_policy(self):
        diagnostic = ("server busy\r\nconnected generation=999\t\x1b[31m " * 80).encode()
        properties = b"\x1f" + len(diagnostic).to_bytes(2, "big") + diagnostic
        def peer(listener):
            with listener.accept()[0] as connection:
                connection.settimeout(4)
                packet(connection)
                connection.sendall(b"\x20\x03\x00\x00\x00")
                connection.sendall(frame(0xe0, b"\x8e" + variable(len(properties)) + properties))
                try:
                    self.assertEqual(packet(connection), b"")
                except (ConnectionResetError, EOFError):
                    pass
        result = self.run_handler(peer)
        self.assertIn("terminal server_disconnect code=142", result.stdout)
        self.assertNotIn("connected generation=999", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
