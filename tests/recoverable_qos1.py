#!/usr/bin/env python3
"""Raw MQTT 3.1.1 peers for reconnect-resilient QoS 1 semantics."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def exact(conn: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise EOFError("peer closed")
        data.extend(chunk)
    return bytes(data)


def packet(conn: socket.socket) -> bytes:
    head = exact(conn, 1)
    encoded = bytearray()
    size, multiplier = 0, 1
    while True:
        digit = exact(conn, 1)[0]
        encoded.append(digit)
        size += (digit & 127) * multiplier
        if not digit & 128:
            break
        multiplier *= 128
    return head + encoded + exact(conn, size)


def body_offset(data: bytes) -> int:
    index = 1
    while data[index] & 128:
        index += 1
    return index + 1


def accept_connect(listener: socket.socket, session_present: bool) -> socket.socket:
    conn, _ = listener.accept()
    conn.settimeout(5)
    connect = packet(conn)
    assert connect[0] == 0x10
    body = body_offset(connect)
    connect_flags = connect[body + 7]
    assert not connect_flags & 0x02, "ResumeSession must use CleanSession=false"
    conn.sendall(b"\x20\x02" + (b"\x01" if session_present else b"\x00") + b"\x00")
    return conn


def publish_id(data: bytes) -> int:
    index = body_offset(data)
    topic_size = int.from_bytes(data[index:index + 2], "big")
    index += 2 + topic_size
    return int.from_bytes(data[index:index + 2], "big")


def free_port() -> int:
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]
    finally:
        listener.close()


def wait_listening(port: int, process: subprocess.Popen, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"mosquitto exited early ({process.returncode})\n{stdout}\n{stderr}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"mosquitto did not listen on port {port}")


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    process.communicate()


class RecoverableQos1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        moon = os.environ.get("MOON", str(ROOT / "scripts/moon.sh"))
        subprocess.run([moon, "build", "--target", "native"], cwd=ROOT, check=True)
        matches = list((ROOT / "_build/native/debug/build").glob(
            "**/examples/recovery_driver/recovery_driver.exe"
        ))
        if len(matches) != 1:
            raise AssertionError(f"expected one recovery driver, found {matches}")
        cls.driver = matches[0]

    def run_case(
        self, handler, *, mode="recover", attempts="3", extra_env=None,
    ):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(8)
        errors = []

        def serve():
            try:
                handler(listener)
            except Exception as error:  # delivered to the main assertion
                errors.append(error)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        env = os.environ.copy()
        env.update({
            "Q1_PORT": str(listener.getsockname()[1]),
            "Q1_MODE": mode,
            "Q1_RECONNECT_ATTEMPTS": attempts,
            "Q1_CLIENT_ID": f"moon-q1-{mode}-{time.time_ns()}",
        })
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            [str(self.driver)], cwd=ROOT, env=env,
            text=True, capture_output=True, timeout=10,
        )
        worker.join(timeout=8)
        self.assertFalse(worker.is_alive(), "raw peer did not finish")
        self.assertEqual(errors, [], errors)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_same_packet_id_and_dup_after_session_resume(self):
        evidence = {}

        def peer(listener):
            first = accept_connect(listener, False)
            first_publish = packet(first)
            evidence["first_id"] = publish_id(first_publish)
            evidence["first_dup"] = bool(first_publish[0] & 0x08)
            first.close()

            second = accept_connect(listener, True)
            replay = packet(second)
            evidence["second_id"] = publish_id(replay)
            evidence["second_dup"] = bool(replay[0] & 0x08)
            second.sendall(b"\x40\x02" + evidence["second_id"].to_bytes(2, "big"))
            self.assertEqual(packet(second), b"\xe0\x00")
            second.close()

        output = self.run_case(peer)
        self.assertEqual(evidence["first_id"], evidence["second_id"])
        self.assertFalse(evidence["first_dup"])
        self.assertTrue(evidence["second_dup"])
        self.assertIn("acknowledged", output)
        self.assertIn("attempts=2", output)

    def test_wait_timeout_does_not_own_delivery(self):
        def peer(listener):
            conn = accept_connect(listener, False)
            publish = packet(conn)
            time.sleep(0.08)
            conn.sendall(b"\x40\x02" + publish_id(publish).to_bytes(2, "big"))
            self.assertEqual(packet(conn), b"\xe0\x00")
            conn.close()

        output = self.run_case(peer, mode="cancel_wait")
        self.assertIn("wait_cancelled", output)
        self.assertIn("acknowledged", output)
        self.assertIn("attempts=1", output)

    def test_session_loss_stops_without_replay(self):
        def peer(listener):
            first = accept_connect(listener, False)
            packet(first)
            first.close()
            second = accept_connect(listener, False)
            self.assertEqual(second.recv(1), b"", "client replayed into a lost session")
            second.close()

        output = self.run_case(peer)
        self.assertIn("unknown reason=broker returned Session Present=false", output)

    def test_resume_skips_duplicate_subscription_at_capacity_one(self):
        evidence = {}

        def peer(listener):
            first = accept_connect(listener, False)
            subscribe = packet(first)
            self.assertEqual(subscribe[0], 0x82)
            subscribe_id = int.from_bytes(
                subscribe[body_offset(subscribe):body_offset(subscribe) + 2],
                "big",
            )
            first.sendall(
                b"\x90\x03" + subscribe_id.to_bytes(2, "big") + b"\x01"
            )
            first_publish = packet(first)
            evidence["first_id"] = publish_id(first_publish)
            first.close()

            second = accept_connect(listener, True)
            replay = packet(second)
            self.assertEqual(replay[0] & 0xF0, 0x30)
            evidence["second_id"] = publish_id(replay)
            evidence["second_dup"] = bool(replay[0] & 0x08)
            second.sendall(
                b"\x40\x02" + evidence["second_id"].to_bytes(2, "big")
            )
            self.assertEqual(packet(second), b"\xe0\x00")
            second.close()

        output = self.run_case(peer, extra_env={"Q1_SUBSCRIBE": "1"})
        self.assertEqual(evidence["first_id"], evidence["second_id"])
        self.assertTrue(evidence["second_dup"])
        self.assertIn("subscribed", output)
        self.assertIn("acknowledged", output)

    def test_real_mosquitto_persistent_session_replay(self):
        broker_port = free_port()
        with tempfile.TemporaryDirectory(prefix="moon-q1-mosquitto-") as temp:
            config = Path(temp) / "mosquitto.conf"
            config.write_text(
                f"listener {broker_port} 127.0.0.1\n"
                "allow_anonymous true\n"
                "persistence false\n",
                encoding="utf-8",
            )
            broker = subprocess.Popen(
                [str(ROOT / ".tools/mosquitto"), "-c", str(config), "-v"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.addCleanup(stop_process, broker)
            wait_listening(broker_port, broker)
            evidence = {}

            def forward_one(source, destination):
                value = packet(source)
                destination.sendall(value)
                return value

            def proxy(listener):
                first, _ = listener.accept()
                first.settimeout(5)
                with first, socket.create_connection(
                    ("127.0.0.1", broker_port), timeout=5,
                ) as backend:
                    backend.settimeout(5)
                    forward_one(first, backend)
                    first_connack = forward_one(backend, first)
                    self.assertEqual(first_connack, b"\x20\x02\x00\x00")
                    first_publish = forward_one(first, backend)
                    evidence["first_id"] = publish_id(first_publish)
                    evidence["first_dup"] = bool(first_publish[0] & 0x08)
                    first_puback = packet(backend)
                    self.assertEqual(
                        first_puback,
                        b"\x40\x02" + evidence["first_id"].to_bytes(2, "big"),
                    )
                    # Deliberately hide the broker's PUBACK and drop transport.

                second, _ = listener.accept()
                second.settimeout(5)
                with second, socket.create_connection(
                    ("127.0.0.1", broker_port), timeout=5,
                ) as backend:
                    backend.settimeout(5)
                    forward_one(second, backend)
                    second_connack = forward_one(backend, second)
                    self.assertEqual(second_connack, b"\x20\x02\x01\x00")
                    replay = forward_one(second, backend)
                    evidence["second_id"] = publish_id(replay)
                    evidence["second_dup"] = bool(replay[0] & 0x08)
                    forward_one(backend, second)
                    self.assertEqual(forward_one(second, backend), b"\xe0\x00")

            output = self.run_case(proxy)
            stop_process(broker)
            self.assertEqual(evidence["first_id"], evidence["second_id"])
            self.assertFalse(evidence["first_dup"])
            self.assertTrue(evidence["second_dup"])
            self.assertIn("acknowledged", output)
            self.assertIn("attempts=2", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
