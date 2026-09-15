#!/usr/bin/env python3
"""Broker-level smoke test for the state-sync thermostat demo.

Starts a real Mosquitto broker, the simulated device and the controller through
the compiled binaries, and observes the wire with an independent Paho client. No
hardware is involved: the device is the simulated process in
`examples/mqtt_demo/test_device`. The richer fault/restart cases live in
`examples/mqtt_demo/demo.py`; this file keeps the default local check fast.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mqtt_integration", ROOT / "tests/integration/run.py"
)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

BUILD = ROOT / "_build/native/debug/build/examples/mqtt_demo"
PREFIX = "moon/demo/thermostat"


def until(predicate, seconds=8.0, what="expected broker observation"):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.025)
    raise AssertionError(what)


def main() -> int:
    subprocess.run([str(h.MOON), "build", "--target", "native"], cwd=ROOT, check=True)
    broker = h.Broker()
    broker.start()
    messages: list[tuple[str, bytes, bool]] = []
    subscribed = threading.Event()
    client = None
    device = None
    controller = None
    failed = True
    try:
        env = {**os.environ, "MQTT_DEMO_PORT": str(broker.port)}
        client = h.paho.Client(
            h.paho.CallbackAPIVersion.VERSION2, client_id="scenario-observer"
        )
        client.on_connect = lambda c, u, f, r, p: c.subscribe(f"{PREFIX}/#", 1)
        client.on_subscribe = lambda *args: subscribed.set()
        client.on_message = lambda c, u, m: messages.append(
            (m.topic, m.payload, m.retain)
        )
        client.connect("127.0.0.1", broker.port)
        client.loop_start()
        assert subscribed.wait(3)

        device = subprocess.Popen(
            [str(BUILD / "test_device/test_device.exe")], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        until(lambda: device.poll() is None, 2, "device process started")
        controller = subprocess.Popen(
            [str(BUILD / "controller/controller.exe")], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # The controller queries the device on connect before any command.
        until(
            lambda: any(t.endswith("/relay/query") for t, _, _ in messages),
            8,
            "controller to query the device on startup",
        )
        # A 28 C sample must produce an absolute ON command, and the device must
        # report the executed state back.
        client.publish(
            f"{PREFIX}/temperature", b'{"temperature_c":28}', 1
        ).wait_for_publish(3)
        until(
            lambda: any(
                t.endswith("/relay/set") and b'"target":"ON"' in p
                for t, p, _ in messages
            ),
            8,
            "ON command",
        )
        until(
            lambda: any(
                t.endswith("/relay/feedback") and b'"state":"ON"' in p
                for t, p, _ in messages
            ),
            8,
            "device feedback confirming ON",
        )
        # Malformed and non-finite input is rejected without a command.
        before = len([1 for t, _, _ in messages if t.endswith("/relay/set")])
        for payload in (b"{bad json", b"\xff", b'{"temperature_c":1e999}'):
            client.publish(f"{PREFIX}/temperature", payload, 1).wait_for_publish(3)
        time.sleep(.4)
        after = len([1 for t, _, _ in messages if t.endswith("/relay/set")])
        assert before == after, "invalid temperature input produced a command"
        # A 26 C sample turns the relay off again.
        client.publish(
            f"{PREFIX}/temperature", b'{"temperature_c":26}', 1
        ).wait_for_publish(3)
        until(
            lambda: any(
                t.endswith("/relay/set") and b'"target":"OFF"' in p
                for t, p, _ in messages
            ),
            8,
            "OFF command",
        )
        until(
            lambda: any(
                t.endswith("/relay/feedback") and b'"state":"OFF"' in p
                for t, p, _ in messages
            ),
            8,
            "device feedback confirming OFF",
        )
        print(
            "state-sync demo smoke passed: startup query, ON at 28 C, invalid "
            "input ignored, OFF at 26 C, device feedback correlated"
        )
        failed = False
        return 0
    except AssertionError as error:
        print(f"state-sync demo smoke failed: {error}")
        return 1
    finally:
        for process in (controller, device):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if client is not None:
            client.disconnect()
            client.loop_stop()
        broker.close(failed=failed)


if __name__ == "__main__":
    raise SystemExit(main())
