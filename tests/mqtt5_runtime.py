#!/usr/bin/env python3
"""Independent MQTT 5 production-runtime peers, not codec-only probes."""
from __future__ import annotations

import os
import queue
import importlib.util
from pathlib import Path
import socket
import subprocess
import threading
import tempfile
import time
import unittest

from protocol_faults import recv_packet, body_offset, publish_id
from harness import NativeProcess

ROOT = Path(__file__).resolve().parents[1]


def variable(value: int) -> bytes:
    out = bytearray()
    while True:
        digit = value % 128
        value //= 128
        out.append(digit | (128 if value else 0))
        if not value:
            return bytes(out)


def frame(header: int, body: bytes) -> bytes:
    return bytes([header]) + variable(len(body)) + body


def text(value: str) -> bytes:
    encoded = value.encode()
    return len(encoded).to_bytes(2, "big") + encoded


def connack(conn: socket.socket, properties=b"", present=False):
    connect = recv_packet(conn)
    offset = body_offset(connect)
    assert connect[0] == 0x10, connect
    assert connect[offset:offset + 7] == b"\x00\x04MQTT\x05", connect
    conn.sendall(frame(0x20, bytes([int(present), 0]) + variable(len(properties)) + properties))
    return connect


def ack(conn: socket.socket, identifier: int, reason=0):
    # Legal compact PUBACK with reason but no property length.
    conn.sendall(frame(0x40, identifier.to_bytes(2, "big") + bytes([reason])))


class RuntimePeers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([os.environ.get("MOON", str(ROOT / "scripts/moon.sh")), "build", "--target", "native"], cwd=ROOT, check=True)
        matches = list((ROOT / "_build/native/debug/build").glob("**/mqtt5_runtime_driver/mqtt5_runtime_driver.exe"))
        if len(matches) != 1:
            raise AssertionError(f"production runtime driver missing: {matches}")
        cls.driver = matches[0]

    def run_peer(self, mode, action, *, success=True, extra_env=None, phases=None):
        failures = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(8)
            def serve():
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(4)
                        action(conn, listener)
                except BaseException as error:
                    failures.append(error)
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            command = [str(self.driver), mode, str(listener.getsockname()[1])]
            env = {**os.environ, "MOONBIT_ASYNC_CHECK_FD_LEAK": "1", **(extra_env or {})}
            if phases is None:
                result = subprocess.run(command, cwd=ROOT, text=True,
                                        capture_output=True, timeout=12, env=env)
            else:
                process = NativeProcess(command, cwd=ROOT, env=env, events=phases)
                try:
                    result = process.finish(timeout=12)
                finally:
                    process.close()
            thread.join(5)
            self.assertFalse(thread.is_alive(), "raw peer did not terminate")
            self.assertEqual(failures, [], (failures, result.stdout, result.stderr))
            if success:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

    def test_receive_maximum_does_not_block_subscribe_or_unsubscribe(self):
        phases = queue.Queue()

        def phase(expected):
            self.assertEqual(phases.get(timeout=3), expected)

        def peer(conn, _):
            connack(conn, b"\x21\x00\x01")
            first = recv_packet(conn)
            self.assertEqual(first[0], 0x32)
            self.assertTrue(first.endswith(b"first"))
            phase("h01: second-admitted pending=2 business=1")
            phase("h01: subscriptions-admitted pending=4")
            # These bounded reads fail on the original parked-head production
            # path, before any request timeout can advance the scenario.
            for expected, response in ((0x82, 0x90), (0xa2, 0xb0)):
                request = recv_packet(conn)
                self.assertEqual(request[0], expected, "PUBLISH spent unavailable credit")
                offset = body_offset(request)
                conn.sendall(frame(response, request[offset:offset + 2] + b"\x00\x00"))
            barrier = recv_packet(conn)
            self.assertEqual(barrier[0], 0x82, "SUBACK/UNSUBACK incorrectly restored publish credit")
            self.assertIn(b"h01/barrier", barrier)
            offset = body_offset(barrier)
            conn.sendall(frame(0x90, barrier[offset:offset + 2] + b"\x00\x00"))
            ack(conn, publish_id(first))
            second = recv_packet(conn)
            self.assertEqual(second[0], 0x32)
            self.assertTrue(second.endswith(b"second"))
            self.assertNotEqual(publish_id(first), publish_id(second))
            ack(conn, publish_id(second))
            self.assertEqual(recv_packet(conn)[0], 0xe0)

        result = self.run_peer("flow-subscriptions", peer, phases=phases)
        self.assertIn("h01: complete", result.stdout)

    def test_receive_maximum_keeps_control_ack_moving(self):
        def peer(conn, _):
            connack(conn, b"\x21\x00\x01")
            first = recv_packet(conn)
            self.assertEqual(first[0] & 0xf6, 0x32)
            first_id = publish_id(first)
            conn.settimeout(0.15)
            with self.assertRaises(socket.timeout):
                conn.recv(1)
            conn.settimeout(4)
            # While send credit is exhausted the inbound QoS1 PUBACK must pass.
            conn.sendall(frame(0x32, text("m1/incoming") + b"\x00\x4d\x00control"))
            control = recv_packet(conn)
            self.assertEqual(control[0], 0x40)
            self.assertEqual(control[body_offset(control):body_offset(control)+2], b"\x00\x4d")
            ack(conn, first_id, 0x10)
            second = recv_packet(conn)
            self.assertEqual(second[0] & 0xf6, 0x32)
            self.assertNotEqual(publish_id(second), first_id)
            ack(conn, publish_id(second))
            self.assertEqual(recv_packet(conn)[0], 0xe0)
        result = self.run_peer("flow", peer)
        self.assertIn("reason=16", result.stdout)

    def test_reconnect_reduces_credit_without_quarantining_queued_deliveries(self):
        def peer(conn, listener):
            connack(conn, b"\x21\x00\x02")
            originals = [recv_packet(conn), recv_packet(conn)]
            identifiers = [publish_id(packet) for packet in originals]
            self.assertNotEqual(*identifiers)
            self.assertTrue(all(packet[0] == 0x32 for packet in originals))
            conn.close()
            with listener.accept()[0] as resumed:
                resumed.settimeout(4)
                connack(resumed, b"\x21\x00\x01", present=True)
                first = recv_packet(resumed)
                self.assertEqual(first[0], 0x3a)
                self.assertEqual(publish_id(first), identifiers[0])
                resumed.settimeout(0.15)
                with self.assertRaises(socket.timeout):
                    resumed.recv(1)
                resumed.settimeout(4)
                ack(resumed, identifiers[0], 0x10)
                second = recv_packet(resumed)
                self.assertEqual(second[0], 0x3a)
                self.assertEqual(publish_id(second), identifiers[1])
                ack(resumed, identifiers[1])
                self.assertEqual(recv_packet(resumed)[0], 0xe0)
        self.assertIn("reason=16", self.run_peer("flow-reconnect", peer).stdout)

    def test_negative_puback_is_definite_broker_rejection(self):
        def peer(conn, _):
            connack(conn)
            publish = recv_packet(conn)
            identifier = publish_id(publish)
            props = b"\x1f" + text("denied by policy")
            conn.sendall(frame(0x40, identifier.to_bytes(2, "big") + b"\x87" + variable(len(props)) + props))
            self.assertEqual(recv_packet(conn)[0], 0xe0)
        result = self.run_peer("negative", peer)
        self.assertIn("rejected=135", result.stdout)
        self.assertIn("denied by policy", result.stdout)
        self.assertNotIn("OutcomeUnknown", result.stdout)

    def test_durable_negative_ack_reports_rejection_and_persists_removal(self):
        def peer(conn, _):
            connack(conn)
            publication = recv_packet(conn)
            ack(conn, publish_id(publication), 0x87)
            self.assertEqual(recv_packet(conn)[0], 0xe0)
        with tempfile.TemporaryDirectory(prefix="mqtt5-rejected-") as directory:
            path = str(Path(directory) / "outbox.sqlite3")
            result = self.run_peer("durable-negative", peer, extra_env={"M1_OUTBOX": path})
            self.assertIn("durable-rejected=135", result.stdout)

    def test_mixed_subscription_reasons_preserve_reader_lifecycle(self):
        def peer(conn, _):
            connack(conn)
            for expected, header, reasons in ((0x82, 0x90, b"\x01\x87"), (0xa2, 0xb0, b"\x00\x87"), (0xa2, 0xb0, b"\x87")):
                packet = recv_packet(conn)
                self.assertEqual(packet[0], expected)
                offset = body_offset(packet)
                conn.sendall(frame(header, packet[offset:offset+2] + b"\x00" + reasons))
            publication = recv_packet(conn)
            self.assertEqual(publication[0], 0x32)
            ack(conn, publish_id(publication))
            self.assertEqual(recv_packet(conn)[0], 0xe0)
        self.assertIn("subscription-reasons-ok", self.run_peer("subscription-reasons", peer).stdout)

    def test_fresh_resume_rejects_unowned_session(self):
        def peer(conn, _):
            connack(conn, present=True)
            try:
                self.assertEqual(conn.recv(1), b"", "fresh client sent into unowned logical session")
            except ConnectionResetError:
                pass
        self.run_peer("fresh-resume", peer, success=False)

    def test_server_keepalive_overrides_configured_interval(self):
        def peer(conn, _):
            connack(conn, b"\x13\x00\x01")
            conn.settimeout(2.5)
            self.assertEqual(recv_packet(conn), b"\xc0\x00")
            conn.sendall(b"\xd0\x00")
            while True:
                packet = recv_packet(conn)
                if packet[0] == 0xe0:
                    return
                self.assertEqual(packet, b"\xc0\x00")
                conn.sendall(b"\xd0\x00")
        self.run_peer("keepalive", peer)

    def test_negotiated_publication_limits_reject_before_write(self):
        for mode, properties in (
            ("packet-limit", b"\x27\x00\x00\x00\x20"),
            ("qos-limit", b"\x24\x00"),
            ("retain-limit", b"\x25\x00"),
        ):
            with self.subTest(mode=mode):
                def peer(conn, _, properties=properties):
                    connack(conn, properties)
                    self.assertEqual(recv_packet(conn)[0], 0xe0, "incompatible PUBLISH reached wire")
                result = self.run_peer(mode, peer)
                self.assertIn("local-rejection", result.stdout)

    def test_server_disconnect_reason_is_terminal_without_reconnect(self):
        def peer(conn, listener):
            connack(conn)
            # Legal compact DISCONNECT: Session Taken Over, no property length.
            conn.sendall(b"\xe0\x01\x8e")
            try:
                self.assertEqual(conn.recv(1), b"")
            except ConnectionResetError:
                pass
            listener.settimeout(0.3)
            with self.assertRaises(socket.timeout):
                listener.accept()
        result = self.run_peer("disconnect-reason", peer)
        self.assertIn("disconnect=142", result.stdout)

    def test_production_metadata_roundtrip_with_independent_paho(self):
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
        spec = importlib.util.spec_from_file_location("m1_broker", ROOT / "tests/integration/run.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        broker = helper.Broker()
        client = None
        ready = threading.Event()
        received = []
        failures = []
        try:
            broker.start()
            client = helper.paho.Client(helper.paho.CallbackAPIVersion.VERSION2, client_id="m1-independent", protocol=helper.paho.MQTTv5)
            client.on_connect = lambda c, u, f, r, p: c.subscribe("m1/request", 1)
            client.on_subscribe = lambda *args: ready.set()
            def on_message(c, u, message):
                try:
                    self.assertEqual(message.payload, b"native5")
                    self.assertEqual(message.properties.ResponseTopic, "m1/reply")
                    self.assertEqual(message.properties.CorrelationData, b"\x00\xff")
                    self.assertEqual(message.properties.UserProperty, [("origin", "native"), ("origin", "ordered")])
                    self.assertGreater(message.properties.MessageExpiryInterval, 0)
                    self.assertLessEqual(message.properties.MessageExpiryInterval, 30)
                    props = Properties(PacketTypes.PUBLISH)
                    props.CorrelationData = message.properties.CorrelationData
                    props.UserProperty = [("origin", "paho")]
                    props.MessageExpiryInterval = 20
                    c.publish("m1/reply", b"paho5", qos=1, properties=props)
                    received.append(message.payload)
                except BaseException as error:
                    failures.append(error)
            client.on_message = on_message
            client.connect("127.0.0.1", broker.port)
            client.loop_start()
            self.assertTrue(ready.wait(5), "Paho SUBACK missing")
            result = subprocess.run([str(self.driver), "metadata", str(broker.port)], cwd=ROOT, text=True, capture_output=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(failures, [])
            self.assertEqual(received, [b"native5"])
            self.assertIn("metadata-ok", result.stdout)
        finally:
            if client:
                client.disconnect()
                client.loop_stop()
            broker.stop()

    def test_empty_durable_outbox_remembers_logical_session(self):
        spec = importlib.util.spec_from_file_location("m1_empty_broker", ROOT / "tests/integration/run.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        broker = helper.Broker()
        try:
            broker.start()
            with tempfile.TemporaryDirectory(prefix="mqtt5-empty-session-") as directory:
                env = {**os.environ, "M1_OUTBOX": str(Path(directory) / "outbox.sqlite3")}
                for attempt in range(2):
                    result = subprocess.run([str(self.driver), "durable-empty", str(broker.port)], cwd=ROOT, env=env, text=True, capture_output=True, timeout=12)
                    self.assertEqual(result.returncode, 0, (attempt, result.stdout, result.stderr))
                    self.assertIn("known-session-empty-ok", result.stdout)
        finally:
            broker.stop()

    def test_durable_process_restart_preserves_metadata_and_decreases_expiry(self):
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
        spec = importlib.util.spec_from_file_location("m1_recovery_broker", ROOT / "tests/integration/run.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        broker = helper.Broker()
        seen = threading.Event()
        killed = threading.Event()
        failures = []
        evidence = []
        first = None
        worker = None
        try:
            broker.start()
            with tempfile.TemporaryDirectory(prefix="mqtt5-recovery-") as directory, socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                listener.settimeout(8)
                port = listener.getsockname()[1]
                env = {**os.environ, "M1_OUTBOX": str(Path(directory) / "outbox.sqlite3")}
                def capture(packet):
                    self.assertEqual(packet[0] & 0xf6, 0x32)
                    offset = body_offset(packet)
                    topic_length = int.from_bytes(packet[offset:offset + 2], "big")
                    prop_offset = offset + 2 + topic_length + 2
                    props = Properties(PacketTypes.PUBLISH)
                    _, consumed = props.unpack(packet[prop_offset:])
                    self.assertEqual(packet[prop_offset + consumed:], b"native5")
                    self.assertEqual(props.ResponseTopic, "m1/reply")
                    self.assertEqual(props.CorrelationData, b"\x00\xff")
                    self.assertEqual(props.UserProperty, [("origin", "native"), ("origin", "ordered")])
                    evidence.append((publish_id(packet), bool(packet[0] & 8), props.MessageExpiryInterval))
                def proxy():
                    try:
                        for attempt in range(2):
                            with listener.accept()[0] as frontend, socket.create_connection(("127.0.0.1", broker.port), timeout=4) as backend:
                                frontend.settimeout(4)
                                backend.settimeout(4)
                                backend.sendall(recv_packet(frontend))
                                response = recv_packet(backend)
                                self.assertEqual(response[0], 0x20)
                                self.assertEqual(response[body_offset(response)] & 1, attempt)
                                frontend.sendall(response)
                                publication = recv_packet(frontend)
                                capture(publication)
                                backend.sendall(publication)
                                response = recv_packet(backend)
                                self.assertEqual(response[0], 0x40)
                                if attempt == 0:
                                    # Real broker accepted the publication; the
                                    # ordinary client binary never receives ACK.
                                    seen.set()
                                    self.assertTrue(killed.wait(5))
                                else:
                                    frontend.sendall(response)
                                    ending = recv_packet(frontend)
                                    self.assertEqual(ending[0], 0xe0)
                                    backend.sendall(ending)
                    except BaseException as error:
                        failures.append(error)
                        seen.set()
                worker = threading.Thread(target=proxy, daemon=True)
                worker.start()
                first = subprocess.Popen([str(self.driver), "durable", str(port)], cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertTrue(seen.wait(8), "durable5 first PUBLISH missing")
                self.assertEqual(failures, [])
                first.kill()
                first.communicate(timeout=5)
                killed.set()
                time.sleep(1.2)
                result = subprocess.run([str(self.driver), "durable", str(port)], cwd=ROOT, env=env, text=True, capture_output=True, timeout=12)
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(failures, [], (failures, result.stdout, result.stderr))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("durable-acknowledged", result.stdout)
                self.assertEqual(len(evidence), 2)
                self.assertEqual(evidence[0][0], evidence[1][0])
                self.assertFalse(evidence[0][1])
                self.assertTrue(evidence[1][1])
                self.assertGreater(evidence[1][2], 0)
                self.assertLess(evidence[1][2], evidence[0][2], "restart extended the immutable expiry")
        finally:
            if first and first.poll() is None:
                first.kill()
                first.communicate(timeout=5)
            killed.set()
            if worker:
                worker.join(5)
            broker.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
