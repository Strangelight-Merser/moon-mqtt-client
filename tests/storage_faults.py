#!/usr/bin/env python3
"""Real bounded-volume ENOSPC and syscall EIO; neither simulates power loss."""
import argparse
from contextlib import contextmanager, closing
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile

from durable_recovery_admin import create_store, identity
from harness import NativeProcess
from protocol_faults import recv_packet, publish_id

ROOT = Path(__file__).resolve().parents[1]


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return db.execute("SELECT * FROM durable_meta").fetchall(), db.execute("SELECT * FROM durable_outbox").fetchall()


def build_injector(directory):
    library = directory / ("io_faults.dylib" if platform.system() == "Darwin" else "io_faults.so")
    flags = ["-dynamiclib"] if platform.system() == "Darwin" else ["-shared", "-fPIC", "-ldl"]
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", *flags, str(ROOT / "tests/io_faults.c"), "-o", str(library)], check=True)
    return library


def injection(library, root, operation, marker, arm=None):
    variable = "DYLD_INSERT_LIBRARIES" if platform.system() == "Darwin" else "LD_PRELOAD"
    env = {**os.environ, variable: str(library), "MQTT_IO_ROOT": str(root.resolve()),
           "MQTT_IO_OPERATION": operation, "MQTT_IO_MARKER": str(marker)}
    if arm:
        env["MQTT_IO_ARM"] = str(arm)
    return env


@contextmanager
def small_volume(directory):
    mount = directory / "volume"
    mount.mkdir()
    if platform.system() == "Darwin":
        image = directory / "isolated.dmg"
        subprocess.run(["hdiutil", "create", "-size", "32m", "-fs", "HFS+", "-volname", "mqtt-audit", str(image)], check=True, capture_output=True)
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-mountpoint", str(mount), str(image)], check=True, capture_output=True)
        detach = ["hdiutil", "detach", str(mount)]
    elif platform.system() == "Linux":
        # Only this new temporary mount is affected. Hosted Linux supports
        # passwordless sudo; inability to mount is a failed required gate.
        subprocess.run(["sudo", "-n", "mount", "-t", "tmpfs", "-o", "size=32m,mode=0777", "tmpfs", str(mount)], check=True)
        detach = ["sudo", "-n", "umount", str(mount)]
    else:
        raise RuntimeError("ENOSPC volume is not implemented on this platform")
    try:
        size = os.statvfs(mount)
        assert size.f_blocks * size.f_frsize <= 64 * 1024 * 1024, "refuse filling a non-isolated volume"
        yield mount
    finally:
        subprocess.run(detach, check=True, capture_output=True)


def full_volume_case(work, native):
    with small_volume(work) as volume, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen(); listener.settimeout(4)
        port = listener.getsockname()[1]
        source = volume / "outbox.sqlite3"
        create_store(source, identity("127.0.0.1", port, "moon-durable-recovery"), blocked=False)
        with closing(sqlite3.connect(source)) as db, db:
            db.execute("DELETE FROM durable_outbox")
            db.execute("UPDATE durable_meta SET next_sequence=1, known_session=0")
        before = snapshot(source)
        with (volume / "filler").open("wb", buffering=0) as filler:
            # A failed 1 MiB write can leave enough space for a small archive.
            # Exhaust the remaining allocation blocks too, on this volume only.
            for size in (1024 * 1024, 4096, 1):
                try:
                    while True:
                        filler.write(bytes(size))
                except OSError as error:
                    assert error.errno == errno.ENOSPC, error
        assert os.statvfs(volume).f_bavail == 0, "volume still has allocatable blocks"
        export = subprocess.run([sys.executable, str(ROOT / "scripts/durable_recovery.py"),
            "export", "--source", str(source), "--archive", str(volume / "archive.mqttrec")],
            text=True, capture_output=True, timeout=15)
        assert export.returncode != 0 and not (volume / "archive.mqttrec").exists(), export.stdout
        driver = NativeProcess([native], env={**os.environ, "D1_PORT": str(port), "D1_OUTBOX": str(source)})
        try:
            # Open may fail before dialing; either path must surface storage
            # failure and must never put PUBLISH on the wire.
            try:
                connection = listener.accept()[0]
            except socket.timeout:
                connection = None
            if connection:
                with connection:
                    connection.settimeout(4)
                    assert recv_packet(connection)[0] == 0x10
                    connection.sendall(b"\x20\x02\x00\x00")
                    try:
                        packet = connection.recv(1)
                        assert packet == b"", packet
                    except ConnectionResetError:
                        pass
            result = driver.finish()
            assert result.returncode != 0 and "Durable" in result.stdout, result.stdout
        finally:
            driver.close()
        assert snapshot(source) == before
        return {"case": "real_ENOSPC", "source_preserved": True, "archive_absent": True,
                "native_result": result.stdout, "operator_result": export.stderr,
                "volume_bytes": os.statvfs(volume).f_blocks * os.statvfs(volume).f_frsize}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=Path(os.environ.get("MQTT_EVIDENCE_DIR", ROOT / "_build/pro-audit/storage")))
    parser.add_argument("--syscalls-only", action="store_true", help="Diagnostic subset, not a complete storage gate")
    args = parser.parse_args(); args.evidence.mkdir(parents=True, exist_ok=True)
    subprocess.run([os.environ.get("MOON", str(ROOT / "scripts/moon.sh")), "build", "--target", "native"], cwd=ROOT, check=True)
    native = list((ROOT / "_build/native/debug/build").glob("**/durable_recovery_driver/durable_recovery_driver.exe"))
    assert len(native) == 1, native
    records = []
    with tempfile.TemporaryDirectory(prefix="mqtt-storage-") as temporary:
        work = Path(temporary).resolve()
        library = build_injector(work)
        for operation in ("fsync", "pwrite"):
            case = work / operation; case.mkdir()
            source = case / "source.sqlite3"
            create_store(source, identity("127.0.0.1", 1883, "old"))
            before = snapshot(source)
            marker = work / f"export-{operation}.marker"
            archive = case / "export.mqttrec"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/durable_recovery.py"),
                "export", "--source", str(source), "--archive", str(archive)],
                env=injection(library, case, operation, marker), text=True, capture_output=True, timeout=15)
            assert marker.exists(), f"syscall injector did not execute: {operation}, {result.stderr}"
            assert result.returncode != 0 and not archive.exists(), result.stdout
            assert snapshot(source) == before
            records.append({"case": f"operator_{operation}_EIO", "injected": marker.read_text(),
                            "source_preserved": True, "archive_absent": True, "stderr": result.stderr})
            (args.evidence / "partial.json").write_text(json.dumps(records, indent=2) + "\n")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0)); listener.listen(); listener.settimeout(4)
                arm = work / f"native-{operation}.arm"
                marker = work / f"native-{operation}.marker"
                outbox = case / "native.sqlite3"
                env = injection(library, case, operation, marker, arm)
                env.update(D1_PORT=str(listener.getsockname()[1]), D1_OUTBOX=str(outbox))
                driver = NativeProcess([native[0]], env=env)
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(4)
                        assert recv_packet(conn)[0] == 0x10
                        conn.sendall(b"\x20\x02\x00\x00")
                        publication = recv_packet(conn)
                        assert publication[0] == 0x32
                        # Arm only after possible-write COMMIT and real PUBLISH.
                        arm.touch()
                        conn.sendall(b"\x40\x02" + publish_id(publication).to_bytes(2, "big"))
                        result = driver.finish()
                    assert marker.exists(), f"native injector did not execute: {operation}"
                    assert "acknowledged id=" not in result.stdout and "Durable" in result.stdout, result.stdout
                    assert len(snapshot(outbox)[1]) == 1, "failed ACK DELETE lost recovery row"
                    records.append({"case": f"native_ACK_DELETE_{operation}_EIO", "injected": marker.read_text(),
                                    "row_retained": True, "result": result.stdout})
                    (args.evidence / "partial.json").write_text(json.dumps(records, indent=2) + "\n")
                finally:
                    driver.close()
        if not args.syscalls_only:
            records.append(full_volume_case(work, native[0]))
    report = {"platform": platform.platform(), "power_loss_tested": False,
              "full_gate": not args.syscalls_only, "passed": True, "cases": records}
    (args.evidence / "storage-faults.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
