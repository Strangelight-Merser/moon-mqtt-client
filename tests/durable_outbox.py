#!/usr/bin/env python3
"""Process-crash and real-broker evidence for the concrete durable outbox."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BROKER = ROOT / ".tools/mosquitto"
BROKER = Path(os.environ.get(
    "MOSQUITTO",
    shutil.which("mosquitto") or (
        str(LOCAL_BROKER) if LOCAL_BROKER.is_file() else "mosquitto"
    ),
))


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


def publish_id(data: bytes) -> int:
    index = body_offset(data)
    topic_size = int.from_bytes(data[index:index + 2], "big")
    index += 2 + topic_size
    return int.from_bytes(data[index:index + 2], "big")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_listening(port: int, process: subprocess.Popen, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"mosquitto command: {process.args!r}\n"
                f"mosquitto exited early ({process.returncode})\n{stdout}\n{stderr}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"mosquitto command: {process.args!r} did not listen")


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    process.communicate()


class DurableOutboxProcessRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        moon = os.environ.get("MOON", str(ROOT / "scripts/moon.sh"))
        subprocess.run([moon, "build", "--target", "native"], cwd=ROOT, check=True)
        matches = list((ROOT / "_build/native/debug/build").glob(
            "**/examples/durable_recovery_driver/durable_recovery_driver.exe"
        ))
        if len(matches) != 1:
            raise AssertionError(f"expected one durable driver, found {matches}")
        cls.driver = matches[0]

    @classmethod
    def ack_delete_crash_driver(cls) -> Path:
        """Build an isolated binary that exits after parsing PUBACK, before DELETE.

        The failpoint exists only in a temporary source copy. Production source
        and its public API remain identical to the candidate under test.
        """
        cached = getattr(cls, "_ack_crash_driver", None)
        if cached is not None:
            return cached
        cls._ack_crash_temp = tempfile.TemporaryDirectory(
            prefix="moon-durable-ack-crash-build-",
        )
        cls.addClassCleanup(cls._ack_crash_temp.cleanup)
        checkout = Path(cls._ack_crash_temp.name) / "source"
        ignored = shutil.ignore_patterns(
            ".git", "_build", ".tools", ".venv", ".mooncakes",
        )
        shutil.copytree(ROOT, checkout, ignore=ignored)
        for dependency in (".tools", ".mooncakes"):
            os.symlink(ROOT / dependency, checkout / dependency,
                       target_is_directory=True)
        runtime_path = checkout / "runtime.mbt"
        runtime = runtime_path.read_text(encoding="utf-8")
        needle = "                  meta.runtime.outbox.acknowledge(delivery.delivery_id)"
        replacement = (
            "                  durable_ack_delete_crash_for_test(87)\n" + needle
        )
        if runtime.count(needle) != 1:
            raise AssertionError("durable ACK DELETE seam changed")
        runtime_path.write_text(runtime.replace(needle, replacement), encoding="utf-8")
        (checkout / "durable_ack_crash_injection.mbt").write_text(
            'extern "C" fn durable_ack_delete_crash_for_test(code : Int) = "_exit"\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(checkout / "scripts/moon.sh"), "build", "--target", "native"],
            cwd=checkout, text=True, capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            raise AssertionError(
                "failed to build isolated ACK crash injection:\n"
                + result.stdout + result.stderr
            )
        matches = list((checkout / "_build/native/debug/build").glob(
            "**/examples/durable_recovery_driver/durable_recovery_driver.exe"
        ))
        if len(matches) != 1:
            raise AssertionError(f"expected one injected durable driver, found {matches}")
        cls._ack_crash_driver = matches[0]
        return matches[0]

    def driver_env(self, outbox: Path, port: int, mode: str = "run"):
        env = os.environ.copy()
        env.update({
            "D1_OUTBOX": str(outbox),
            "D1_PORT": str(port),
            "D1_MODE": mode,
            "D1_CLIENT_ID": "moon-durable-process-recovery",
            "D1_OPERATION_TIMEOUT_MS": "5000",
            "D1_RECONNECT_ATTEMPTS": "2",
        })
        return env

    def inspect(self, outbox: Path, port: int) -> str:
        result = subprocess.run(
            [str(self.driver)], cwd=ROOT,
            env=self.driver_env(outbox, port, "inspect"),
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def assert_process_crash_replay(self, crash_after_ack: bool):
        broker_port = free_port()
        with tempfile.TemporaryDirectory(prefix="moon-durable-process-") as temp:
            temp_path = Path(temp)
            outbox = temp_path / "outbox.sqlite3"
            config = temp_path / "mosquitto.conf"
            config.write_text(
                f"listener {broker_port} 127.0.0.1\n"
                "allow_anonymous true\n"
                "persistence false\n",
                encoding="utf-8",
            )
            broker = subprocess.Popen(
                [str(BROKER), "-c", str(config), "-v"], cwd=ROOT,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            print(f"mosquitto command: {broker.args!r}")
            self.addCleanup(stop_process, broker)
            wait_listening(broker_port, broker)

            listener = socket.socket()
            self.addCleanup(listener.close)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(8)
            proxy_port = listener.getsockname()[1]
            first_publish_seen = threading.Event()
            first_process_crashed = threading.Event()
            errors = []
            evidence = {}

            def forward(source, destination):
                value = packet(source)
                destination.sendall(value)
                return value

            def proxy():
                try:
                    first, _ = listener.accept()
                    first.settimeout(5)
                    with first, socket.create_connection(
                        ("127.0.0.1", broker_port), timeout=5,
                    ) as backend:
                        backend.settimeout(5)
                        forward(first, backend)
                        self.assertEqual(forward(backend, first), b"\x20\x02\x00\x00")
                        first_publish = forward(first, backend)
                        evidence["first_id"] = publish_id(first_publish)
                        evidence["first_dup"] = bool(first_publish[0] & 0x08)
                        expected_ack = b"\x40\x02" + evidence["first_id"].to_bytes(2, "big")
                        broker_ack = packet(backend)
                        self.assertEqual(broker_ack, expected_ack)
                        if crash_after_ack:
                            # Forward PUBACK to the injected build. Its read
                            # loop exits from the exact ACK path immediately
                            # before durable DELETE.
                            first.sendall(broker_ack)
                        # Otherwise retain the original lost-PUBACK window: the
                        # broker accepted PUBLISH but the client never receives
                        # its ACK before the process is killed.
                        first_publish_seen.set()
                        self.assertTrue(first_process_crashed.wait(5))

                    second, _ = listener.accept()
                    second.settimeout(5)
                    with second, socket.create_connection(
                        ("127.0.0.1", broker_port), timeout=5,
                    ) as backend:
                        backend.settimeout(5)
                        forward(second, backend)
                        self.assertEqual(forward(backend, second), b"\x20\x02\x01\x00")
                        replay = forward(second, backend)
                        evidence["second_id"] = publish_id(replay)
                        evidence["second_dup"] = bool(replay[0] & 0x08)
                        forward(backend, second)
                        self.assertEqual(forward(second, backend), b"\xe0\x00")
                except Exception as error:
                    errors.append(error)
                    first_publish_seen.set()

            # Build before starting bounded socket waits; the isolated compile
            # is deliberately outside all protocol deadlines.
            crash_driver = (
                self.ack_delete_crash_driver() if crash_after_ack else self.driver
            )
            worker = threading.Thread(target=proxy, daemon=True)
            worker.start()
            first = subprocess.Popen(
                [str(crash_driver)], cwd=ROOT,
                env=self.driver_env(outbox, proxy_port),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.addCleanup(stop_process, first)
            self.assertTrue(first_publish_seen.wait(8), "first durable PUBLISH not seen")
            if errors:
                self.fail(errors)
            if crash_after_ack:
                first_stdout, first_stderr = first.communicate(timeout=8)
            else:
                first.kill()
                first_stdout, first_stderr = first.communicate(timeout=5)
            first_process_crashed.set()
            if crash_after_ack:
                self.assertEqual(first.returncode, 87, first_stdout + first_stderr)
            else:
                self.assertNotEqual(first.returncode, 0, first_stdout + first_stderr)

            crashed = self.inspect(outbox, proxy_port)
            self.assertIn("records=1", crashed)
            self.assertIn("attempts=1", crashed)
            self.assertIn("started=true", crashed)
            self.assertIn("state=pending", crashed)

            second = subprocess.run(
                [str(self.driver)], cwd=ROOT,
                env=self.driver_env(outbox, proxy_port),
                text=True, capture_output=True, timeout=10,
            )
            worker.join(timeout=8)
            self.assertFalse(worker.is_alive(), "durable proxy did not finish")
            self.assertEqual(errors, [], errors)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(evidence["first_id"], evidence["second_id"])
            self.assertFalse(evidence["first_dup"])
            self.assertTrue(evidence["second_dup"])
            self.assertIn("recovered=1", second.stdout)
            self.assertIn("acknowledged", second.stdout)
            self.assertIn("attempts=2", second.stdout)
            self.assertIn("records=0", self.inspect(outbox, proxy_port))
            stop_process(broker)

    def test_process_crash_replays_same_packet_with_dup_on_real_broker(self):
        self.assert_process_crash_replay(crash_after_ack=False)

    def test_process_crash_after_puback_before_delete_replays_same_packet(self):
        self.assert_process_crash_replay(crash_after_ack=True)

    def test_process_crash_after_commit_and_before_first_write_is_not_sent(self):
        with tempfile.TemporaryDirectory(prefix="moon-durable-before-write-") as temp:
            outbox = Path(temp) / "outbox.sqlite3"
            listener = socket.socket()
            self.addCleanup(listener.close)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(8)
            port = listener.getsockname()[1]
            errors = []

            def peer():
                try:
                    conn, _ = listener.accept()
                    conn.settimeout(5)
                    with conn:
                        self.assertEqual(packet(conn)[0], 0x10)
                        conn.sendall(b"\x20\x02\x00\x00")
                        self.assertEqual(
                            conn.recv(1), b"",
                            "PUBLISH reached the wire before crash_after_admit",
                        )
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=peer, daemon=True)
            worker.start()
            result = subprocess.run(
                [str(self.driver)], cwd=ROOT,
                env=self.driver_env(outbox, port, "crash_after_admit"),
                text=True, capture_output=True, timeout=10,
            )
            worker.join(timeout=8)
            self.assertFalse(worker.is_alive(), "before-write peer did not finish")
            self.assertEqual(errors, [], errors)
            self.assertEqual(result.returncode, 86, result.stdout + result.stderr)
            inspected = self.inspect(outbox, port)
            self.assertIn("records=1", inspected)
            self.assertIn("attempts=1", inspected)
            self.assertIn("started=false", inspected)
            self.assertIn("state=pending", inspected)

            # A later scope that cannot establish its first connection must not
            # reinterpret this never-sent row as a terminal broker outcome.
            listener.close()
            unavailable = subprocess.run(
                [str(self.driver)], cwd=ROOT,
                env=self.driver_env(outbox, port),
                text=True, capture_output=True, timeout=10,
            )
            self.assertNotEqual(
                unavailable.returncode, 0,
                unavailable.stdout + unavailable.stderr,
            )
            still_pending = self.inspect(outbox, port)
            self.assertIn("records=1", still_pending)
            self.assertIn("state=pending", still_pending)

            discard_env = self.driver_env(outbox, port, "discard")
            discard_env["D1_DELIVERY_ID"] = "durable-1"
            discarded = subprocess.run(
                [str(self.driver)], cwd=ROOT, env=discard_env,
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(
                discarded.returncode, 0, discarded.stdout + discarded.stderr,
            )
            self.assertIn("records=0", self.inspect(outbox, port))

    def test_recovered_possible_write_is_quarantined_when_session_is_lost(self):
        with tempfile.TemporaryDirectory(prefix="moon-durable-session-loss-") as temp:
            outbox = Path(temp) / "outbox.sqlite3"
            listener = socket.socket()
            self.addCleanup(listener.close)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(8)
            port = listener.getsockname()[1]
            published = threading.Event()
            killed = threading.Event()
            errors = []

            def accept_connect(session_present: bool):
                conn, _ = listener.accept()
                conn.settimeout(5)
                connect = packet(conn)
                self.assertEqual(connect[0], 0x10)
                conn.sendall(
                    b"\x20\x02" + (b"\x01" if session_present else b"\x00") + b"\x00"
                )
                return conn

            def peer():
                try:
                    first = accept_connect(False)
                    with first:
                        first_publish = packet(first)
                        self.assertEqual(first_publish[0] & 0xF0, 0x30)
                        published.set()
                        self.assertTrue(killed.wait(5))
                    second = accept_connect(False)
                    with second:
                        self.assertEqual(
                            second.recv(1), b"",
                            "recovered possible-write row was replayed into a lost session",
                        )
                except Exception as error:
                    errors.append(error)
                    published.set()

            worker = threading.Thread(target=peer, daemon=True)
            worker.start()
            first = subprocess.Popen(
                [str(self.driver)], cwd=ROOT,
                env=self.driver_env(outbox, port),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertTrue(published.wait(8), "first possible write was not observed")
            if errors:
                self.fail(errors)
            first.kill()
            first.communicate(timeout=5)
            killed.set()

            second = subprocess.run(
                [str(self.driver)], cwd=ROOT,
                env=self.driver_env(outbox, port),
                text=True, capture_output=True, timeout=10,
            )
            worker.join(timeout=8)
            self.assertFalse(worker.is_alive(), "session-loss peer did not finish")
            self.assertEqual(errors, [], errors)
            self.assertNotEqual(second.returncode, 0, second.stdout + second.stderr)
            inspected = self.inspect(outbox, port)
            self.assertIn("records=1", inspected)
            self.assertIn("started=true", inspected)
            self.assertIn("state=blocked:broker returned Session Present=false", inspected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
