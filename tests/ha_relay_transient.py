#!/usr/bin/env python3
"""Native HA controller transient-error and permanent-error regression tests.

The tests use a real Mosquitto broker plus a packet-aware TCP proxy.  The
proxy drops one selected controller PUBLISH after the broker has accepted the
connection, so the controller must keep its scope alive, reconnect, flush only
bounded retained metadata, and issue a fresh query without replaying a
physical command.  A separate raw peer sends a malformed SUBACK to prove
permanent protocol errors still terminate the controller.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("integration", ROOT / "tests/integration/run.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
from build_paths import demo_build_dir


def until(predicate, why: str, timeout: float = 8.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError(why)


def recv_exact(conn: socket.socket, size: int) -> bytes:
    value = bytearray()
    while len(value) < size:
        chunk = conn.recv(size - len(value))
        if not chunk:
            raise EOFError("peer closed")
        value.extend(chunk)
    return bytes(value)


def recv_packet(conn: socket.socket) -> bytes:
    first = recv_exact(conn, 1)
    remaining = 0
    multiplier = 1
    encoded = bytearray()
    while True:
        digit = recv_exact(conn, 1)[0]
        encoded.append(digit)
        remaining += (digit & 0x7F) * multiplier
        if digit < 0x80:
            break
        multiplier *= 128
        if multiplier > 128**3:
            raise ValueError("malformed MQTT remaining length")
    return first + bytes(encoded) + recv_exact(conn, remaining)


def packet_end(buf: bytearray) -> int | None:
    remaining = 0
    multiplier = 1
    pos = 1
    while pos < len(buf):
        digit = buf[pos]
        remaining += (digit & 0x7F) * multiplier
        pos += 1
        if digit < 0x80:
            return pos + remaining if len(buf) >= pos + remaining else None
        multiplier *= 128
        if multiplier > 128**3:
            raise ValueError("malformed MQTT remaining length")
    return None


def publish_fields(packet: bytes) -> tuple[str, bytes] | None:
    if packet[0] >> 4 != 3:
        return None
    pos = 1
    while packet[pos] & 0x80:
        pos += 1
    pos += 1
    topic_size = int.from_bytes(packet[pos:pos + 2], "big")
    pos += 2
    topic = packet[pos:pos + topic_size].decode("utf-8")
    pos += topic_size
    if packet[0] & 0x06:
        pos += 2
    return topic, packet[pos:]


def publish_packet_id(packet: bytes) -> int | None:
    if packet[0] >> 4 != 3 or not packet[0] & 0x06:
        return None
    pos = 1
    while packet[pos] & 0x80:
        pos += 1
    pos += 1
    topic_size = int.from_bytes(packet[pos:pos + 2], "big")
    pos += 2 + topic_size
    return int.from_bytes(packet[pos:pos + 2], "big")


class DropProxy:
    """Forward MQTT TCP and drop exactly one selected client PUBLISH."""

    def __init__(self, target_port: int, should_drop):
        self.target_port = target_port
        self.should_drop = should_drop
        self.port = h.free_port()
        self.dropped = threading.Event()
        self.closed = threading.Event()
        self.stop_event = threading.Event()
        self.connections: set[socket.socket] = set()
        self.connection_lock = threading.Lock()
        self.threads: list[threading.Thread] = []
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", self.port))
        self.listener.listen(8)
        self.accept_thread = threading.Thread(target=self._accept, daemon=True)
        self.accept_thread.start()

    def _accept(self) -> None:
        while not self.stop_event.is_set():
            try:
                source, _ = self.listener.accept()
                target = socket.create_connection(("127.0.0.1", self.target_port), timeout=3)
            except OSError:
                return
            source.settimeout(3)
            target.settimeout(3)
            with self.connection_lock:
                self.connections.update((source, target))
            for args in ((source, target, True), (target, source, False)):
                thread = threading.Thread(target=self._copy, args=args, daemon=True)
                self.threads.append(thread)
                thread.start()

    def _copy(self, source: socket.socket, target: socket.socket, from_client: bool) -> None:
        buf = bytearray()
        try:
            while not self.stop_event.is_set():
                chunk = source.recv(65536)
                if not chunk:
                    return
                if not from_client:
                    target.sendall(chunk)
                    continue
                buf.extend(chunk)
                while (end := packet_end(buf)) is not None:
                    packet = bytes(buf[:end])
                    del buf[:end]
                    fields = publish_fields(packet)
                    if fields is not None and not self.dropped.is_set() and self.should_drop(*fields):
                        self.dropped.set()
                        self.closed.set()
                        return
                    target.sendall(packet)
        except (OSError, EOFError):
            return
        finally:
            with self.connection_lock:
                self.connections.discard(source)
                self.connections.discard(target)
            for conn in (source, target):
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.listener.close()
        except OSError:
            pass
        with self.connection_lock:
            active = list(self.connections)
        for conn in active:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self.accept_thread.join(timeout=2)
        for thread in self.threads:
            thread.join(timeout=2)


class RawMalformedPeer:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.error: BaseException | None = None
        self.done = threading.Event()
        self.discovery_seen = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self.listener.accept()
            with conn:
                conn.settimeout(5)
                connect = recv_packet(conn)
                assert connect[0] >> 4 == 1
                conn.sendall(b"\x20\x02\x00\x00")
                subscribe = recv_packet(conn)
                assert subscribe[0] == 0x82
                pos = 1
                while subscribe[pos] & 0x80:
                    pos += 1
                pos += 1
                packet_id = int.from_bytes(subscribe[pos:pos + 2], "big")
                pos += 2
                reasons = 0
                while pos < len(subscribe):
                    topic_size = int.from_bytes(subscribe[pos:pos + 2], "big")
                    pos += 2 + topic_size + 1
                    reasons += 1
                conn.sendall(b"\x90" + bytes([2 + reasons]) + packet_id.to_bytes(2, "big") + b"\x00" * reasons)
                discovery = recv_packet(conn)
                fields = publish_fields(discovery)
                if fields is not None and fields[0].endswith("/device/query"):
                    query_id = publish_packet_id(discovery)
                    assert query_id is not None
                    conn.sendall(b"\x40\x02" + query_id.to_bytes(2, "big"))
                    discovery = recv_packet(conn)
                    fields = publish_fields(discovery)
                assert fields is not None and fields[0].endswith("/config")
                self.discovery_seen.set()
                pos = 1
                while discovery[pos] & 0x80:
                    pos += 1
                pos += 1
                topic_size = int.from_bytes(discovery[pos:pos + 2], "big")
                actual_pos = pos + 2 + topic_size
                actual = int.from_bytes(discovery[actual_pos:actual_pos + 2], "big")
                wrong = 1 if actual == 65535 else actual + 1
                # A wrong PUBACK identifier is a permanent protocol error after
                # the controller has entered its metadata flush path.
                conn.sendall(b"\x40\x02" + wrong.to_bytes(2, "big"))
                try:
                    recv_exact(conn, 1)
                except (EOFError, ConnectionResetError, socket.timeout):
                    pass
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()

    def close(self) -> None:
        self.listener.close()
        self.thread.join(timeout=2)


class HaRelayTransient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([str(h.MOON), "build", "--target", "native"], cwd=ROOT, check=True)
        cls.binary = demo_build_dir(ROOT).parent / "ha_relay"

    def setUp(self):
        self.broker = h.Broker()
        self.broker.start()
        self.addCleanup(self.broker.close)
        self.prefix = "ha-transient/" + uuid.uuid4().hex
        self.entity = "relay_" + uuid.uuid4().hex
        self.messages: list[tuple[str, bytes]] = []
        self.logs: list[tempfile.TemporaryFile] = []
        self.env_base = {
            **os.environ,
            "HA_RELAY_PREFIX": self.prefix,
            "HA_RELAY_ENTITY_ID": self.entity,
        }
        self.oracle = h.paho.Client(h.paho.CallbackAPIVersion.VERSION2,
                                    client_id="ha-transient-oracle-" + uuid.uuid4().hex)
        ready = threading.Event()
        self.oracle.on_connect = lambda c, u, f, r, p: c.subscribe([
            (self.prefix + "/device/#", 1),
            (self.prefix + "/controller/#", 1),
            (self.prefix + "/ha/#", 1),
            (self.discovery_topic, 1),
        ])
        self.oracle.on_subscribe = lambda *args: ready.set()
        self.oracle.on_message = lambda c, u, m: self.messages.append((m.topic, m.payload))
        self.oracle.connect("127.0.0.1", self.broker.port)
        self.oracle.loop_start()
        self.addCleanup(self.oracle.loop_stop)
        self.addCleanup(self.oracle.disconnect)
        self.assertTrue(ready.wait(5))

    def launch(self, name: str, port: int) -> subprocess.Popen:
        env = {**self.env_base, "MQTT_DEMO_PORT": str(port)}
        output = tempfile.TemporaryFile(mode="w+b")
        self.logs.append(output)
        self.addCleanup(output.close)
        process = subprocess.Popen(
            [str(self.binary / name / (name + ".exe"))],
            env=env, stdout=output, stderr=subprocess.STDOUT,
        )
        process._audit_output = output
        return process

    @staticmethod
    def stop_process(process: subprocess.Popen) -> None:
        was_running = process.poll() is None
        if was_running:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        output = getattr(process, "_audit_output", None)
        if output is not None and not was_running and process.returncode not in (None, 0):
            output.seek(0)
            print(output.read().decode("utf-8", errors="replace"))

    def _metadata_drop(self, drop_topic: str, expected_discovery_count: int) -> None:
        self.messages.clear()
        proxy = DropProxy(self.broker.port, lambda topic, payload: topic == drop_topic)
        controller = self.launch("controller", proxy.port)
        try:
            before_availability = len(self.values("/controller/availability"))
            until(proxy.dropped.is_set, "proxy did not drop selected metadata")
            time.sleep(.5)
            self.assertIsNone(controller.poll(), "transient metadata failure terminated controller")
            until(lambda: len(self.discovery_values()) >= expected_discovery_count,
                  "discovery metadata was not retried")
            until(lambda: len(self.values("/controller/availability")) > before_availability,
                  "availability metadata was not retried")
            until(lambda: len(self.values("/device/query")) >= 1, "reconnect query missing")
            until(lambda: b"offline" in self.values("/controller/availability"),
                  "reconnect availability metadata missing")
            until(lambda: b"None" in self.values("/ha/state"),
                  "reconnect unknown state missing")
        finally:
            self.stop_process(controller)
            proxy.close()

    def values(self, suffix: str) -> list[bytes]:
        return [p for topic, p in self.messages if topic == self.prefix + suffix]

    @property
    def discovery_topic(self) -> str:
        return "homeassistant/switch/" + self.entity + "/config"

    def discovery_values(self) -> list[bytes]:
        return [p for topic, p in self.messages if topic == self.discovery_topic]

    def test_discovery_drop_keeps_controller_scope_alive(self):
        self._metadata_drop(self.discovery_topic, expected_discovery_count=1)

    def test_availability_drop_keeps_controller_scope_alive(self):
        self._metadata_drop(self.prefix + "/controller/availability", expected_discovery_count=2)

    def test_state_drop_does_not_replay_physical_command(self):
        proxy = DropProxy(
            self.broker.port,
            lambda topic, payload: topic == self.prefix + "/ha/state" and payload == b"ON",
        )
        self.addCleanup(proxy.close)
        device = self.launch("simulator", proxy.port)
        controller = self.launch("controller", proxy.port)
        self.addCleanup(self.stop_process, controller)
        self.addCleanup(self.stop_process, device)
        until(lambda: b"online" in self.values("/device/availability"), "device did not connect")
        until(lambda: b"OFF" in self.values("/ha/state"), "initial controller state missing")
        until(lambda: b"online" in self.values("/controller/availability"),
              "controller did not establish an online state")
        old_query_ids = {json.loads(value)["id"] for value in self.values("/device/query")}
        self.oracle.publish(self.prefix + "/ha/set", b"ON", qos=1).wait_for_publish(5)
        until(lambda: any(b'"target":"ON"' in p for p in self.values("/device/set")),
              "physical command missing")
        self.assertEqual(len(self.values("/device/set")), 1)
        until(proxy.dropped.is_set, "proxy did not drop reported ON metadata")
        time.sleep(.5)
        self.assertIsNone(controller.poll(), "transient state failure terminated controller")
        until(lambda: any(json.loads(value)["id"] not in old_query_ids
                          for value in self.values("/device/query")),
              "reconnect query did not get a fresh identity")
        new_query_ids = {json.loads(value)["id"] for value in self.values("/device/query")} - old_query_ids
        until(lambda: any(json.loads(value).get("correlation_id") in new_query_ids and
                          json.loads(value).get("state") == "ON"
                          for value in self.values("/device/feedback")),
              "fresh query did not confirm ON")
        self.assertEqual(len(self.values("/device/set")), 1,
                         "reconnect replayed the physical command")
        until(lambda: b"ON" in self.values("/ha/state"), "fresh query state not reported")

    def test_permanent_protocol_error_still_terminates_controller(self):
        peer = RawMalformedPeer()
        peer.start()
        self.addCleanup(peer.close)
        env = {**self.env_base, "MQTT_DEMO_PORT": str(peer.port)}
        result = subprocess.run(
            [str(self.binary / "controller" / "controller.exe")],
            env=env, capture_output=True, text=True, timeout=8,
        )
        peer.done.wait(3)
        self.assertNotEqual(result.returncode, 0, "permanent protocol error was swallowed")
        self.assertTrue(peer.discovery_seen.is_set(), "raw peer did not reach metadata flush")
        self.assertIn("ProtocolError", result.stdout + result.stderr)
        self.assertIsNone(peer.error, peer.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
