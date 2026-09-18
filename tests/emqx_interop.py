#!/usr/bin/env python3
"""Black-box interoperability checks against a pinned official EMQX image."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import platform
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

import paho.mqtt.client as paho

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mqtt_integration", ROOT / "tests/integration/run.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

IMAGE_BY_ARCH = {
    "x86_64": "emqx/emqx:5.8.8@sha256:2bbadcd6ebdf1e2d032bdd8149c9615867ed25f32b1a09ec5bd92ddcf159e1aa",
    "arm64": "emqx/emqx:5.8.8@sha256:b07f5f1f20009c8be42b7865f37ae97e61aea3505fbe2be8233201a206d793b7",
}


def _close_sockets_from(exc: BaseException | None, *roots: object) -> None:
    seen: set[int] = set()

    def close_one(value: object) -> None:
        ident = id(value)
        if ident in seen:
            return
        seen.add(ident)
        if isinstance(value, (ssl.SSLSocket, socket.socket)):
            try:
                value.close()
            except Exception:
                pass

    for root in roots:
        close_one(root)
        if isinstance(root, dict):
            for value in list(root.values()):
                close_one(value)
    tb = None if exc is None else exc.__traceback__
    while tb is not None:
        for value in list(tb.tb_frame.f_locals.values()):
            close_one(value)
        tb = tb.tb_next
    if exc is not None:
        exc.__traceback__ = None


def _close_probe(probe: paho.Client, exc: BaseException | None = None) -> None:
    try:
        probe.loop_stop()
    except Exception:
        pass
    try:
        probe.disconnect()
    except Exception:
        pass
    try:
        probe._reset_sockets()
    except Exception:
        pass
    _close_sockets_from(exc, vars(probe))


def _probe_ready(host: str, port: int, timeout: float, **tls) -> bool:
    connected = threading.Event()
    probe = paho.Client(
        paho.CallbackAPIVersion.VERSION2,
        client_id=f"emqx-ready-{os.getpid()}-{threading.get_ident()}",
    )
    if tls:
        probe.tls_set(**tls)
    probe.on_connect = lambda c, u, f, r, p: connected.set() if not r.is_failure else None
    ready = False
    try:
        probe.connect(host, port)
        probe.loop_start()
        ready = connected.wait(timeout)
    except OSError as exc:
        _close_probe(probe, exc)
        ready = False
    else:
        _close_probe(probe)
    return ready


class Emqx:
    def __init__(self):
        self.port = h.free_port()
        self.name = f"moon-mqtt-emqx-{os.getpid()}"
        self.temp = Path(tempfile.mkdtemp(prefix="moon-mqtt-emqx-"))
        (self.temp / "acl.conf").write_text(
            '{allow, all, all, ["allowed/#", "it/#"]}.\n'
            '{deny, all, subscribe, ["denied/#"]}.\n'
            '{deny, all}.\n'
        )
        # The bind-mounted acl.conf is read by the EMQX container user (uid
        # 1000); `mkdtemp` creates 0700, which blocks it from traversing the
        # directory. The file itself is a public authorization rule set.
        self.temp.chmod(0o755)

    def _remove_container(self) -> None:
        subprocess.run(["docker", "rm", "--force", self.name], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def start(self) -> None:
        image = IMAGE_BY_ARCH.get(platform.machine())
        if not image:
            raise unittest.SkipTest(f"no pinned EMQX image for {platform.machine()}")
        # A previous failed start can leave this name behind; drop it first so a
        # retry is not blocked by a stale container.
        self._remove_container()
        subprocess.run([
            "docker", "run", "--detach", "--name", self.name,
            "--publish", f"127.0.0.1:{self.port}:1883",
            "--env", "EMQX_AUTHORIZATION__NO_MATCH=deny",
            "--env", "EMQX_AUTHORIZATION__DENY_ACTION=ignore",
            "--volume", f"{self.temp / 'acl.conf'}:/opt/emqx/etc/acl.conf:ro",
            image,
        ], check=True, stdout=subprocess.DEVNULL)
        h.wait_port(self.port, 30)
        self.wait_healthy()
        self.wait_mqtt()

    def wait_healthy(self) -> None:
        end = time.monotonic() + 45
        while time.monotonic() < end:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}", self.name],
                text=True, capture_output=True,
            )
            if result.returncode == 0 and result.stdout.strip() in {"healthy", "running"}:
                return
            time.sleep(.25)
        raise TimeoutError(f"EMQX container {self.name} did not become healthy")

    def wait_mqtt(self) -> None:
        end = time.monotonic() + 30
        while time.monotonic() < end:
            if _probe_ready("127.0.0.1", self.port, timeout=0.5):
                return
            time.sleep(.25)
        raise TimeoutError(f"EMQX container {self.name} did not accept MQTT CONNECT")

    def stop(self) -> None:
        subprocess.run(["docker", "stop", self.name], check=True, stdout=subprocess.DEVNULL)

    def start_existing(self) -> None:
        subprocess.run(["docker", "start", self.name], check=True, stdout=subprocess.DEVNULL)
        h.wait_port(self.port, 30)
        self.wait_healthy()
        self.wait_mqtt()

    def close(self) -> None:
        if os.environ.get("MQTT_KEEP_TEST_ARTIFACTS") == "1":
            print(f"EMQX evidence retained in container {self.name} and {self.temp}")
            return
        self._remove_container()
        __import__("shutil").rmtree(self.temp, ignore_errors=True)


class EmqxInteropTest(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = Emqx()
        # Register cleanup before start: a failed start must still remove the
        # container, otherwise the next run hits a container-name conflict.
        self.addCleanup(self.broker.close)
        self.broker.start()

    def test_qos1_bidirectional_and_acl_suback(self) -> None:
        received = []
        ready = __import__("threading").Event()
        oracle = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-emqx-oracle")
        oracle.on_connect = lambda c, u, f, r, p: c.subscribe("allowed/from-moon", 1)
        oracle.on_subscribe = lambda *args: ready.set()
        oracle.on_message = lambda c, u, m: received.append(m.payload)
        oracle.connect("127.0.0.1", self.broker.port)
        oracle.loop_start()
        self.addCleanup(lambda: (oracle.disconnect(), oracle.loop_stop()))
        self.assertTrue(ready.wait(5))

        driver = h.Driver("suback_rejection", self.broker.port)
        self.addCleanup(driver.close)
        driver.expect("suback_rejection_verified")
        self.assertEqual(driver.finish()[0], 0)

        publisher = h.Driver("tcp", self.broker.port)
        self.addCleanup(publisher.close)
        publisher.expect("subscribed")
        oracle.publish("it/to-moon/qos0", b"paho-qos0", qos=0).wait_for_publish(3)
        oracle.publish("it/to-moon/qos1", b"paho-qos1", qos=1).wait_for_publish(3)
        publisher.expect("received_qos0")
        publisher.expect("received_qos1")
        self.assertEqual(publisher.finish()[0], 0)

    def test_reconnect_and_resubscribe(self) -> None:
        driver = h.Driver("reconnect", self.broker.port)
        self.addCleanup(driver.close)
        self.assertEqual(driver.expect("connected").get("generation"), 1)
        self.broker.stop()
        driver.expect("disconnected", 10)
        self.broker.start_existing()
        self.assertGreater(driver.expect("connected", 15).get("generation", 0), 1)
        oracle = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-emqx-reconnect")
        oracle.connect("127.0.0.1", self.broker.port)
        oracle.loop_start()
        oracle.publish("it/reconnect", b"after-restart", qos=1).wait_for_publish(5)
        oracle.disconnect()
        oracle.loop_stop()
        driver.expect("message_after_reconnect", 10)
        self.assertEqual(driver.finish()[0], 0)


class EmqxMtls:
    def __init__(self):
        self.port = h.free_port()
        self.name = f"moon-mqtt-emqx-mtls-{os.getpid()}"
        self.temp = Path(tempfile.mkdtemp(prefix="moon-mqtt-emqx-mtls-"))
        h.write_pki(self.temp, mtls=True)
        # EMQX reads the mounted PKI as its in-container user (uid/gid 1000), so
        # the certificate directory must be traversable and the server key
        # readable by that user. `mkdtemp` creates 0700, which blocks the
        # container user entirely. Only the throwaway server key is exposed;
        # the client private keys stay host-only (0600). The whole PKI is
        # generated per test and removed by close().
        self.temp.chmod(0o755)
        (self.temp / "server.key").chmod(0o644)
        for name in ("client-ca.key", "client.key", "paho-client.key"):
            (self.temp / name).chmod(0o600)

    def _remove_container(self) -> None:
        subprocess.run(["docker", "rm", "--force", self.name], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def start(self) -> None:
        image = IMAGE_BY_ARCH.get(platform.machine())
        if not image:
            raise unittest.SkipTest(f"no pinned EMQX image for {platform.machine()}")
        # Drop a container left behind by an earlier failed start so the name
        # does not conflict.
        self._remove_container()
        subprocess.run([
            "docker", "run", "--detach", "--name", self.name,
            "--publish", f"127.0.0.1:{self.port}:8883",
            "--env", "EMQX_LISTENERS__SSL__DEFAULT__BIND=8883",
            "--env", "EMQX_LISTENERS__SSL__DEFAULT__SSL_OPTIONS__CACERTFILE=/opt/emqx/etc/certs/mtls/client-ca.pem",
            "--env", "EMQX_LISTENERS__SSL__DEFAULT__SSL_OPTIONS__CERTFILE=/opt/emqx/etc/certs/mtls/server.pem",
            "--env", "EMQX_LISTENERS__SSL__DEFAULT__SSL_OPTIONS__KEYFILE=/opt/emqx/etc/certs/mtls/server.key",
            "--env", "EMQX_LISTENERS__SSL__DEFAULT__SSL_OPTIONS__VERIFY=verify_peer",
            "--env", "EMQX_LISTENERS__SSL__DEFAULT__SSL_OPTIONS__FAIL_IF_NO_PEER_CERT=true",
            "--volume", f"{self.temp}:/opt/emqx/etc/certs/mtls:ro",
            image,
        ], check=True, stdout=subprocess.DEVNULL)
        h.wait_port(self.port, 30)
        self.wait_mqtt()

    def wait_mqtt(self) -> None:
        end = time.monotonic() + 45
        while time.monotonic() < end:
            if _probe_ready(
                "localhost", self.port, timeout=0.8,
                ca_certs=str(self.temp / "ca.pem"),
                certfile=str(self.temp / "paho-client.pem"),
                keyfile=str(self.temp / "paho-client.key"),
            ):
                return
            time.sleep(.25)
        raise TimeoutError(f"EMQX mTLS listener {self.name} did not accept MQTT CONNECT")

    def close(self) -> None:
        self._remove_container()
        if os.environ.get("MQTT_KEEP_TEST_ARTIFACTS") != "1":
            __import__("shutil").rmtree(self.temp, ignore_errors=True)


class EmqxMtlsInteropTest(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = EmqxMtls()
        self.addCleanup(self.broker.close)
        self.broker.start()

    def test_mtls_qos1_bidirectional(self) -> None:
        received = []
        ready = __import__("threading").Event()
        oracle = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-emqx-mtls")
        oracle.tls_set(
            ca_certs=str(self.broker.temp / "ca.pem"),
            certfile=str(self.broker.temp / "paho-client.pem"),
            keyfile=str(self.broker.temp / "paho-client.key"),
        )
        oracle.on_connect = lambda c, u, f, r, p: c.subscribe("it/from-moon/mtls", 1)
        oracle.on_subscribe = lambda *args: ready.set()
        oracle.on_message = lambda c, u, m: received.append(m.payload)
        oracle.connect("localhost", self.broker.port)
        oracle.loop_start()
        self.addCleanup(lambda: (oracle.disconnect(), oracle.loop_stop()))
        self.assertTrue(ready.wait(5))
        driver = h.Driver(
            "mtls", self.broker.port, host="localhost",
            MQTT_TEST_CA=str(self.broker.temp / "ca.pem"),
            MQTT_TEST_CERT=str(self.broker.temp / "client.pem"),
            MQTT_TEST_KEY=str(self.broker.temp / "client.key"),
        )
        self.addCleanup(driver.close)
        driver.expect("subscribed")
        oracle.publish("it/to-moon/mtls", b"paho-mtls", qos=1).wait_for_publish(5)
        driver.expect("received_mtls")
        self.assertEqual(driver.finish()[0], 0)
        end = time.monotonic() + 3
        while not received and time.monotonic() < end:
            time.sleep(.025)
        self.assertEqual(received, [b"moon-mtls"])

    def test_mtls_rejects_missing_certificate(self) -> None:
        driver = h.Driver(
            "tls", self.broker.port, host="localhost",
            MQTT_TEST_CA=str(self.broker.temp / "ca.pem"),
        )
        self.addCleanup(driver.close)
        self.assertNotEqual(driver.finish()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
