#!/usr/bin/env python3
"""Native host consumer against Mosquitto and a Paho HA-message oracle.
This does not launch Home Assistant or exercise ESP32 hardware.
"""
import importlib.util
import json
import os
from pathlib import Path
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


def until(predicate, why, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.025)
    raise AssertionError(why)


class HaRelay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([str(h.MOON), "build", "--target", "native"], cwd=ROOT, check=True)
        cls.binary = demo_build_dir(ROOT).parent / "ha_relay"

    def setUp(self):
        self.broker = h.Broker()
        self.broker.start()
        self.addCleanup(self.broker.stop)
        self.prefix = "ha-test/" + uuid.uuid4().hex
        self.entity = "relay_" + uuid.uuid4().hex
        self.messages = []
        self.logs = []
        self.env = {**os.environ, "MQTT_DEMO_PORT": str(self.broker.port),
                    "HA_RELAY_PREFIX": self.prefix, "HA_RELAY_ENTITY_ID": self.entity}
        self.client = h.paho.Client(h.paho.CallbackAPIVersion.VERSION2,
                                   client_id="ha-oracle-" + uuid.uuid4().hex)
        ready = threading.Event()
        self.client.on_connect = lambda c, u, f, r, p: c.subscribe([
            (self.prefix + "/#", 1), (self.discovery_topic, 1)])
        self.client.on_subscribe = lambda *args: ready.set()
        self.client.on_message = lambda c, u, m: self.messages.append((m.topic, m.payload))
        self.client.connect("127.0.0.1", self.broker.port)
        self.client.loop_start()
        self.addCleanup(self.client.loop_stop)
        self.addCleanup(self.client.disconnect)
        self.assertTrue(ready.wait(5))

    @property
    def discovery_topic(self):
        return "homeassistant/switch/" + self.entity + "/config"

    def launch(self, name):
        output = tempfile.TemporaryFile(mode="w+")
        self.addCleanup(output.close)
        self.logs.append(output)
        process = subprocess.Popen([str(self.binary / name / (name + ".exe"))],
                                   env=self.env, stdout=output, stderr=subprocess.STDOUT)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.addCleanup(cleanup)
        return process

    def tearDown(self):
        if any(test is self for test, _ in self._outcome.result.failures + self._outcome.result.errors):
            for log in self.logs:
                log.seek(0)
                print(log.read())

    def publish(self, suffix, value, retain=False):
        payload = json.dumps(value) if isinstance(value, dict) else value
        info = self.client.publish(self.prefix + suffix, payload, qos=1, retain=retain)
        info.wait_for_publish(timeout=5)
        self.assertTrue(info.is_published())

    def values(self, suffix):
        return [payload for topic, payload in self.messages if topic == self.prefix + suffix]

    def wait_value(self, suffix, value):
        until(lambda: value in self.values(suffix), suffix + " missing " + repr(value))

    def start_pair(self):
        device = self.launch("simulator")
        self.wait_value("/device/availability", b"online")
        controller = self.launch("controller")
        self.wait_value("/controller/availability", b"online")
        self.wait_value("/ha/state", b"OFF")
        return device, controller

    def test_command_confirmation_restart_identity_and_discovery_birth(self):
        _, controller = self.start_pair()
        self.publish("/ha/set", "ON")
        self.wait_value("/ha/state", b"ON")
        command = json.loads(self.values("/device/set")[-1])
        self.assertEqual(len(command["id"]), 32)
        self.assertGreater(command["expires_at_ms"], int(time.time()*1000))
        discovery = json.loads(next(p for t,p in self.messages if t == self.discovery_topic))
        self.assertFalse(discovery["optimistic"])
        self.assertFalse(discovery["retain"])
        self.assertEqual(discovery["availability_mode"], "all")
        before = sum(t == self.discovery_topic for t,p in self.messages)
        self.client.publish("homeassistant/status", "online", qos=1).wait_for_publish(5)
        until(lambda: sum(t == self.discovery_topic for t,p in self.messages) > before,
              "discovery was not republished on HA birth")
        old_queries = {json.loads(v)["id"] for v in self.values("/device/query")}
        controller.kill(); controller.wait(timeout=5)
        self.wait_value("/controller/availability", b"offline")
        self.messages.clear()
        self.launch("controller")
        self.wait_value("/controller/availability", b"online")
        self.wait_value("/ha/state", b"ON")
        new_queries = {json.loads(v)["id"] for v in self.values("/device/query")}
        self.assertTrue(new_queries)
        self.assertTrue(old_queries.isdisjoint(new_queries))
        self.assertEqual(self.values("/device/set"), [], "restart silently replayed a command")

    def test_expired_and_retained_commands_do_not_change_device(self):
        self.publish("/device/set", {"id":"retained", "target":"ON", "expires_at_ms":int(time.time()*1000)+30000}, retain=True)
        self.launch("simulator")
        self.wait_value("/device/availability", b"online")
        self.publish("/device/set", {"id":"expired", "target":"ON", "expires_at_ms":1})
        # Retention is tested at resubscription; MQTT 3 forwards live retained
        # publications with RETAIN=0, so the immutable expiry is still required.
        self.publish("/device/set", {"id":"bad", "target":"ON"}, retain=True)
        query = uuid.uuid4().hex
        self.publish("/device/query", {"id":query})
        until(lambda: any(json.loads(v)["correlation_id"] == query for v in self.values("/device/feedback")), "query feedback missing")
        report = next(json.loads(v) for v in self.values("/device/feedback") if json.loads(v)["correlation_id"] == query)
        self.assertEqual(report["state"], "OFF")

    def test_puback_without_device_feedback_never_claims_execution(self):
        self.launch("controller")
        until(lambda: bool(self.values("/device/query")), "controller query missing")
        self.publish("/ha/set", "ON")
        until(lambda: bool(self.values("/device/set")), "command missing")
        until(lambda: len(self.values("/device/query")) >= 2, "unconfirmed command did not trigger query")
        self.assertNotIn(b"ON", self.values("/ha/state"))
        self.assertNotIn(b"online", self.values("/controller/availability"))

    def test_device_crash_publishes_offline_will(self):
        device, _ = self.start_pair()
        device.kill(); device.wait(timeout=5)
        self.wait_value("/device/availability", b"offline")


if __name__ == "__main__":
    unittest.main(verbosity=2)
