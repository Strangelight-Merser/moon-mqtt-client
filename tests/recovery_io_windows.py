#!/usr/bin/env python3
"""Test-only syscall EIO at three recovery publication/retirement windows."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from durable_recovery_admin import CleanPeer, DurableRecoveryAdmin, CLI, ROOT
from storage_faults import build_injector, injection, snapshot


WINDOWS = {
    "prepared_journal": {"path_contains": ".old.sqlite3.recovery.sqlite3."},
    "source_retirement": {"path_contains": "old.sqlite3-journal"},
    "target_directory_publish": {"directory_only": True,
                                 "arm_prefix": ".new.sqlite3.recovery-",
                                 "arm_target": True},
}


def cli(request, command, env=None):
    return subprocess.run([sys.executable, str(CLI), command, "--request", str(request)],
                          cwd=ROOT, env=env, text=True, capture_output=True, timeout=25)


def native_inspect(binary, path, port, client_id):
    env = os.environ | {"RECOVERY_OUTBOX": str(path), "RECOVERY_HOST": "127.0.0.1",
                        "RECOVERY_PORT": str(port), "RECOVERY_CLIENT_ID": client_id,
                        "RECOVERY_PROTOCOL": "mqtt311", "RECOVERY_SESSION_EXPIRY": "0"}
    return subprocess.run([str(binary)], cwd=ROOT, env=env, text=True,
                          capture_output=True, timeout=10)


def run_window(name, selector, injector, native, output):
    output.mkdir()
    peer = CleanPeer()
    helper = DurableRecoveryAdmin("runTest")
    request_path, request = helper.request(output, peer.port)
    source = Path(request["source"])
    target = Path(request["target"])
    archive = Path(request["archive"])
    before = snapshot(source)
    marker = output / "injected.marker"
    env = injection(injector, output, "fsync", marker)
    if selector.get("path_contains"):
        env["MQTT_IO_PATH_CONTAINS"] = selector["path_contains"]
    if selector.get("directory_only"):
        env["MQTT_IO_DIRECTORY_ONLY"] = "1"
    if selector.get("arm_prefix"):
        env["MQTT_IO_ARM_PREFIX"] = selector["arm_prefix"]
    if selector.get("arm_target"):
        env["MQTT_IO_ARM_PATH"] = str(target)
    if name == "target_directory_publish":
        peer.start()
    failed = cli(request_path, "resolve", env)
    (output / "first-attempt.log").write_text(failed.stdout + failed.stderr)
    assert marker.exists(), (name, failed.stdout, failed.stderr)
    assert failed.returncode != 0, (name, failed.stdout, failed.stderr)
    assert archive.exists() and archive.stat().st_size > 0, name
    verified = subprocess.run([sys.executable, str(CLI), "verify", "--archive", str(archive)],
                              cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert verified.returncode == 0, verified.stderr
    archive_rows = json.loads(verified.stdout)["manifest"]["snapshot"]["records"]
    assert [row["delivery_id"] for row in archive_rows] == ["old-delivery"]
    assert snapshot(source)[1] == before[1], "old durable row changed"
    with sqlite3.connect(source) as connection:
        old_identity = connection.execute("SELECT identity FROM durable_meta").fetchone()[0]
    assert old_identity == before[0][0][2] or old_identity.startswith(
        "moon-mqtt-retired-v1|"), old_identity
    target_before = (target.stat().st_ino, hashlib.sha256(target.read_bytes()).hexdigest()) \
        if target.exists() else None
    if name != "target_directory_publish":
        peer.start()
    resumed = cli(request_path, "resume")
    (output / "resume.log").write_text(resumed.stdout + resumed.stderr)
    assert resumed.returncode == 0 and json.loads(resumed.stdout)["phase"] == "Complete", (
        name, resumed.stdout, resumed.stderr)
    peer.finish()
    assert len(peer.connects) == 2, (name, len(peer.connects))
    assert all(not packet or packet[0] >> 4 != 3 for packet in peer.trailing), (
        name, peer.trailing)
    assert snapshot(source)[1] == before[1]
    assert target.exists()
    if target_before:
        assert target_before == (target.stat().st_ino,
                                 hashlib.sha256(target.read_bytes()).hexdigest()), \
            "resume replaced an already published target"
    old_native = native_inspect(native, source, peer.port, "recovery-old")
    new_native = native_inspect(native, target, peer.port, "recovery-new")
    assert old_native.returncode != 0, old_native.stdout + old_native.stderr
    assert new_native.returncode == 0, new_native.stdout + new_native.stderr
    record = {"window": name, "first_exit": failed.returncode,
              "injected": marker.read_text(), "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "source_rows_preserved": True, "target_before_resume": target_before,
              "target_not_overwritten": True, "resume_complete": True,
              "old_identity_rejected": True, "new_store_opened": True,
              "broker_connects": len(peer.connects), "power_loss_tested": False}
    (output / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    output = (args.artifacts or Path(os.environ.get("MQTT_EVIDENCE_DIR", ROOT / "_build" / "recovery-io"))
              / f"recovery-io-{time.time_ns()}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([os.environ.get("MOON", str(ROOT / "scripts/moon.sh")),
                    "build", "--target", "native"], cwd=ROOT, check=True)
    native = next((ROOT / "_build/native/debug/build").glob(
        "**/tests/recovery_inspector/recovery_inspector.exe"))
    injector = build_injector(output)
    report = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                   cwd=ROOT, text=True).strip(),
              "native_sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
              "injector_sha256": hashlib.sha256(injector.read_bytes()).hexdigest(),
              "results": []}
    for name, selector in WINDOWS.items():
        print("RUN", name, flush=True)
        report["results"].append(run_window(name, selector, injector, native, output / name))
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Recovery I/O windows passed:", output)


if __name__ == "__main__":
    main()
