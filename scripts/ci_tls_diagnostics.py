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
    driver = h.Driver(scenario, broker.port, host="localhost", **env)
    try:
        start = time.monotonic()
        for _ in range(12):
            time.sleep(1)
            print(scenario, round(time.monotonic() - start, 3), driver.process.poll(), driver.drain_events(), flush=True)
            if driver.process.poll() is not None:
                break
        print("BROKER", (broker.temp / "broker.log").read_text(), flush=True)
        subprocess.run(["ps", "-e", "-o", "pid,ppid,stat,wchan:30,args", "--forest"], check=False)
        if driver.process.poll() is None:
            descendants = [driver.process.pid]
            for pid in descendants:
                children = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
                descendants.extend(int(x) for x in children.stdout.split())
            for pid in descendants:
                print("PROCESS", pid, flush=True)
                subprocess.run(["sudo", "ls", "-l", f"/proc/{pid}/fd"], check=False)
                subprocess.run(["sudo", "gdb", "-batch", "-ex", "set pagination off", "-ex", "thread apply all bt", "-p", str(pid)], timeout=20, check=False)
            if driver.process.stderr:
                os.set_blocking(driver.process.stderr.fileno(), False)
                print("LIVE STDERR", os.read(driver.process.stderr.fileno(), 65536), flush=True)
        else:
            print("STDERR", driver.stderr(), flush=True)
    finally:
        driver.close()
        broker.close()
