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
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import paho.mqtt.client as paho


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from harness import free_port, stop_process, retain_log
LOCAL_BROKER = ROOT / ".tools/mosquitto"
BROKER = Path(os.environ.get("MOSQUITTO", shutil.which("mosquitto") or
                            (str(LOCAL_BROKER) if LOCAL_BROKER.is_file() else "mosquitto")))
MOON = Path(os.environ.get("MOON", ROOT / "scripts/moon.sh"))


def openssl(*args: str) -> None:
    subprocess.run(
        ["openssl", *args], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def sign_expired_cert(directory: Path, name: str, ca: str) -> None:
    """Issue an already-expired leaf. OpenSSL 3.0 `x509 -req` has no -not_before."""
    work = Path(tempfile.mkdtemp(prefix="moon-mqtt-ca-"))
    try:
        (work / "newcerts").mkdir()
        (work / "index.txt").write_text("")
        (work / "serial").write_text("01\n")
        (work / "ca.cnf").write_text(
            "[ ca ]\n"
            "default_ca = CA_default\n"
            "[ CA_default ]\n"
            f"dir = {work}\n"
            "database = $dir/index.txt\n"
            "serial = $dir/serial\n"
            "new_certs_dir = $dir/newcerts\n"
            "default_md = sha256\n"
            "policy = policy_any\n"
            "x509_extensions = usr_cert\n"
            "unique_subject = no\n"
            "email_in_dn = no\n"
            "[ policy_any ]\n"
            "commonName = supplied\n"
            "[ usr_cert ]\n"
            "basicConstraints = CA:FALSE\n"
            "extendedKeyUsage = clientAuth\n"
        )
        subprocess.run(
            [
                "openssl", "ca", "-batch", "-notext",
                "-config", str(work / "ca.cnf"),
                "-cert", str(directory / f"{ca}.pem"),
                "-keyfile", str(directory / f"{ca}.key"),
                "-in", str(directory / f"{name}.csr"),
                "-out", str(directory / f"{name}.pem"),
                "-startdate", "20010101000000Z",
                "-enddate", "20010102000000Z",
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        still_valid = subprocess.run(
            ["openssl", "x509", "-in", str(directory / f"{name}.pem"), "-checkend", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if still_valid.returncode == 0:
            raise RuntimeError(f"{name}.pem is not expired")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def write_pki(directory: Path, *, mtls: bool = False) -> None:
    """Write a throwaway PKI into `directory`. Server CA and client CA are separate."""
    for name, cn in (("ca", "moon-mqtt-test-ca"), ("unrelated-ca", "unrelated-test-ca")):
        openssl(
            "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(directory / f"{name}.key"),
            "-out", str(directory / f"{name}.pem"), "-days", "1",
            "-subj", f"/CN={cn}",
            "-addext", "basicConstraints=critical,CA:TRUE",
        )
    openssl(
        "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(directory / "server.key"),
        "-out", str(directory / "server.csr"), "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost",
    )
    (directory / "server.ext").write_text("subjectAltName=DNS:localhost\n")
    openssl(
        "x509", "-req", "-in", str(directory / "server.csr"),
        "-CA", str(directory / "ca.pem"), "-CAkey", str(directory / "ca.key"),
        "-CAcreateserial", "-out", str(directory / "server.pem"), "-days", "1",
        "-extfile", str(directory / "server.ext"),
    )
    if not mtls:
        return
    openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(directory / "client-ca.key"),
        "-out", str(directory / "client-ca.pem"), "-days", "1",
        "-subj", "/CN=moon-mqtt-client-ca",
        "-addext", "basicConstraints=critical,CA:TRUE",
    )
    openssl(
        "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(directory / "client-int.key"),
        "-out", str(directory / "client-int.csr"), "-subj", "/CN=moon-mqtt-client-int",
    )
    (directory / "client-int.ext").write_text(
        "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n"
    )
    openssl(
        "x509", "-req", "-in", str(directory / "client-int.csr"),
        "-CA", str(directory / "client-ca.pem"), "-CAkey", str(directory / "client-ca.key"),
        "-CAcreateserial", "-out", str(directory / "client-int.pem"), "-days", "1",
        "-extfile", str(directory / "client-int.ext"),
    )

    def sign_leaf(name: str, cn: str, ca: str) -> None:
        openssl(
            "req", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(directory / f"{name}.key"),
            "-out", str(directory / f"{name}.csr"), "-subj", f"/CN={cn}",
        )
        openssl(
            "x509", "-req", "-in", str(directory / f"{name}.csr"),
            "-CA", str(directory / f"{ca}.pem"), "-CAkey", str(directory / f"{ca}.key"),
            "-CAcreateserial", "-out", str(directory / f"{name}.pem"), "-days", "1",
        )

    for name in ("client", "paho-client", "device-a", "device-b"):
        sign_leaf(name, name, "client-ca")
    sign_leaf("client-unrelated", "client-unrelated", "unrelated-ca")
    sign_leaf("client-via-int", "client-via-int", "client-int")
    (directory / "client-chain.pem").write_bytes(
        (directory / "client-via-int.pem").read_bytes()
        + (directory / "client-int.pem").read_bytes()
    )
    openssl(
        "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(directory / "client-expired.key"),
        "-out", str(directory / "client-expired.csr"), "-subj", "/CN=client-expired",
    )
    sign_expired_cert(directory, "client-expired", "client-ca")
    openssl("genrsa", "-out", str(directory / "client-mismatch.key"), "2048")
    openssl(
        "pkcs8", "-topk8", "-in", str(directory / "client.key"),
        "-out", str(directory / "client-encrypted.key"),
        "-v2", "aes-256-cbc", "-passout", "pass:secret",
    )
    (directory / "corrupt.pem").write_text("this is not a pem certificate\n")


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
    def __init__(self, *, tls: bool = False, mtls: bool = False,
                 cert_acl: bool = False, authenticated: bool = False,
                 tls_version: str | None = None, port: int | None = None):
        self.port = port or free_port()
        self.mtls = mtls or cert_acl
        self.tls = tls or self.mtls
        self.cert_acl = cert_acl
        self.authenticated = authenticated
        self.tls_version = tls_version
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
            if self.tls_version:
                lines.append(f"tls_version {self.tls_version}")
            if self.mtls:
                lines += [
                    f"cafile {self.temp / 'client-ca.pem'}",
                    "require_certificate true",
                ]
                if self.cert_acl:
                    acl_file = self.temp / "cert.acl"
                    acl_file.write_text(
                        "user device-a\n"
                        "topic readwrite device-a/#\n"
                        "user device-b\n"
                        "topic readwrite device-b/#\n"
                    )
                    acl_file.chmod(0o600)
                    lines += [
                        "use_identity_as_username true",
                        f"acl_file {acl_file}",
                    ]
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
        write_pki(self.temp, mtls=self.mtls)

    def stop(self) -> None:
        stop_process(self.process)
        if hasattr(self, "log") and not self.log.closed:
            self.log.close()

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self, failed: bool = False) -> None:
        self.stop()
        retain_log(self.temp / "broker.log", "mosquitto")
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
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

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

    def drain_events(self) -> list[dict]:
        items = []
        while True:
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                return items

    def finish(self, timeout: float = 10.0) -> tuple[int, str]:
        try:
            code = self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait()
            raise AssertionError(f"driver timed out; stderr={self.stderr()!r}")
        self._reader.join(timeout=2)
        stderr = self.stderr()
        for stream in (self.process.stdin, self.stdout, self.process.stderr):
            if stream:
                stream.close()
        return code, stderr

    def client_error_type(self) -> str | None:
        for item in self.drain_events():
            if item.get("event") == "client_error":
                return item.get("type")
        return None

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
    def run_broker(self, *, tls: bool = False, mtls: bool = False,
                   cert_acl: bool = False, authenticated: bool = False,
                   tls_version: str | None = None) -> Broker:
        broker = Broker(tls=tls, mtls=mtls, cert_acl=cert_acl,
                        authenticated=authenticated, tls_version=tls_version)
        broker.start()
        self.addCleanup(broker.close)
        return broker

    def mtls_env(self, broker: Broker, cert: str = "client", **extra: str) -> dict[str, str]:
        return {
            "host": "localhost",
            "MQTT_TEST_CA": str(broker.temp / "ca.pem"),
            "MQTT_TEST_CERT": str(broker.temp / f"{cert}.pem"),
            "MQTT_TEST_KEY": str(broker.temp / f"{cert}.key"),
            **extra,
        }

    def assert_mtls_rejected(self, broker: Broker, error: str | None = None,
                             **env: str) -> None:
        driver = Driver("tls", broker.port, **self.mtls_env(broker, **env))
        self.addCleanup(driver.close)
        code, stderr = driver.finish()
        self.assertNotEqual(code, 0, stderr)
        if error is not None:
            self.assertEqual(
                driver.client_error_type(), error,
                f"expected {error}; stderr={stderr}",
            )

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
            code, stderr = bad.finish()
            self.assertNotEqual(code, 0, f"TLS unexpectedly accepted host={host}, ca={ca}")
            self.assertEqual(
                bad.client_error_type(), "TlsFailure",
                f"host={host} ca={ca} stderr={stderr}",
            )

    def test_mtls_qos1_roundtrip(self) -> None:
        broker = self.run_broker(mtls=True)
        received: list[tuple[str, bytes, int]] = []
        ready = threading.Event()
        oracle = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-mtls-oracle")
        oracle.tls_set(
            ca_certs=str(broker.temp / "ca.pem"),
            certfile=str(broker.temp / "paho-client.pem"),
            keyfile=str(broker.temp / "paho-client.key"),
        )
        oracle.on_connect = lambda c, u, f, r, p: c.subscribe("it/from-moon/mtls", 1)
        oracle.on_subscribe = lambda *args: ready.set()
        oracle.on_message = lambda c, u, m: received.append((m.topic, m.payload, m.qos))
        oracle.connect("localhost", broker.port)
        oracle.loop_start()
        self.addCleanup(lambda: (oracle.disconnect(), oracle.loop_stop()))
        self.assertTrue(ready.wait(3))
        driver = Driver(
            "mtls", broker.port, host="localhost",
            MQTT_TEST_CA=str(broker.temp / "ca.pem"),
            MQTT_TEST_CERT=str(broker.temp / "client.pem"),
            MQTT_TEST_KEY=str(broker.temp / "client.key"),
        )
        self.addCleanup(driver.close)
        driver.expect("subscribed")
        oracle.publish("it/to-moon/mtls", b"paho-mtls", qos=1).wait_for_publish(3)
        driver.expect("received_mtls")
        code, stderr = driver.finish()
        self.assertEqual(code, 0, stderr)
        end = time.monotonic() + 3
        while not received and time.monotonic() < end:
            time.sleep(.025)
        self.assertEqual(received, [("it/from-moon/mtls", b"moon-mtls", 1)])

        missing = Driver(
            "tls", broker.port, host="localhost",
            MQTT_TEST_CA=str(broker.temp / "ca.pem"),
        )
        self.addCleanup(missing.close)
        self.assertNotEqual(missing.finish()[0], 0, "broker accepted a client without a certificate")

    def test_mtls_intermediate_chain(self) -> None:
        broker = self.run_broker(mtls=True)
        driver = Driver(
            "tls", broker.port,
            **self.mtls_env(broker, cert="client-via-int",
                            MQTT_TEST_CERT=str(broker.temp / "client-chain.pem"),
                            MQTT_TEST_KEY=str(broker.temp / "client-via-int.key")),
        )
        self.addCleanup(driver.close)
        driver.expect("connected")
        self.assertEqual(driver.finish()[0], 0)

    def test_mtls_rejection_matrix(self) -> None:
        broker = self.run_broker(mtls=True)
        self.assert_mtls_rejected(
            broker, cert="client-via-int",
            MQTT_TEST_CERT=str(broker.temp / "client-via-int.pem"),
            MQTT_TEST_KEY=str(broker.temp / "client-via-int.key"),
        )
        self.assert_mtls_rejected(broker, cert="client-unrelated")
        self.assert_mtls_rejected(broker, cert="client-expired")
        self.assert_mtls_rejected(
            broker, MQTT_TEST_CERT=str(broker.temp / "client.pem"),
            MQTT_TEST_KEY=str(broker.temp / "client-mismatch.key"),
            error="InvalidConfig",
        )
        self.assert_mtls_rejected(
            broker, MQTT_TEST_CERT=str(broker.temp / "corrupt.pem"),
            MQTT_TEST_KEY=str(broker.temp / "client.key"),
            error="InvalidConfig",
        )
        self.assert_mtls_rejected(
            broker, MQTT_TEST_CERT=str(broker.temp / "client.pem"),
            MQTT_TEST_KEY=str(broker.temp / "client-encrypted.key"),
            error="InvalidConfig",
        )
        self.assert_mtls_rejected(
            broker, MQTT_TEST_CA=str(broker.temp / "unrelated-ca.pem"),
            error="TlsFailure",
        )
        self.assert_mtls_rejected(broker, host="127.0.0.1", error="TlsFailure")
        self.assert_mtls_rejected(
            broker, MQTT_TEST_KEY=str(broker.temp / "no-such-key.pem"),
            error="InvalidConfig",
        )

    def test_mtls_tls13_only_qos1_roundtrip(self) -> None:
        broker = self.run_broker(mtls=True, tls_version="tlsv1.3")
        received: list[tuple[str, bytes, int]] = []
        ready = threading.Event()
        oracle = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-tls13")
        oracle.tls_set(
            ca_certs=str(broker.temp / "ca.pem"),
            certfile=str(broker.temp / "paho-client.pem"),
            keyfile=str(broker.temp / "paho-client.key"),
        )
        oracle.on_connect = lambda c, u, f, r, p: c.subscribe("it/from-moon/mtls", 1)
        oracle.on_subscribe = lambda *args: ready.set()
        oracle.on_message = lambda c, u, m: received.append((m.topic, m.payload, m.qos))
        oracle.connect("localhost", broker.port)
        oracle.loop_start()
        self.addCleanup(lambda: (oracle.disconnect(), oracle.loop_stop()))
        self.assertTrue(ready.wait(3))
        driver = Driver("mtls", broker.port, **self.mtls_env(broker))
        self.addCleanup(driver.close)
        driver.expect("subscribed")
        oracle.publish("it/to-moon/mtls", b"paho-mtls", qos=1).wait_for_publish(3)
        driver.expect("received_mtls")
        self.assertEqual(driver.finish()[0], 0)
        end = time.monotonic() + 3
        while not received and time.monotonic() < end:
            time.sleep(.025)
        self.assertEqual(received, [("it/from-moon/mtls", b"moon-mtls", 1)])
        self.assert_mtls_rejected(broker, cert="client-unrelated", error="TlsFailure")

    def test_mtls_tls13_only_identity_rejection_error_types(self) -> None:
        """TLS 1.3 rejects a client identity after the local handshake returns.

        The server can only announce the rejection on the first MQTT read or
        write, so the public error must still be the TLS category instead of the
        generic ProtocolError fallback.
        """
        broker = self.run_broker(mtls=True, tls_version="tlsv1.3")
        driver = Driver(
            "tls", broker.port, host="localhost",
            MQTT_TEST_CA=str(broker.temp / "ca.pem"),
        )
        self.addCleanup(driver.close)
        code, stderr = driver.finish()
        self.assertNotEqual(code, 0, stderr)
        self.assertEqual(driver.client_error_type(), "TlsFailure", stderr)
        self.assert_mtls_rejected(broker, cert="client-unrelated", error="TlsFailure")
        self.assert_mtls_rejected(broker, cert="client-expired", error="TlsFailure")

    def test_tls_clean_close_without_alert_is_tls_failure(self) -> None:
        """A clean TLS transport close must keep the public TlsFailure category.

        A TLS 1.3 peer can reject the client after its local handshake returned
        and signal it only by shutting the transport down (close_notify, no
        fatal alert). That used to surface as the generic ProtocolError fallback
        via a closed reader rather than as a TLS failure.
        """
        certs = Path(tempfile.mkdtemp(prefix="moon-mqtt-clean-close-"))
        self.addCleanup(shutil.rmtree, certs, True)
        write_pki(certs)
        port = free_port()
        listening = threading.Event()

        def serve() -> None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.load_cert_chain(certs / "server.pem", certs / "server.key")
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", port))
                listener.listen(1)
                listening.set()
                connection, _ = listener.accept()
                with context.wrap_socket(connection, server_side=True) as tls:
                    try:
                        tls.recv(1)
                    except OSError:
                        pass
                    # Send close_notify. The peer need not answer, so a short
                    # read timeout keeps this helper from hanging.
                    tls.settimeout(0.5)
                    try:
                        tls.unwrap()
                    except (OSError, ssl.SSLError):
                        pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.assertTrue(listening.wait(3), "TLS helper server did not start")
        driver = Driver(
            "tls", port, host="localhost", MQTT_TEST_CA=str(certs / "ca.pem"),
        )
        self.addCleanup(driver.close)
        code, stderr = driver.finish()
        self.assertNotEqual(code, 0, stderr)
        self.assertEqual(driver.client_error_type(), "TlsFailure", stderr)

    def test_mtls_concurrent_identities(self) -> None:
        broker = self.run_broker(mtls=True)
        alice = Driver(
            "tls", broker.port,
            **self.mtls_env(broker, cert="device-a", MQTT_TEST_CLIENT_ID="device-a"),
        )
        bob = Driver(
            "tls", broker.port,
            **self.mtls_env(broker, cert="device-b", MQTT_TEST_CLIENT_ID="device-b"),
        )
        self.addCleanup(alice.close)
        self.addCleanup(bob.close)
        alice.expect("connected")
        bob.expect("connected")
        self.assertEqual(alice.finish()[0], 0)
        self.assertEqual(bob.finish()[0], 0)

    def test_mtls_in_process_dual_identity(self) -> None:
        broker = self.run_broker(mtls=True)
        driver = Driver(
            "mtls_dual", broker.port, host="localhost",
            MQTT_TEST_CA=str(broker.temp / "ca.pem"),
            MQTT_TEST_CERT_A=str(broker.temp / "device-a.pem"),
            MQTT_TEST_KEY_A=str(broker.temp / "device-a.key"),
            MQTT_TEST_CERT_B=str(broker.temp / "device-b.pem"),
            MQTT_TEST_KEY_B=str(broker.temp / "device-b.key"),
        )
        self.addCleanup(driver.close)
        driver.expect("dual_subscribed")
        driver.expect("dual_exchanged")
        self.assertEqual(driver.finish()[0], 0)

    def test_mtls_cert_acl_isolation(self) -> None:
        broker = self.run_broker(cert_acl=True)
        holder = Driver(
            "mtls_acl_listener", broker.port,
            **self.mtls_env(
                broker, cert="device-b", MQTT_TEST_CLIENT_ID="device-b",
                MQTT_TEST_SUB_TOPIC="device-b/#",
                MQTT_TEST_PUB_TOPIC="device-b/own",
                MQTT_TEST_PAYLOAD="only-b",
            ),
        )
        self.addCleanup(holder.close)
        holder.expect("acl_own_received")
        intruder = Driver(
            "mtls_acl_intruder", broker.port,
            **self.mtls_env(
                broker, cert="device-a", MQTT_TEST_CLIENT_ID="device-a",
                MQTT_TEST_PUB_TOPIC="device-b/secret",
                MQTT_TEST_PAYLOAD="from-a",
            ),
        )
        self.addCleanup(intruder.close)
        intruder.expect("acl_intruder_published")
        self.assertEqual(intruder.finish()[0], 0)
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            try:
                item = holder.events.get(timeout=0.1)
            except queue.Empty:
                continue
            self.assertNotEqual(item.get("event"), "acl_foreign", item)
            self.assertNotEqual(item.get("event"), "client_error", item)

    def test_mtls_reconnect_and_resubscribe(self) -> None:
        broker = self.run_broker(mtls=True)
        driver = Driver("reconnect", broker.port, **self.mtls_env(broker))
        self.addCleanup(driver.close)
        first = driver.expect("connected")
        self.assertEqual(first.get("generation"), 1)
        broker.stop()
        driver.expect("disconnected")
        broker.start()
        second = driver.expect("connected", 12)
        self.assertGreater(second.get("generation", 0), 1)
        publisher = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-mtls-reconnect")
        publisher.tls_set(
            ca_certs=str(broker.temp / "ca.pem"),
            certfile=str(broker.temp / "paho-client.pem"),
            keyfile=str(broker.temp / "paho-client.key"),
        )
        publisher.connect("localhost", broker.port)
        publisher.loop_start()
        publisher.publish("it/reconnect", b"after-restart", qos=1).wait_for_publish(3)
        publisher.disconnect()
        publisher.loop_stop()
        driver.expect("message_after_reconnect")
        self.assertEqual(driver.finish()[0], 0)

    def test_mtls_handshake_timeout_then_new_client(self) -> None:
        broker = self.run_broker(mtls=True)
        stall = socket.socket()
        stall.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        stall.bind(("127.0.0.1", 0))
        stall.listen()
        stall_port = stall.getsockname()[1]
        held: list[socket.socket] = []

        def accept() -> None:
            try:
                conn, _ = stall.accept()
                held.append(conn)
                time.sleep(8)
            except OSError:
                return

        threading.Thread(target=accept, daemon=True).start()
        def close_stall() -> None:
            stall.close()
            for conn in held:
                try:
                    conn.close()
                except OSError:
                    pass
        self.addCleanup(close_stall)
        timed_out = Driver(
            "tls", stall_port, **self.mtls_env(
                broker, MQTT_TEST_CONNECT_TIMEOUT_MS="800",
            ),
        )
        self.addCleanup(timed_out.close)
        started = time.monotonic()
        code, _ = timed_out.finish()
        self.assertNotEqual(code, 0)
        self.assertLess(time.monotonic() - started, 4)
        fresh = Driver("tls", broker.port, **self.mtls_env(broker))
        self.addCleanup(fresh.close)
        fresh.expect("connected")
        self.assertEqual(fresh.finish()[0], 0)

    def test_mtls_repeat_connections(self) -> None:
        broker = self.run_broker(mtls=True)
        driver = Driver("mtls_repeat", broker.port, **self.mtls_env(broker))
        self.addCleanup(driver.close)
        driver.expect("mtls_repeat_done", 20)
        self.assertEqual(driver.finish()[0], 0)

    def test_cli_identity_flags(self) -> None:
        def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(MOON), "run", "examples/mqtt_demo/cli", "--target", "native",
                 "--", *args],
                cwd=ROOT, text=True, capture_output=True, timeout=20,
            )

        cert_only = run_cli("publish", "-t", "lab/x", "--cert", "a.pem")
        self.assertNotEqual(cert_only.returncode, 0)
        self.assertIn("--cert requires --key", cert_only.stderr + cert_only.stdout)
        key_only = run_cli("publish", "-t", "lab/x", "--key", "a.pem")
        self.assertNotEqual(key_only.returncode, 0)
        self.assertIn("--key requires --cert", key_only.stderr + key_only.stdout)
        no_tls = run_cli("publish", "-t", "lab/x", "--cert", "a.pem", "--key", "b.pem")
        self.assertNotEqual(no_tls.returncode, 0)
        self.assertIn("--cert/--key require --tls", no_tls.stderr + no_tls.stdout)
        broker = self.run_broker(mtls=True)
        encrypted = run_cli(
            "publish", "-t", "lab/x", "-m", "hi", "--tls", "--qos", "1",
            "--host", "localhost", "--port", str(broker.port),
            "--ca", str(broker.temp / "ca.pem"),
            "--cert", str(broker.temp / "client.pem"),
            "--key", str(broker.temp / "client-encrypted.key"),
        )
        self.assertNotEqual(encrypted.returncode, 0)

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
