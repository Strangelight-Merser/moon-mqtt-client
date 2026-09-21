#!/usr/bin/env python3
"""Raw MQTT 5 acceptance for opt-in reason-aware server reconnect."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]


def packet(connection: socket.socket) -> bytes:
    head = connection.recv(1)
    if not head:
        return b""
    encoded = bytearray()
    size, multiplier = 0, 1
    while True:
        digit = connection.recv(1)
        if not digit:
            raise EOFError("truncated remaining length")
        encoded.extend(digit)
        size += (digit[0] & 127) * multiplier
        if not digit[0] & 128:
            break
        multiplier *= 128
    body = bytearray()
    while len(body) < size:
        chunk = connection.recv(size - len(body))
        if not chunk:
            raise EOFError("truncated packet")
        body.extend(chunk)
    return head + bytes(encoded) + bytes(body)


def body_offset(value: bytes) -> int:
    index = 1
    while value[index] & 128:
        index += 1
    return index + 1


def publish_id(value: bytes) -> int:
    index = body_offset(value)
    topic_size = int.from_bytes(value[index:index + 2], "big")
    index += 2 + topic_size
    return int.from_bytes(value[index:index + 2], "big")


class ReasonPeer:
    def __init__(self, reasons: list[int], *, final_stays_ready: bool = False):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(5)
        self.port = self.listener.getsockname()[1]
        self.reasons = reasons
        self.final_stays_ready = final_stays_ready
        self.connects: list[bytes] = []
        self.trailing: list[bytes] = []
        self.errors: list[Exception] = []
        self.worker = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.worker.start()

    def _run(self):
        try:
            for reason in self.reasons:
                connection, _ = self.listener.accept()
                connection.settimeout(3)
                with connection:
                    connect = packet(connection)
                    self.connects.append(connect)
                    connection.sendall(b"\x20\x03\x00\x00\x00")
                    connection.sendall(b"\xe0\x01" + bytes([reason]))
                    try:
                        self.trailing.append(packet(connection))
                    except (socket.timeout, ConnectionResetError):
                        self.trailing.append(b"")
            if self.final_stays_ready:
                connection, _ = self.listener.accept()
                connection.settimeout(3)
                with connection:
                    self.connects.append(packet(connection))
                    connection.sendall(b"\x20\x03\x00\x00\x00")
                    self.trailing.append(packet(connection))
        except Exception as error:
            self.errors.append(error)
        finally:
            self.listener.close()

    def finish(self):
        self.worker.join(8)
        if self.worker.is_alive():
            raise AssertionError("raw reason peer did not finish")
        if self.errors:
            raise AssertionError(self.errors)


class ReasonReconnect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        moon = os.environ.get("MOON", str(ROOT / "scripts/moon.sh"))
        subprocess.run([moon, "build", "--target", "native"], cwd=ROOT, check=True)
        matches = list((ROOT / "_build/native/debug/build").glob(
            "**/tests/reason_reconnect_driver/reason_reconnect_driver.exe"
        ))
        if len(matches) != 1:
            raise AssertionError(f"expected one reason reconnect driver, found {matches}")
        cls.driver = matches[0]

    def run_case(
        self, reasons: list[int], *, policy: str, budget: int,
        target_connections: int = 999, final_stays_ready: bool = False,
    ) -> tuple[subprocess.CompletedProcess, ReasonPeer]:
        peer = ReasonPeer(reasons, final_stays_ready=final_stays_ready)
        peer.start()
        env = os.environ.copy()
        env.update({
            "B3_PORT": str(peer.port), "B3_POLICY": policy,
            "B3_RETRY_BUDGET": str(budget),
            "B3_TARGET_CONNECTIONS": str(target_connections),
        })
        result = subprocess.run(
            [str(self.driver)], cwd=ROOT, env=env,
            text=True, capture_output=True, timeout=10,
        )
        peer.finish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result, peer

    def run_handler(
        self, handler, *, delivery: bool = False, budget: int = 1,
        target_connections: int = 999,
    ):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)
        errors = []

        def serve():
            try:
                handler(listener)
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        env = os.environ.copy()
        env.update({
            "B3_PORT": str(listener.getsockname()[1]),
            "B3_POLICY": "retry",
            "B3_RETRY_BUDGET": str(budget),
            "B3_DELIVERY": "1" if delivery else "0",
            "B3_TARGET_CONNECTIONS": str(target_connections),
        })
        result = subprocess.run(
            [str(self.driver)], cwd=ROOT, env=env,
            text=True, capture_output=True, timeout=10,
        )
        worker.join(8)
        self.assertFalse(worker.is_alive(), "custom reason peer did not finish")
        self.assertEqual(errors, [], (errors, result.stdout, result.stderr))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_default_busy_is_terminal(self):
        result, peer = self.run_case([0x89], policy="terminal", budget=3)
        self.assertEqual(len(peer.connects), 1)
        self.assertIn("terminal server_disconnect code=137", result.stdout)

    def test_opt_in_busy_and_shutdown_reconnect(self):
        for reason in (0x89, 0x8B):
            with self.subTest(reason=reason):
                result, peer = self.run_case(
                    [reason], policy="retry", budget=1,
                    target_connections=2, final_stays_ready=True,
                )
                self.assertEqual(len(peer.connects), 2)
                self.assertIn("connected generation=2", result.stdout)

    def test_lifetime_budget_survives_successful_connacks(self):
        result, peer = self.run_case(
            [0x89, 0x8B, 0x89], policy="retry", budget=2,
        )
        self.assertEqual(len(peer.connects), 3)
        self.assertIn("terminal server_disconnect code=137", result.stdout)

    def test_zero_budget_and_disallowed_reasons_never_reconnect(self):
        for reason, budget in ((0x89, 0), (0x8E, 3), (0x87, 3), (0x82, 3), (0x9C, 3)):
            with self.subTest(reason=reason, budget=budget):
                result, peer = self.run_case([reason], policy="retry", budget=budget)
                self.assertEqual(len(peer.connects), 1)
                self.assertIn(f"terminal server_disconnect code={reason}", result.stdout)

    def test_reason_string_cannot_make_disallowed_code_retryable(self):
        def peer(listener):
            with listener.accept()[0] as connection:
                connection.settimeout(3)
                packet(connection)
                connection.sendall(b"\x20\x03\x00\x00\x00")
                diagnostic = b"server busy"
                properties = b"\x1f" + len(diagnostic).to_bytes(2, "big") + diagnostic
                connection.sendall(
                    b"\xe0" + bytes([2 + len(properties), 0x8E, len(properties)]) +
                    properties
                )
                try:
                    self.assertEqual(packet(connection), b"")
                except (ConnectionResetError, EOFError):
                    pass
            listener.settimeout(0.25)
            with self.assertRaises(socket.timeout):
                listener.accept()

        result = self.run_handler(peer)
        self.assertIn("terminal server_disconnect code=142", result.stdout)

    def test_negative_connack_after_retry_is_typed_terminal(self):
        def peer(listener):
            with listener.accept()[0] as first:
                first.settimeout(3)
                packet(first)
                first.sendall(b"\x20\x03\x00\x00\x00\xe0\x01\x89")
                try:
                    packet(first)
                except (ConnectionResetError, EOFError, socket.timeout):
                    pass
            with listener.accept()[0] as second:
                second.settimeout(3)
                packet(second)
                second.sendall(b"\x20\x03\x00\x87\x00")
                try:
                    self.assertEqual(packet(second), b"")
                except (ConnectionResetError, EOFError):
                    pass
            listener.settimeout(0.25)
            with self.assertRaises(socket.timeout):
                listener.accept()

        result = self.run_handler(peer)
        self.assertIn("terminal broker_rejected code=135", result.stdout)

    def test_later_ordinary_reconnect_does_not_inherit_server_dial_origin(self):
        def peer(listener):
            with listener.accept()[0] as first:
                first.settimeout(3)
                packet(first)
                first.sendall(b"\x20\x03\x00\x00\x00\xe0\x01\x89")
            with listener.accept()[0] as server_retry:
                server_retry.settimeout(3)
                packet(server_retry)
                server_retry.sendall(b"\x20\x03\x00\x00\x00")
                # An unrelated transport loss follows a successful CONNACK.
            with listener.accept()[0] as ordinary_reject:
                ordinary_reject.settimeout(3)
                packet(ordinary_reject)
                ordinary_reject.sendall(b"\x20\x03\x00\x87\x00")
                try:
                    self.assertEqual(packet(ordinary_reject), b"")
                except (ConnectionResetError, EOFError):
                    pass
            with listener.accept()[0] as ordinary_success:
                ordinary_success.settimeout(3)
                packet(ordinary_success)
                ordinary_success.sendall(b"\x20\x03\x00\x00\x00")
                self.assertEqual(packet(ordinary_success)[0], 0xE0)

        result = self.run_handler(
            peer, budget=3, target_connections=3,
        )
        self.assertIn("connected generation=3", result.stdout)
        self.assertNotIn("terminal broker_rejected", result.stdout)

    def test_failed_server_directed_dial_does_not_taint_next_ordinary_dial(self):
        def peer(listener):
            with listener.accept()[0] as first:
                first.settimeout(3)
                packet(first)
                first.sendall(b"\x20\x03\x00\x00\x00\xe0\x01\x89")
            with listener.accept()[0] as failed_server_retry:
                failed_server_retry.settimeout(3)
                packet(failed_server_retry)
                # Close before CONNACK: this consumes the server-directed dial.
            with listener.accept()[0] as ordinary_reject:
                ordinary_reject.settimeout(3)
                packet(ordinary_reject)
                ordinary_reject.sendall(b"\x20\x03\x00\x87\x00")
                try:
                    self.assertEqual(packet(ordinary_reject), b"")
                except (ConnectionResetError, EOFError):
                    pass
            with listener.accept()[0] as ordinary_success:
                ordinary_success.settimeout(3)
                packet(ordinary_success)
                ordinary_success.sendall(b"\x20\x03\x00\x00\x00")
                self.assertEqual(packet(ordinary_success)[0], 0xE0)

        result = self.run_handler(
            peer, budget=3, target_connections=2,
        )
        self.assertIn("connected generation=2", result.stdout)
        self.assertNotIn("terminal broker_rejected", result.stdout)

    def test_recoverable_delivery_requires_session_present_before_replay(self):
        def peer(listener):
            with listener.accept()[0] as first:
                first.settimeout(3)
                packet(first)
                first.sendall(b"\x20\x03\x00\x00\x00")
                original = packet(first)
                self.assertEqual(original[0], 0x32)
                first.sendall(b"\xe0\x01\x89")
            with listener.accept()[0] as second:
                second.settimeout(3)
                packet(second)
                second.sendall(b"\x20\x03\x00\x00\x00")
                try:
                    self.assertEqual(packet(second), b"")
                except (ConnectionResetError, EOFError):
                    pass

        result = self.run_handler(peer, delivery=True)
        self.assertIn("delivery unknown reason=broker returned Session Present=false", result.stdout)

    def test_recoverable_delivery_replays_same_id_with_dup_and_ack(self):
        evidence = {}

        def peer(listener):
            with listener.accept()[0] as first:
                first.settimeout(3)
                packet(first)
                first.sendall(b"\x20\x03\x00\x00\x00")
                original = packet(first)
                evidence["original_id"] = publish_id(original)
                evidence["original_dup"] = bool(original[0] & 0x08)
                first.sendall(b"\xe0\x01\x8b")
            with listener.accept()[0] as second:
                second.settimeout(3)
                packet(second)
                second.sendall(b"\x20\x03\x01\x00\x00")
                replay = packet(second)
                evidence["replay_id"] = publish_id(replay)
                evidence["replay_dup"] = bool(replay[0] & 0x08)
                second.sendall(
                    b"\x40\x02" + evidence["replay_id"].to_bytes(2, "big")
                )
                self.assertEqual(packet(second)[0], 0xE0)

        result = self.run_handler(peer, delivery=True)
        self.assertEqual(evidence["original_id"], evidence["replay_id"])
        self.assertFalse(evidence["original_dup"])
        self.assertTrue(evidence["replay_dup"])
        self.assertIn("delivery acknowledged", result.stdout)
        self.assertIn("attempts=2", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
