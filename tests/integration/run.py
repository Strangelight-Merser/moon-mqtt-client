#!/usr/bin/env python3
"""Black-box Mosquitto/Paho integration tests for moon-mqtt-client.

The MoonBit test driver prints one JSON object per line.  Assertions in this
file are deliberately based on broker traffic, Paho observations, process exit
status, and timings rather than a self-reported "PASS" string.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

import paho.mqtt.client as paho


ROOT = Path(__file__).resolve().parents[2]
LOCAL_BROKER = ROOT / ".tools/mosquitto"
BROKER = Path(os.environ.get("MOSQUITTO", shutil.which("mosquitto") or
                            (str(LOCAL_BROKER) if LOCAL_BROKER.is_file() else "mosquitto")))
MOON = Path(os.environ.get("MOON", ROOT / "scripts/moon.sh"))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port: int, deadline: float = 3.0) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return
        except OSError:
            time.sleep(.025)
    raise TimeoutError(f"broker on port {port} did not become ready")


class Broker:
    def __init__(self, *, tls: bool = False, authenticated: bool = False,
                 port: int | None = None):
        self.port = port or free_port()
        self.tls = tls
        self.authenticated = authenticated
        self.temp = Path(tempfile.mkdtemp(prefix="moon-mqtt-it-"))
        self.process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        lines = [f"listener {self.port} 127.0.0.1", "log_type all"]
        if self.authenticated:
            password_file = self.temp / "passwords"
            acl_file = self.temp / "acl"
            # Fixed PBKDF2-SHA512 fixture generated in Mosquitto's documented
            # password-file format. The cleartext is deliberately test-only.
            password_file.write_text(
                "moon:$7$101$MDEyMzQ1Njc4OWFi$"
                "ce5rBZuUHDrLzLiwk/47IcBv56gfmNa6DRRAD6lCasNKttQaV4jhuIOCvmgPyR0I4MEcJTS3Cb981Vdi6rNwWQ==\n"
            )
            acl_file.write_text("user moon\ntopic readwrite allowed/#\n")
            password_file.chmod(0o600)
            acl_file.chmod(0o600)
            lines += ["allow_anonymous false", f"password_file {password_file}", f"acl_file {acl_file}"]
        else:
            lines.append("allow_anonymous true")
        if self.tls:
            if not (self.temp / "ca.pem").exists():
                self._certificates()
            lines += [f"certfile {self.temp / 'server.pem'}", f"keyfile {self.temp / 'server.key'}"]
        (self.temp / "mosquitto.conf").write_text("\n".join(lines) + "\n")
        self.log = open(self.temp / "broker.log", "a", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(BROKER), "-c", str(self.temp / "mosquitto.conf"), "-v"],
            stdout=self.log, stderr=subprocess.STDOUT, text=True,
        )
        try:
            wait_port(self.port)
        except BaseException:
            self.stop()
            raise

    def _certificates(self) -> None:
        for name, cn in (("ca", "moon-mqtt-test-ca"), ("unrelated-ca", "unrelated-test-ca")):
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(self.temp / f"{name}.key"),
                "-out", str(self.temp / f"{name}.pem"), "-days", "1",
                "-subj", f"/CN={cn}",
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([
            "openssl", "req", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(self.temp / "server.key"),
            "-out", str(self.temp / "server.csr"), "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.temp / "server.ext").write_text("subjectAltName=DNS:localhost\n")
        subprocess.run([
            "openssl", "x509", "-req", "-in", str(self.temp / "server.csr"),
            "-CA", str(self.temp / "ca.pem"), "-CAkey", str(self.temp / "ca.key"),
            "-CAcreateserial", "-out", str(self.temp / "server.pem"), "-days", "1",
            "-extfile", str(self.temp / "server.ext"),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if hasattr(self, "log") and not self.log.closed:
            self.log.close()

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self, failed: bool = False) -> None:
        self.stop()
        if failed or os.environ.get("MQTT_KEEP_TEST_ARTIFACTS") == "1":
            print(f"broker evidence retained at {self.temp}", flush=True)
        else:
            shutil.rmtree(self.temp)


class PacketProxy:
    """Packet-aware proxy used to fragment replies or discard one PUBACK."""
    def __init__(self, target_port: int, mode: str):
        self.port = free_port()
        self.target_port = target_port
        self.mode = mode
        self.dropped_puback = threading.Event()
        self.stop_event = threading.Event()
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", self.port))
        self.listener.listen()
        self.threads: list[threading.Thread] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self.stop_event.is_set():
            try:
                source, _ = self.listener.accept()
                target = socket.create_connection(("127.0.0.1", self.target_port))
            except OSError:
                return
            for args in ((source, target, False), (target, source, True)):
                thread = threading.Thread(target=self._copy, args=args, daemon=True)
                self.threads.append(thread)
                thread.start()

    @staticmethod
    def _packet_end(buf: bytearray) -> int | None:
        remaining, multiplier, pos = 0, 1, 1
        while pos < len(buf):
            digit = buf[pos]
            remaining += (digit & 127) * multiplier
            pos += 1
            if digit < 128:
                return pos + remaining if len(buf) >= pos + remaining else None
            multiplier *= 128
            if multiplier > 128 ** 3:
                raise ValueError("malformed MQTT remaining length")
        return None

    def _copy(self, source: socket.socket, target: socket.socket, from_broker: bool) -> None:
        buf = bytearray()
        try:
            while not self.stop_event.is_set():
                chunk = source.recv(65536)
                if not chunk:
                    return
                if not from_broker:
                    target.sendall(chunk)
                    continue
                buf.extend(chunk)
                while (end := self._packet_end(buf)) is not None:
                    packet = bytes(buf[:end]); del buf[:end]
                    packet_type = packet[0] >> 4
                    if self.mode == "drop_puback" and packet_type == 4:
                        self.dropped_puback.set()
                        source.shutdown(socket.SHUT_RDWR)
                        target.shutdown(socket.SHUT_RDWR)
                        return
                    if self.mode == "drop_pingresp" and packet_type == 13:
                        continue
                    if self.mode == "fragment":
                        for byte in packet:
                            target.sendall(bytes((byte,)))
                    else:
                        target.sendall(packet)
        except OSError:
            return
        finally:
            source.close(); target.close()

    def close(self) -> None:
        self.stop_event.set()
        self.listener.close()


class Driver:
    def __init__(self, scenario: str, port: int, **env: str):
        child_env = {**os.environ, "MQTT_TEST_SCENARIO": scenario,
                     "MQTT_TEST_HOST": env.pop("host", "127.0.0.1"),
                     "MQTT_TEST_PORT": str(port), **env}
        # The driver writes each JSON event through moonbitlang/async/stdio's
        # unbuffered stdout, so a plain pipe is enough. A pty is deliberately not
        # used: it hides output buffering bugs and is unavailable in sandboxes
        # that block /dev/ptmx.
        self.process = subprocess.Popen(
            [str(MOON), "run", "examples/test_driver"], cwd=ROOT, env=child_env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        assert self.process.stdout
        self.stdout = self.process.stdout
        self.events: queue.Queue[dict] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            for line in self.stdout:
                try:
                    self.events.put(json.loads(line))
                except json.JSONDecodeError:
                    self.events.put({"event": "non_json_stdout", "line": line.rstrip()})
        except (OSError, ValueError):
            pass

    def expect(self, event: str, timeout: float = 8.0) -> dict:
        end = time.monotonic() + timeout
        seen = []
        while time.monotonic() < end:
            try:
                item = self.events.get(timeout=max(.01, end - time.monotonic()))
            except queue.Empty:
                break
            seen.append(item)
            if item.get("event") == event:
                return item
        raise AssertionError(f"expected driver event {event!r}; saw {seen!r}; stderr={self.stderr()!r}")

    def send(self, command: str) -> None:
        assert self.process.stdin
        self.process.stdin.write(command + "\n"); self.process.stdin.flush()

    def finish(self, timeout: float = 10.0) -> tuple[int, str]:
        try:
            code = self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait()
            raise AssertionError(f"driver timed out; stderr={self.stderr()!r}")
        stderr = self.stderr()
        for stream in (self.process.stdin, self.stdout, self.process.stderr):
            if stream:
                stream.close()
        return code, stderr

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        for stream in (self.process.stdin, self.stdout, self.process.stderr):
            if stream and not stream.closed:
                stream.close()

    def stderr(self) -> str:
        return self.process.stderr.read() if self.process.poll() is not None and self.process.stderr else ""


class IntegrationTest(unittest.TestCase):
    def run_broker(self, *, tls: bool = False, authenticated: bool = False) -> Broker:
        broker = Broker(tls=tls, authenticated=authenticated); broker.start()
        self.addCleanup(broker.close)
        return broker

    def test_tcp_qos0_qos1_and_subscription(self) -> None:
        broker = self.run_broker()
        received: list[tuple[str, bytes, int]] = []
        ready = threading.Event()
        oracle = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-tcp-oracle")
        oracle.on_connect = lambda c, u, f, r, p: c.subscribe("it/from-moon/#", 1)
        oracle.on_subscribe = lambda *args: ready.set()
        oracle.on_message = lambda c, u, m: received.append((m.topic, m.payload, m.qos))
        oracle.connect("127.0.0.1", broker.port); oracle.loop_start()
        self.addCleanup(lambda: (oracle.disconnect(), oracle.loop_stop()))
        self.assertTrue(ready.wait(3))
        driver = Driver("tcp", broker.port)
        self.addCleanup(driver.close)
        driver.expect("subscribed")
        oracle.publish("it/to-moon/qos0", b"paho-qos0", qos=0).wait_for_publish(3)
        oracle.publish("it/to-moon/qos1", b"paho-qos1", qos=1).wait_for_publish(3)
        driver.expect("received_qos0"); driver.expect("received_qos1")
        code, stderr = driver.finish()
        self.assertEqual(code, 0, stderr)
        end = time.monotonic() + 3
        while len(received) < 2 and time.monotonic() < end:
            time.sleep(.025)
        self.assertEqual(sorted(received), [("it/from-moon/qos0", b"moon-qos0", 0), ("it/from-moon/qos1", b"moon-qos1", 1)])

    def test_authenticated_broker_credentials_and_subscription_acl(self) -> None:
        broker = self.run_broker(authenticated=True)
        good = Driver(
            "auth_acl", broker.port,
            MQTT_TEST_USERNAME="moon", MQTT_TEST_PASSWORD="correct-password",
        )
        self.addCleanup(good.close)
        good.expect("auth_acl_ready")
        publisher = paho.Client(
            paho.CallbackAPIVersion.VERSION2,
            client_id="paho-authenticated-acl-oracle",
        )
        publisher.username_pw_set("moon", "correct-password")
        publisher.connect("127.0.0.1", broker.port)
        publisher.loop_start()
        try:
            publisher.publish("denied/read", b"must-not-arrive", qos=1).wait_for_publish(3)
        finally:
            publisher.disconnect()
            publisher.loop_stop()
        good.expect("acl_denied_delivery", 2)
        self.assertEqual(good.finish()[0], 0)

        bad = Driver(
            "fragment", broker.port,
            MQTT_TEST_USERNAME="moon", MQTT_TEST_PASSWORD="wrong-password",
        )
        self.addCleanup(bad.close)
        code, stderr = bad.finish()
        self.assertNotEqual(code, 0, f"broker accepted wrong credentials: {stderr}")

    def test_tls_custom_ca_and_rejections(self) -> None:
        broker = self.run_broker(tls=True)
        good = Driver("tls", broker.port, host="localhost", MQTT_TEST_CA=str(broker.temp / "ca.pem"))
        self.addCleanup(good.close)
        good.expect("connected"); self.assertEqual(good.finish()[0], 0)
        for host, ca in (("127.0.0.1", broker.temp / "ca.pem"), ("localhost", broker.temp / "unrelated-ca.pem")):
            bad = Driver("tls_reject", broker.port, host=host, MQTT_TEST_CA=str(ca))
            self.addCleanup(bad.close)
            code, _ = bad.finish()
            self.assertNotEqual(code, 0, f"TLS unexpectedly accepted host={host}, ca={ca}")

    def test_reconnect_and_resubscribe(self) -> None:
        broker = self.run_broker()
        driver = Driver("reconnect", broker.port)
        self.addCleanup(driver.close)
        first = driver.expect("connected"); self.assertEqual(first.get("generation"), 1)
        broker.stop(); driver.expect("disconnected")
        broker.start(); second = driver.expect("connected", 12)
        self.assertGreater(second.get("generation", 0), 1)
        publisher = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-after-reconnect")
        publisher.connect("127.0.0.1", broker.port); publisher.loop_start()
        publisher.publish("it/reconnect", b"after-restart", qos=1).wait_for_publish(3)
        publisher.disconnect(); publisher.loop_stop()
        driver.expect("message_after_reconnect")
        self.assertEqual(driver.finish()[0], 0)

    def test_fragmented_broker_packets(self) -> None:
        broker = self.run_broker()
        proxy = PacketProxy(broker.port, "fragment"); self.addCleanup(proxy.close)
        driver = Driver("fragment", proxy.port); self.addCleanup(driver.close)
        driver.expect("connected")
        self.assertEqual(driver.finish()[0], 0)

    def test_will_suppressed_on_disconnect_and_sent_on_abrupt_scope_end(self) -> None:
        broker = self.run_broker()
        messages: list[bytes] = []
        subscribed = threading.Event()
        oracle = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-will-oracle")
        oracle.on_connect = lambda c, u, f, r, p: c.subscribe("it/will", 1)
        oracle.on_subscribe = lambda *args: subscribed.set()
        oracle.on_message = lambda c, u, m: messages.append(m.payload)
        oracle.connect("127.0.0.1", broker.port); oracle.loop_start()
        self.addCleanup(lambda: (oracle.disconnect(), oracle.loop_stop()))
        self.assertTrue(subscribed.wait(3))
        normal = Driver("will_disconnect", broker.port); self.addCleanup(normal.close)
        normal.expect("connected"); self.assertEqual(normal.finish()[0], 0)
        time.sleep(.3)
        self.assertEqual(messages, [], "graceful DISCONNECT unexpectedly triggered Will")
        abrupt = Driver("will_abrupt", broker.port); self.addCleanup(abrupt.close)
        abrupt.expect("connected"); self.assertEqual(abrupt.finish()[0], 0)
        end = time.monotonic() + 3
        while not messages and time.monotonic() < end: time.sleep(.025)
        self.assertEqual(messages, [b"abrupt"])

    def test_reconnect_stats_count_generations(self) -> None:
        broker = self.run_broker()
        driver = Driver("reconnect", broker.port); self.addCleanup(driver.close)
        first = driver.expect("connected"); self.assertEqual(first.get("generation"), 1)
        broker.stop(); driver.expect("disconnected")
        broker.start(); second = driver.expect("connected", 12)
        self.assertGreater(second.get("generation", 0), 1)
        publisher = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-reconnect-stats")
        publisher.connect("127.0.0.1", broker.port); publisher.loop_start()
        publisher.publish("it/reconnect", b"after-restart", qos=1).wait_for_publish(3)
        publisher.disconnect(); publisher.loop_stop()
        outcome = driver.expect("message_after_reconnect")
        self.assertGreaterEqual(outcome.get("generation", 0), 2, outcome)
        self.assertGreaterEqual(outcome.get("reconnects", 0), 1, outcome)
        self.assertGreaterEqual(outcome.get("disconnects", 0), 1, outcome)
        self.assertEqual(outcome.get("pending"), 0, outcome)
        self.assertEqual(driver.finish()[0], 0)

    def test_silent_broker_causes_heartbeat_disconnect(self) -> None:
        broker = self.run_broker()
        proxy = PacketProxy(broker.port, "drop_pingresp"); self.addCleanup(proxy.close)
        driver = Driver("heartbeat", proxy.port, MQTT_TEST_OPERATION_TIMEOUT_MS="500")
        self.addCleanup(driver.close)
        driver.expect("connected")
        driver.expect("disconnected", 10)
        self.assertEqual(driver.finish()[0], 0)

    def test_retained_delivery_and_zero_byte_clear(self) -> None:
        broker = self.run_broker()
        setter = Driver("retained_set", broker.port); self.addCleanup(setter.close)
        setter.expect("retained_set"); self.assertEqual(setter.finish()[0], 0)

        def fresh_subscription(client_id: str, window: float = 1.0) -> list[tuple[bytes, bool]]:
            messages: list[tuple[bytes, bool]] = []
            subscribed = threading.Event()
            client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=client_id)
            client.on_connect = lambda c, u, f, r, p: c.subscribe("it/retained", 1)
            client.on_subscribe = lambda *args: subscribed.set()
            client.on_message = lambda c, u, m: messages.append((m.payload, m.retain))
            client.connect("127.0.0.1", broker.port); client.loop_start()
            try:
                self.assertTrue(subscribed.wait(3))
                end = time.monotonic() + window
                while not messages and time.monotonic() < end: time.sleep(.025)
                return messages
            finally:
                client.disconnect(); client.loop_stop()

        self.assertEqual(fresh_subscription("paho-retained-before-clear"), [(b"retained-value", True)])
        clearer = Driver("retained_clear", broker.port); self.addCleanup(clearer.close)
        clearer.expect("retained_cleared"); self.assertEqual(clearer.finish()[0], 0)
        self.assertEqual(fresh_subscription("paho-retained-after-clear", .6), [])

    def test_unsubscribe_ack_then_no_further_delivery(self) -> None:
        broker = self.run_broker()
        driver = Driver("unsubscribe", broker.port); self.addCleanup(driver.close)
        driver.expect("unsubscribed")
        publisher = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-unsubscribe-publisher")
        publisher.connect("127.0.0.1", broker.port); publisher.loop_start()
        try:
            publisher.publish("it/unsubscribe", b"must-not-arrive", qos=1).wait_for_publish(3)
        finally:
            publisher.disconnect(); publisher.loop_stop()
        driver.expect("no_message_after_unsubscribe", 2)
        self.assertEqual(driver.finish()[0], 0)

    def test_stats_snapshot_reports_live_connection(self) -> None:
        broker = self.run_broker()
        driver = Driver("stats", broker.port); self.addCleanup(driver.close)
        snapshot = driver.expect("stats")
        self.assertEqual(snapshot.get("generation"), 1, snapshot)
        self.assertEqual(snapshot.get("pending"), 0, snapshot)
        self.assertEqual(snapshot.get("business"), 0, snapshot)
        self.assertEqual(snapshot.get("control"), 0, snapshot)
        self.assertEqual(snapshot.get("reconnects"), 0, snapshot)
        self.assertEqual(snapshot.get("disconnects"), 0, snapshot)
        self.assertEqual(snapshot.get("unknown"), 0, snapshot)
        self.assertEqual(driver.finish()[0], 0)

    def test_lost_puback_never_reports_success(self) -> None:
        broker = self.run_broker()
        proxy = PacketProxy(broker.port, "drop_puback"); self.addCleanup(proxy.close)
        driver = Driver("lost_puback", proxy.port, MQTT_TEST_OPERATION_TIMEOUT_MS="700")
        self.addCleanup(driver.close)
        self.assertTrue(proxy.dropped_puback.wait(5), "proxy did not observe a PUBACK")
        outcome = driver.expect("publish_outcome", 5)
        self.assertNotEqual(outcome.get("outcome"), "success", outcome)
        self.assertEqual(driver.finish()[0], 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("tests", nargs="*")
    args, rest = parser.parse_known_args()
    unittest.main(argv=[__file__, *args.tests, *rest], verbosity=2)
