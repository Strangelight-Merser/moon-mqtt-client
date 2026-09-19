#!/usr/bin/env python3
"""Standalone EMQX WebSocket/WSS fixture and Paho smoke probes.

The fixture is intentionally separate from the TCP interoperability fixtures.
It maps random loopback host ports to EMQX's standard 1883/8083/8084 listeners
and keeps Docker output under ``_build/exec-ws`` only when a probe fails.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

import paho.mqtt.client as paho


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "mqtt_integration", ROOT / "tests/integration/run.py"
)
_helper = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_helper)
_emqx_spec = importlib.util.spec_from_file_location(
    "emqx_interop", ROOT / "tests/emqx_interop.py"
)
_emqx = importlib.util.module_from_spec(_emqx_spec)
assert _emqx_spec.loader is not None
_emqx_spec.loader.exec_module(_emqx)

IMAGE_BY_ARCH = {
    "x86_64": "emqx/emqx:5.8.8@sha256:2bbadcd6ebdf1e2d032bdd8149c9615867ed25f32b1a09ec5bd92ddcf159e1aa",
    "arm64": "emqx/emqx:5.8.8@sha256:b07f5f1f20009c8be42b7865f37ae97e61aea3505fbe2be8233201a206d793b7",
}
ARTIFACTS = ROOT / "_build" / "exec-ws"


def _wait_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            try:
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise TimeoutError(f"port {port} did not become ready")


def _probe(
    *,
    fixture: "WsBroker",
    host: str,
    port: int,
    tls: bool = False,
    client_cert: bool = False,
    timeout: float = 8.0,
    route: bool = False,
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last_reason = "timeout waiting for CONNACK"
    while time.monotonic() < deadline:
        connected = threading.Event()
        reason_text: list[str] = []
        reason_failed = [False]
        routed = threading.Event()
        probe_topic = "it/ws-ready/" + uuid.uuid4().hex
        probe_payload = uuid.uuid4().hex.encode()
        client = paho.Client(
            paho.CallbackAPIVersion.VERSION2,
            client_id=f"ws-probe-{uuid.uuid4().hex[:12]}",
            transport="websockets",
            protocol=paho.MQTTv311,
        )
        client.ws_set_options(path="/mqtt")
        if tls:
            kwargs: dict[str, str] = {"ca_certs": str(fixture.temp / "ca.pem")}
            if client_cert:
                kwargs.update(
                    certfile=str(fixture.client_cert),
                    keyfile=str(fixture.client_key),
                )
            client.tls_set(**kwargs)

        def on_connect(_client, _userdata, _flags, reason, _properties):
            reason_text.append(str(reason))
            reason_failed[0] = bool(getattr(reason, "is_failure", False))
            connected.set()
            if route and not reason_failed[0]:
                _client.subscribe(probe_topic, qos=1)

        def on_subscribe(c, _u, _mid, reasons, _props):
            if len(reasons) == 1 and not reasons[0].is_failure:
                c.publish(probe_topic, probe_payload, qos=1)

        def on_message(_c, _u, message):
            if message.topic == probe_topic and message.payload == probe_payload:
                routed.set()

        client.on_connect = on_connect
        client.on_subscribe = on_subscribe
        client.on_message = on_message
        try:
            client.connect(host, port, keepalive=10)
            client.loop_start()
            if connected.wait(min(2.0, max(0.1, deadline - time.monotonic()))):
                if not reason_failed[0]:
                    if not route or routed.wait(max(0.0, deadline - time.monotonic())):
                        return True, reason_text[-1]
                    return False, "CONNACK succeeded but subscription/publish route was not ready"
                return False, reason_text[-1] if reason_text else "failed CONNACK"
            last_reason = "timeout waiting for CONNACK"
        except Exception as exc:  # expected for WSS without a client identity
            # A failed WebSocket/TLS handshake can leave the raw socket only in
            # the exception traceback. Reuse the existing fixture cleanup so
            # ResourceWarning is not deferred until interpreter shutdown.
            _emqx._close_probe(client, exc)
            last_reason = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
            try:
                client.loop_stop()
            except Exception:
                pass
        time.sleep(0.15)
    return False, last_reason


class WsBroker:
    """Context-managed EMQX 5.8.8 WS/WSS fixture."""

    def __init__(self) -> None:
        self.token = uuid.uuid4().hex
        self.name = f"moon-mqtt-ws-{os.getpid()}-{self.token[:12]}"
        self.tcp_port = _helper.free_port()
        self.ws_port = _helper.free_port()
        self.wss_port = _helper.free_port()
        self.temp = Path(tempfile.mkdtemp(prefix=f"moon-mqtt-ws-{self.token}-"))
        try:
            self.temp.chmod(0o755)
            _helper.write_pki(self.temp, mtls=True)
            (self.temp / "server.key").chmod(0o644)
            for name in ("client-ca.key", "client.key", "paho-client.key"):
                (self.temp / name).chmod(0o600)
        except BaseException:
            shutil.rmtree(self.temp, ignore_errors=True)
            raise
        self.client_ca = self.temp / "client-ca.pem"
        self.client_cert = self.temp / "client.pem"
        self.client_key = self.temp / "client.key"
        self.base_hocon = self.temp / "base.hocon"
        self.base_hocon.write_text(
            "listeners.tcp.default { bind = \"0.0.0.0:1883\" }\n"
            "listeners.ws.default {\n"
            "  bind = \"0.0.0.0:8083\"\n"
            "  websocket.mqtt_path = \"/mqtt\"\n"
            "}\n"
            "listeners.wss.default {\n"
            "  bind = \"0.0.0.0:8084\"\n"
            "  websocket.mqtt_path = \"/mqtt\"\n"
            "  ssl_options {\n"
            "    cacertfile = \"/opt/emqx/etc/certs/mtls/client-ca.pem\"\n"
            "    certfile = \"/opt/emqx/etc/certs/mtls/server.pem\"\n"
            "    keyfile = \"/opt/emqx/etc/certs/mtls/server.key\"\n"
            "    verify = verify_peer\n"
            "    fail_if_no_peer_cert = true\n"
            "  }\n"
            "}\n"
        )
        self.process_started = False

    def __enter__(self) -> "WsBroker":
        self.start()
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        try:
            if exc_type is not None:
                self._retain_failure_log()
        finally:
            self.close()

    def _retain_failure_log(self) -> None:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        log_path = ARTIFACTS / f"{self.name}.docker.log"
        with log_path.open("w", encoding="utf-8") as stream:
            subprocess.run(
                ["docker", "logs", self.name],
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
                text=True,
            )
        print(f"retained Docker log: {log_path}")

    def wait_boot_complete(self) -> None:
        # Listeners can accept MQTT before later applications finish booting.
        # A successful early CONNACK is not evidence of a running broker.
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            result = subprocess.run(["docker", "logs", self.name],
                                    text=True, capture_output=True)
            if result.returncode == 0 and "EMQX 5.8.8 is running now!" in result.stdout + result.stderr:
                return
            state = subprocess.run(["docker", "inspect", "--format",
                                    "{{.State.Running}}", self.name],
                                   text=True, capture_output=True)
            if state.returncode != 0 or state.stdout.strip() != "true":
                raise RuntimeError("EMQX stopped before completing startup")
            time.sleep(0.25)
        raise TimeoutError("EMQX did not complete startup")

    def start(self) -> None:
        image = IMAGE_BY_ARCH.get(platform.machine())
        if image is None:
            raise unittest.SkipTest(f"no pinned EMQX image for {platform.machine()}")
        try:
            self.temp.chmod(0o755)
            command = [
                "docker", "run", "--detach", "--name", self.name,
                "--publish", f"127.0.0.1:{self.tcp_port}:1883",
                "--publish", f"127.0.0.1:{self.ws_port}:8083",
                "--publish", f"127.0.0.1:{self.wss_port}:8084",
                "--volume", f"{self.base_hocon}:/opt/emqx/etc/base.hocon:ro",
                "--volume", f"{self.temp}:/opt/emqx/etc/certs/mtls:ro",
                image,
            ]
            # Mark the unique name for cleanup before invoking Docker: a
            # failed `docker run` can still leave a created container behind.
            self.process_started = True
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
            _wait_port(self.ws_port)
            _wait_port(self.wss_port)
            _wait_port(self.tcp_port)
            self.wait_boot_complete()
            ws_ok, ws_reason = _probe(
                fixture=self, host="127.0.0.1", port=self.ws_port, route=True
            )
            if not ws_ok:
                raise RuntimeError(f"EMQX WS readiness failed: {ws_reason}")
            wss_ok, wss_reason = _probe(
                fixture=self, host="localhost", port=self.wss_port,
                tls=True, client_cert=True, route=True,
            )
            if not wss_ok:
                raise RuntimeError(f"EMQX WSS readiness failed: {wss_reason}")
        except BaseException:
            self._retain_failure_log()
            self.close()
            raise

    def close(self) -> None:
        subprocess.run(
            ["docker", "rm", "--force", self.name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(self.temp, ignore_errors=True)


def main() -> None:
    with WsBroker() as broker:
        ws_ok, ws_reason = _probe(fixture=broker, host="127.0.0.1", port=broker.ws_port)
        wss_ok, wss_reason = _probe(
            fixture=broker, host="localhost", port=broker.wss_port, tls=True, client_cert=True
        )
        missing_ok, missing_reason = _probe(
            fixture=broker, host="localhost", port=broker.wss_port, tls=True, client_cert=False
        )
        print(f"WS  127.0.0.1:{broker.ws_port}/mqtt: {ws_ok} ({ws_reason})")
        print(f"WSS localhost:{broker.wss_port}/mqtt + client cert: {wss_ok} ({wss_reason})")
        print(f"WSS localhost:{broker.wss_port}/mqtt without cert: {missing_ok} ({missing_reason})")
        assert ws_ok, ws_reason
        assert wss_ok, wss_reason
        assert not missing_ok, "WSS accepted a client without a certificate"


if __name__ == "__main__":
    main()
