#!/usr/bin/env python3
"""Six deterministic host crash windows; not electrical power-loss/HIL evidence."""

import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import sqlite3
import subprocess
import threading
import time
from durable_outbox import packet, publish_id
from harness import stop_process

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "before_admission_commit",
    "admission_commit",
    "possible_write_commit",
    "after_publish",
    "before_delete",
    "delete_commit",
)


def run_case(stage, binary, recovery, directory):
    directory.mkdir()
    db = directory / "outbox.sqlite3"
    trace = directory / "trace.jsonl"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(10)
        port = listener.getsockname()[1]
        env = {
            **os.environ,
            "MQTT_BENCH_KIND": "durable",
            "MQTT_BENCH_WARMUP": "0",
            "MQTT_BENCH_SECONDS": "1",
            "MQTT_BENCH_PORT": str(port),
            "MQTT_BENCH_DB": str(db),
            "MQTT_BENCH_TRACE": str(trace),
            "MOONBIT_ASYNC_CHECK_FD_LEAK": "1",
        }
        if stage != "after_publish":
            env["MQTT_BENCH_CRASH"] = stage
        else:
            env.pop("MQTT_BENCH_CRASH", None)
        proc = subprocess.Popen(
            [str(binary)],
            env=env,
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        rows = []
        events = queue.Queue()
        first_wire = None

        def read():
            for line in proc.stdout:
                rows.append(line)
                events.put(json.loads(line))

        reader = threading.Thread(target=read, daemon=True)
        reader.start()

        def wait(event):
            while True:
                row = events.get(timeout=10)
                if row["event"] == event:
                    return

        def release():
            proc.stdin.write("\n")
            proc.stdin.flush()

        try:
            wait("before_scope")
            release()
            with listener.accept()[0] as conn:
                conn.settimeout(10)
                assert packet(conn)[0] == 0x10
                conn.sendall(b"\x20\x02\0\0")
                wait("warmed")
                release()
                if stage in ("after_publish", "before_delete", "delete_commit"):
                    first_wire = packet(conn)
                    assert first_wire[0] == 0x32
                    if stage == "after_publish":
                        proc.kill()
                    else:
                        conn.sendall(
                            b"\x40\x02" + publish_id(first_wire).to_bytes(2, "big")
                        )
                assert conn.recv(1) == b"", (
                    "unexpected PUBLISH or caller completion window"
                )
            code = proc.wait(10)
            reader.join(2)
            assert code == (-9 if stage == "after_publish" else 86), (
                code,
                proc.stderr.read(),
            )
            assert not any(json.loads(x)["event"] == "stages" for x in rows), (
                "caller observed completion before crash"
            )
            with sqlite3.connect(db) as connection:
                assert (
                    connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                )
                records = connection.execute(
                    "SELECT delivery_id,packet_id,ever_started,attempts FROM durable_outbox"
                ).fetchall()
            wanted = 0 if stage in ("before_admission_commit", "delete_commit") else 1
            assert len(records) == wanted, (stage, records)
            if records:
                assert records[0][2] == (0 if stage == "admission_commit" else 1)
            evidence = {
                "stage": stage,
                "pid": proc.pid,
                "exit": code,
                "first_wire_hex": first_wire.hex() if first_wire else None,
                "rows_after_crash": records,
                "crash_mechanism": "SIGKILL after raw peer observed complete PUBLISH"
                if stage == "after_publish"
                else "test-only SQLite trace callback _exit at exact statement boundary",
                "physical_result": "evidence_insufficient; no hardware observed",
            }
            # Reopen through the native production storage path, including the
            # retired/identity/schema guards rather than trusting Python alone.
            resume_env = {
                **os.environ,
                "D1_PORT": str(port),
                "D1_CLIENT_ID": "moon-benchmark",
                "D1_OUTBOX": str(db),
                "D1_MODE": "inspect",
            }
            inspect = subprocess.run(
                [str(recovery)],
                env=resume_env,
                text=True,
                capture_output=True,
                timeout=10,
            )
            assert inspect.returncode == 0, inspect.stdout + inspect.stderr
            assert f"records={wanted}" in inspect.stdout
            (directory / "native-inspect.log").write_text(
                inspect.stdout + inspect.stderr
            )
            if records:
                resume_env["D1_MODE"] = "run"
                resumed = subprocess.Popen(
                    [str(recovery)],
                    env=resume_env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(10)
                        assert packet(conn)[0] == 0x10
                        conn.sendall(b"\x20\x02\x01\0")
                        replay = packet(conn)
                        assert publish_id(replay) == records[0][1]
                        assert bool(replay[0] & 8) == bool(records[0][2]), (
                            stage,
                            replay.hex(),
                        )
                        conn.sendall(
                            b"\x40\x02" + publish_id(replay).to_bytes(2, "big")
                        )
                        assert packet(conn) == b"\xe0\0"
                    out, err = resumed.communicate(timeout=10)
                    assert resumed.returncode == 0, out + err
                    evidence.update(
                        recovery_wire_hex=replay.hex(),
                        same_packet_id=True,
                        dup=bool(replay[0] & 8),
                        native_recovered=True,
                    )
                    (directory / "recovered.log").write_text(out + err)
                    with sqlite3.connect(db) as connection:
                        assert (
                            connection.execute(
                                "SELECT count(*) FROM durable_outbox"
                            ).fetchone()[0]
                            == 0
                        )
                finally:
                    stop_process(resumed)
                    resumed.stdout.close()
                    resumed.stderr.close()
            (directory / "result.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
            return evidence
        finally:
            stop_process(proc)
            reader.join(2)
            (directory / "native.jsonl").write_text("".join(rows))
            (directory / "stderr.log").write_text(proc.stderr.read())
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()


def main():
    out = (
        Path(os.environ.get("MQTT_EVIDENCE_DIR", ROOT / "_build/pro-audit"))
        / f"durable-boundaries-{time.time_ns()}"
    )
    out.mkdir(parents=True)
    subprocess.run(
        [
            os.environ.get("MOON", str(ROOT / "scripts/moon.sh")),
            "build",
            "--target",
            "native",
        ],
        cwd=ROOT,
        check=True,
    )
    base = ROOT / "_build/native/debug/build"
    driver = next(base.glob("**/benchmark_driver/benchmark_driver.exe"))
    recovery = next(base.glob("**/durable_recovery_driver/durable_recovery_driver.exe"))
    report = {
        "binary_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "recovery_sha256": hashlib.sha256(recovery.read_bytes()).hexdigest(),
        "physical_evidence": False,
        "results": [],
    }
    for stage in CASES:
        print("RUN", stage, flush=True)
        report["results"].append(run_case(stage, driver, recovery, out / stage))
        (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Six host crash boundaries passed:", out, flush=True)


if __name__ == "__main__":
    main()
