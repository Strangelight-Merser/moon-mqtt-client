#!/usr/bin/env python3
"""Black-box interoperability checks against a pinned official EMQX image."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import platform
import subprocess
import tempfile
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

    def start(self) -> None:
        image = IMAGE_BY_ARCH.get(platform.machine())
        if not image:
            raise unittest.SkipTest(f"no pinned EMQX image for {platform.machine()}")
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
            connected = __import__("threading").Event()
            probe = paho.Client(
                paho.CallbackAPIVersion.VERSION2,
                client_id=f"emqx-readiness-{os.getpid()}",
            )
            probe.on_connect = lambda c, u, f, r, p: connected.set() if not r.is_failure else None
            try:
                probe.connect("127.0.0.1", self.port)
                probe.loop_start()
                if connected.wait(.5):
                    probe.disconnect()
                    probe.loop_stop()
                    return
                probe.loop_stop()
            except OSError:
                pass
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
        subprocess.run(["docker", "rm", "--force", self.name], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class EmqxInteropTest(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = Emqx()
        self.broker.start()
        self.addCleanup(self.broker.close)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
