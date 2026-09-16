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
broker = h.Broker(mtls=True)
broker.start()
try:
    env = {**os.environ, "MQTT_TEST_PORT": str(broker.port), "MQTT_TEST_CA": str(broker.temp / "ca.pem"), "MQTT_TEST_CERT": str(broker.temp / "client.pem"), "MQTT_TEST_KEY": str(broker.temp / "client.key")}
    subprocess.run([str(h.MOON), "run", "examples/tls_diagnostics"], env=env, timeout=90, check=False)
finally:
    print("BROKER", (broker.temp / "broker.log").read_text(), flush=True)
    broker.close()
