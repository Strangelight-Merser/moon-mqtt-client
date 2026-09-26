#!/usr/bin/env python3
"""Run bounded macOS dial-stage calibration and two fixed-budget soaks."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).resolve().parent
BASE = ROOT / "_build" / "dial-stage-hosted"
BASELINE = "c52ba9b9f7e8a8aee5ccb2e8b186feeecb6faee6"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def step(name, command, env, timeout):
    log = BASE / f"{name}.log"
    started = time.time()
    with log.open("w") as output:
        try:
            code = subprocess.run(command, cwd=ROOT, env=env, stdout=output,
                                  stderr=subprocess.STDOUT, timeout=timeout).returncode
            error = None
        except subprocess.TimeoutExpired:
            code, error = 124, "timeout"
    return {"name": name, "command": command, "returncode": code,
            "error": error, "elapsed_seconds": time.time() - started,
            "log_sha256": sha(log)}


def soak(name, duration, cycles, payload, concurrency, mtls, env):
    directory = BASE / name
    command = [sys.executable, "tests/soak.py", "--duration", str(duration),
               "--cycles", str(cycles), "--payload-bytes", str(payload),
               "--concurrency", str(concurrency), "--broker-log", "debug",
               "--broker-log-limit-mb", "64", "--artifacts", str(directory)]
    if mtls:
        command.append("--mtls")
    return step(name, command, env, duration + 120)


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    patch = subprocess.check_output(
        ["git", "diff", BASELINE, "HEAD", "--", "moon.pkg", "runtime.mbt", "tests/soak.py"],
        cwd=ROOT,
    )
    (BASE / "diagnostic.patch").write_bytes(patch)
    protocol = {
        "purpose": "R2-01 target-hosted diagnostic per-dial stage attribution",
        "frozen_source_commit": BASELINE,
        "diagnostic_commit": git("rev-parse", "HEAD"),
        "diagnostic_build": True,
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "parser_sha256": sha(SCRIPT / "build_ledger.py"),
        "calibration_sha256": sha(SCRIPT / "calibrate.py"),
        "platform": "GitHub Actions macos-latest arm64",
        "runs": [
            {"name": "tcp-1800-100", "transport": "TCP", "duration_seconds": 1800,
             "restart_cycles": 100, "downtime_seconds": .2, "concurrency": 16,
             "payload_bytes": 1024},
            {"name": "mtls-1800-100", "transport": "mTLS", "duration_seconds": 1800,
             "restart_cycles": 100, "downtime_seconds": .2, "concurrency": 16,
             "payload_bytes": 1024},
        ],
        "broker_log": "selected Mosquitto debug connection and CONNACK lines",
        "acceptance": "100 recoveries per run; unique process attempts; socket port, broker instance, MQTT identity and numeric CONNACK correlation; emitted and consumed event order",
        "limits": "Diagnostic build on hosted Mac; no historical artifact backfill, production performance or Release claim.",
    }
    write_json(BASE / "protocol.json", protocol)
    state = {"protocol_sha256": sha(BASE / "protocol.json"), "steps": [], "complete": False}
    write_json(BASE / "run-state.json", state)
    env = os.environ.copy()
    env["MOON"] = subprocess.check_output(["which", "moon"], text=True).strip()
    env["MOSQUITTO"] = subprocess.check_output(["which", "mosquitto"], text=True).strip()
    env["MQTT_DIAL_FILTER_BROKER"] = "1"

    def run(name, command, timeout):
        result = step(name, command, env, timeout)
        state["steps"].append(result)
        write_json(BASE / "run-state.json", state)
        return result["returncode"] == 0

    if not run("moon-update", [env["MOON"], "update"], 180):
        return 1
    if not run("moon-build", [env["MOON"], "build", "--target", "native"], 300):
        return 1
    for name, mtls in (("tcp-short", False), ("mtls-short", True)):
        result = soak(name, 8, 1, 64, 1, mtls, env)
        state["steps"].append(result)
        write_json(BASE / "run-state.json", state)
        if result["returncode"] != 0:
            return 1
        if not run(name + "-ledger", [sys.executable, str(SCRIPT / "build_ledger.py"),
                                       str(BASE / name)], 30):
            return 1
    if not run("negative-calibration", [sys.executable, str(SCRIPT / "calibrate.py")], 120):
        return 1

    failed = False
    for name, mtls in (("tcp-1800-100", False), ("mtls-1800-100", True)):
        result = soak(name, 1800, 100, 1024, 16, mtls, env)
        state["steps"].append(result)
        write_json(BASE / "run-state.json", state)
        if result["returncode"] != 0:
            failed = True
            continue
        if not run(name + "-ledger", [sys.executable, str(SCRIPT / "build_ledger.py"),
                                      str(BASE / name)], 60):
            failed = True
    if not failed and not run("summary", [sys.executable, str(SCRIPT / "summarize.py")], 60):
        failed = True
    state["complete"] = True
    state["passed"] = not failed
    write_json(BASE / "run-state.json", state)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
