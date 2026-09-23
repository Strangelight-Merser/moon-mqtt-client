#!/usr/bin/env python3
"""Native scope lifetimes, sampled outside a controlled child process."""

import argparse
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time

from snapshot import Unsupported, binary_layout, snapshot

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from harness import free_port  # noqa: E402
from soak import Broker  # noqa: E402
from protocol_faults import recv_packet  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--workers", type=int, choices=(1, 16))
    parser.add_argument("--reconnect", action="store_true")
    parser.add_argument("--timeouts", action="store_true")
    parser.add_argument("--durable-reopen", action="store_true")
    parser.add_argument("--storage-failures", action="store_true")
    args = parser.parse_args()
    base = Path(os.environ.get("MQTT_EVIDENCE_DIR", ROOT / "_build" / "task-census"))
    out = (args.artifacts or base / f"census-{time.time_ns()}").resolve()
    out.mkdir(parents=True, exist_ok=False)
    subprocess.run([os.environ.get("MOON", str(ROOT / "scripts/moon.sh")),
                    "build", "--target", "native"], cwd=ROOT, check=True)
    binary = next((ROOT / "_build/native/debug/build").glob(
        "**/task_census/scope_driver/scope_driver.exe"))
    try:
        layout = binary_layout(binary)
    except Unsupported as error:
        (out / "unsupported.json").write_text(json.dumps({"reason": str(error)}) + "\n")
        print("UNSUPPORTED", error)
        raise SystemExit(2)
    (out / "layout.json").write_text(json.dumps(layout, indent=2) + "\n")
    assert sum((args.workers is not None, args.reconnect, args.timeouts,
                args.durable_reopen, args.storage_failures)) <= 1
    broker = None
    listener = None
    peer_thread = None
    peer_stop = threading.Event()
    peer_rows = []
    peer_errors = []
    if args.timeouts:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(.5)

        def serve_timeout_peer():
            while not peer_stop.is_set() and len(peer_rows) < 20:
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    with connection:
                        connection.settimeout(3)
                        assert recv_packet(connection)[0] == 0x10
                        connection.sendall(b"\x20\x02\x00\x00")
                        assert recv_packet(connection)[0] >> 4 == 3
                        # No PUBACK: the native operation deadline owns the result.
                        while connection.recv(4096):
                            pass
                        peer_rows.append({"stage": "publish_received_no_puback", "sequence": len(peer_rows) + 1})
                except Exception as error:
                    peer_errors.append(repr(error))
                    break

        peer_thread = threading.Thread(target=serve_timeout_peer, daemon=True)
        peer_thread.start()
        port = listener.getsockname()[1]
    else:
        broker = Broker(free_port(), out, log_level="normal")
        broker.start()
        port = broker.port
    child_env = os.environ | {"CENSUS_PORT": str(port)}
    if args.workers is not None:
        child_env["CENSUS_WORKERS"] = str(args.workers)
    if args.reconnect:
        child_env["CENSUS_MODE"] = "reconnect"
    if args.timeouts:
        child_env["CENSUS_MODE"] = "timeout"
    if args.durable_reopen or args.storage_failures:
        child_env["CENSUS_MODE"] = "durable_reopen" if args.durable_reopen else "storage_failure"
        outbox = out / "durable.sqlite3"
        child_env["CENSUS_OUTBOX"] = str(outbox)
        if args.storage_failures:
            outbox.write_bytes(b"not a sqlite database")
    process = subprocess.Popen([str(binary)], cwd=ROOT,
                               env=child_env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, bufsize=1)
    rows = queue.Queue()
    raw = []

    def read():
        for line in process.stdout:
            raw.append(line)
            rows.put(json.loads(line))
        rows.put({"event": "eof"})

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    samples = []
    try:
        if args.timeouts:
            schedule = [("cold", 0)] + [("timeout_closed", n) for n in range(1, 21)]
        elif args.reconnect:
            schedule = ([("cold", 0), ("ready", 0)] +
                        [("reconnect_ready", n) for n in range(1, 21)] +
                        [("scope_closed", 20)])
        elif args.durable_reopen or args.storage_failures:
            event = "durable_closed" if args.durable_reopen else "storage_failure"
            schedule = [("cold", 0)] + [(event, n) for n in range(1, 21)]
        elif args.workers is not None:
            schedule = [("cold", 0), ("workers_open", args.workers),
                        ("workers_closed", args.workers)]
        else:
            schedule = [("cold", 0)] + [(event, iteration)
                                      for iteration in range(1, 21)
                                      for event in ("scope_open", "scope_closed")]
        for event, iteration in schedule:
            row = rows.get(timeout=10)
            assert row == {"event": event, "iteration": iteration}, row
            sample = snapshot(process.pid, layout)
            sample.update(event=event, iteration=iteration)
            samples.append(sample)
            (out / "samples.json").write_text(json.dumps(samples, indent=2) + "\n")
            if args.reconnect and event in ("ready", "reconnect_ready") and iteration < 20:
                broker.stop()
                broker.start()
            process.stdin.write("\n")
            process.stdin.flush()
        code = process.wait(timeout=10)
        reader.join(2)
        assert code == 0, f"census driver exited {code}; see driver.stderr.log"
        if args.timeouts:
            peer_thread.join(3)
            assert len(peer_rows) == 20 and not peer_errors, (peer_rows, peer_errors)
        baseline = samples[0]["count"]
        closed = [row["count"] for row in samples if row["event"] in (
            "scope_closed", "workers_closed", "timeout_closed", "durable_closed",
            "storage_failure")]
        opened = [row["count"] for row in samples if row["event"] in ("scope_open", "workers_open")]
        ready_counts = [row["count"] for row in samples if row["event"] == "reconnect_ready"]
        report = {"binary_sha256": layout["sha256"], "baseline": baseline,
                  "scope_open_counts": opened, "scope_closed_counts": closed,
                  "reconnect_ready_counts": ready_counts,
                  "return_to_baseline": all(value == baseline for value in closed),
                  "workers": args.workers,
                  "method": ("SIGSTOP and read-only /proc/pid/mem; SIGCONT in finally"
                             if layout.get("format") == "ELF64 x86-64 PIE" else
                             "LLDB attach/read/detach; no target calls or memory writes"),
                  "scope": layout["scope"], "reconnect_cycles": 20 if args.reconnect else 0,
                  "timeout_cycles": 20 if args.timeouts else 0,
                  "durable_reopen_cycles": 20 if args.durable_reopen else 0,
                  "storage_failure_cycles": 20 if args.storage_failures else 0,
                  "timeout_peer_rows": peer_rows}
        (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        assert report["return_to_baseline"], report
        if args.reconnect:
            assert len(ready_counts) == 20 and len(set(ready_counts)) == 1, report
        print("Task census scope run passed:", out)
    except Unsupported as error:
        (out / "unsupported.json").write_text(json.dumps({
            "reason": str(error), "binary_sha256": layout["sha256"],
            "samples_completed": len(samples),
        }, indent=2) + "\n")
        print("UNSUPPORTED", error)
        raise SystemExit(2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(2)
        (out / "driver.jsonl").write_text("".join(raw))
        (out / "driver.stderr.log").write_text(process.stderr.read())
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        if broker is not None:
            broker.close()
        if listener is not None:
            peer_stop.set()
            listener.close()
        if peer_thread is not None:
            peer_thread.join(3)


if __name__ == "__main__":
    main()
