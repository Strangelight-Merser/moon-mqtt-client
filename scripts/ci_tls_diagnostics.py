#!/usr/bin/env python3
"""Collect bounded diagnostics for TLS driver stalls without changing tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("h", root / "tests/integration/run.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
for scenario in ("mtls_dual", "mtls_repeat"):
    broker = h.Broker(mtls=True)
    broker.start()
    env = {"MQTT_TEST_CA": str(broker.temp / "ca.pem")}
    if scenario == "mtls_dual":
        for suffix, cert in (("A", "device-a"), ("B", "device-b")):
            env["MQTT_TEST_CERT_" + suffix] = str(broker.temp / (cert + ".pem"))
            env["MQTT_TEST_KEY_" + suffix] = str(broker.temp / (cert + ".key"))
    else:
        env.update(MQTT_TEST_CERT=str(broker.temp / "client.pem"), MQTT_TEST_KEY=str(broker.temp / "client.key"))
    binary = root / "_build/native/debug/build/Strangelight-Merser/moon-mqtt-client/examples/test_driver/test_driver.exe"
    child_env = {**os.environ, "MQTT_TEST_SCENARIO": scenario, "MQTT_TEST_HOST": "localhost", "MQTT_TEST_PORT": str(broker.port), **env}
    try:
        subprocess.run([
            "gdb", "-batch", "-ex", "set pagination off",
            "-ex", "handle SIGUSR1 nostop noprint pass",
            "-ex", "handle SIGUSR2 nostop noprint pass",
            "-ex", "run", "-ex", "thread apply all bt",
            "--args", str(binary),
        ], env=child_env, timeout=40, check=False)
    finally:
        print("BROKER", (broker.temp / "broker.log").read_text(), flush=True)
        broker.close()
