#!/usr/bin/env python3
"""Small repeatable native baselines. Raw peer and trace overhead are explicit."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import socket
import subprocess
import threading
import time
from harness import stop_process
from durable_outbox import packet, body_offset, publish_id
from mqtt5_runtime import frame
from soak import process_sample, percentile

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = [
    {"name": "qos0-small", "kind": "publish", "qos": 0, "payload": 32},
    {"name": "qos0-1k", "kind": "publish", "qos": 0, "payload": 1024},
    {"name": "qos0-near-limit", "kind": "publish", "qos": 0, "payload": 65000},
    {"name": "qos1-i1-c1", "kind": "publish", "inflight": 1, "workers": 1},
    {"name": "qos1-i1-c16", "kind": "publish", "inflight": 1, "workers": 16},
    {"name": "qos1-i32-c1", "kind": "publish", "inflight": 32, "workers": 1},
    {"name": "qos1-i32-c16", "kind": "publish", "inflight": 32, "workers": 16},
    {"name": "ordinary-stages", "kind": "stages"},
    {"name": "durable-stages", "kind": "durable"},
    {"name": "restore-100", "kind": "subscriptions", "filters": 100},
    {"name": "restore-1000", "kind": "subscriptions", "filters": 1000},
    {"name": "receive-max-1", "kind": "flow", "credit": 1},
    {"name": "receive-max-32", "kind": "flow", "credit": 32},
]


def percentiles(values):
    values = sorted(values)
    return {
        name: values[
            min(len(values) - 1, max(0, int(len(values) * fraction + 0.999999) - 1))
        ]
        if values
        else None
        for name, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1))
    }


class Peer:
    def __init__(self, settings, directory):
        self.settings = settings
        self.errors = []
        self.stopping = False
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.conn = None
        self.wire = {}
        self.wire_ranges = {}
        self.connections = 0
        self.filters = []
        self.sub_count = 0
        self.flow_count = 0
        self.trace = (directory / "wire.jsonl").open("w")
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            while not self.stopping:
                try:
                    conn, _ = self.listener.accept()
                except socket.timeout:
                    continue
                self.conn = conn
                with conn:
                    conn.settimeout(12)
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    connect = packet(conn)
                    o = body_offset(connect)
                    assert connect[0] == 0x10
                    version = connect[o + 6]
                    properties = b"\x03\x21" + self.settings.get("credit", 32).to_bytes(
                        2, "big"
                    )
                    conn.sendall(
                        frame(0x20, b"\0\0" + (properties if version == 5 else b""))
                    )
                    self.connections += 1
                    filters = 0
                    held = []
                    flow_released = False
                    while not self.stopping:
                        try:
                            data = packet(conn)
                        except EOFError:
                            break
                        typ = data[0] >> 4
                        o = body_offset(data)
                        if typ == 14:
                            break
                        if typ == 12:
                            conn.sendall(b"\xd0\0")
                            continue
                        if typ == 8:
                            pid = data[o : o + 2]
                            i = o + 2 + (1 if version == 5 else 0)
                            count = 0
                            while i < len(data):
                                n = int.from_bytes(data[i : i + 2], "big")
                                i += 3 + n
                                count += 1
                            filters += count
                            self.sub_count += count
                            conn.sendall(
                                frame(
                                    0x90,
                                    pid
                                    + (b"\0" if version == 5 else b"")
                                    + b"\x01" * count,
                                )
                            )
                            if self.settings["kind"] == "flow":
                                assert len(held) == (
                                    1 if self.settings["credit"] == 1 else 2
                                ), held
                                for identifier in held:
                                    conn.sendall(frame(0x40, identifier))
                                self.flow_count += 1
                                flow_released = self.settings["credit"] == 1
                                held = []
                            continue
                        assert typ == 3, data[:10]
                        n = int.from_bytes(data[o : o + 2], "big")
                        topic = data[o + 2 : o + 2 + n].decode()
                        qos = (data[0] >> 1) & 3
                        if topic.startswith("bench-reset-"):
                            conn.sendall(
                                frame(0x40, publish_id(data).to_bytes(2, "big"))
                            )
                            assert filters == self.settings["filters"], (
                                filters,
                                self.settings,
                            )
                            self.filters.append(filters)
                            break
                        prefix, phase, worker, seq = topic.split("-")
                        assert prefix == "bench"
                        key = f"{phase}/{worker}"
                        assert int(seq) > self.wire.get(key, 0), topic
                        self.wire[key] = int(seq)
                        ranges = self.wire_ranges.setdefault(key, [])
                        if ranges and ranges[-1][1] + 1 == int(seq):
                            ranges[-1][1] = int(seq)
                        else:
                            ranges.append([int(seq), int(seq)])
                        if self.settings["kind"] == "flow":
                            identifier = publish_id(data).to_bytes(2, "big")
                            if flow_released:
                                conn.sendall(frame(0x40, identifier))
                                flow_released = False
                            else:
                                held.append(identifier)
                            continue
                        if self.settings["kind"] in ("durable", "stages"):
                            self.trace.write(
                                json.dumps(
                                    {
                                        "id": topic,
                                        "stage": "wire",
                                        "ns": time.clock_gettime_ns(
                                            time.CLOCK_MONOTONIC
                                        ),
                                    }
                                )
                                + "\n"
                            )
                        if qos:
                            if self.settings["kind"] in ("durable", "stages"):
                                self.trace.write(
                                    json.dumps(
                                        {
                                            "id": topic,
                                            "stage": "ack_send_begin",
                                            "ns": time.clock_gettime_ns(
                                                time.CLOCK_MONOTONIC
                                            ),
                                        }
                                    )
                                    + "\n"
                                )
                            conn.sendall(
                                frame(0x40, publish_id(data).to_bytes(2, "big"))
                            )
                            if self.settings["kind"] in ("durable", "stages"):
                                self.trace.write(
                                    json.dumps(
                                        {
                                            "id": topic,
                                            "stage": "ack_sent",
                                            "ns": time.clock_gettime_ns(
                                                time.CLOCK_MONOTONIC
                                            ),
                                        }
                                    )
                                    + "\n"
                                )
                self.conn = None
        except BaseException as error:
            if not self.stopping:
                self.errors.append(repr(error))
        finally:
            self.trace.close()

    def close(self):
        self.stopping = True
        if self.conn:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(2)
        self.listener.close()
        assert not self.thread.is_alive(), "peer did not close"


def trial(binary, settings, warmup, seconds, directory):
    directory.mkdir(parents=True, exist_ok=False)
    peer = Peer(settings, directory)
    env = {
        **os.environ,
        "MOONBIT_ASYNC_CHECK_FD_LEAK": "1",
        "MQTT_BENCH_PORT": str(peer.port),
        "MQTT_BENCH_WARMUP": str(warmup),
        "MQTT_BENCH_SECONDS": str(seconds),
        "MQTT_BENCH_DB": str(directory / "outbox.sqlite3"),
        "MQTT_BENCH_TRACE": str(directory / "sqlite.jsonl"),
    }
    env.update({"MQTT_BENCH_" + k.upper(): str(v) for k, v in settings.items()})
    stdout = (directory / "native.jsonl").open("w")
    stderr = (directory / "stderr.log").open("w")
    identity = {
        "binary": str(binary),
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
    process = subprocess.Popen(
        [str(binary)],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        bufsize=1,
    )
    identity["pid"] = process.pid
    events = queue.Queue()
    samples = []
    sampling = threading.Event()
    rows = []

    def read():
        for line in process.stdout:
            stdout.write(line)
            stdout.flush()
            try:
                row = json.loads(line)
            except ValueError:
                row = {"event": "invalid", "line": line}
            events.put(row)
            rows.append(row)
        events.put({"event": "process_eof"})

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def barrier(kind, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = events.get(timeout=max(0.01, deadline - time.monotonic()))
            if row["event"] == kind:
                return row
            if row["event"] == "process_eof":
                raise AssertionError((kind, (directory / "stderr.log").read_text()))
        raise TimeoutError(kind)

    def release():
        process.stdin.write("\n")
        process.stdin.flush()

    def sample():
        while not sampling.wait(1):
            if process.poll() is not None:
                break
            samples.append(
                [
                    time.clock_gettime_ns(time.CLOCK_MONOTONIC),
                    *process_sample(process.pid),
                ]
            )

    sampler = threading.Thread(target=sample, daemon=True)
    try:
        barrier("before_scope", 10)
        before = process_sample(process.pid)
        release()
        barrier("warmed", warmup + 30)
        warmed = process_sample(process.pid)
        sampler.start()
        release()
        barrier("scope_closed", seconds + 40)
        post = [process_sample(process.pid) for _ in range(3)]
        release()
        code = process.wait(10)
        assert code == 0, (code, (directory / "stderr.log").read_text())
    finally:
        sampling.set()
        if sampler.ident:
            sampler.join(3)
        stop_process(process)
        reader.join(3)
        process.stdout.close()
        process.stdin.close()
        stdout.close()
        stderr.close()
        peer.close()
    assert not peer.errors, peer.errors
    assert not any(x["event"] == "invalid" for x in rows)
    phases = [r for r in rows if r.get("phase") == "sample"]
    start = next(r["ns"] for r in phases if r["event"] == "phase_start")
    end = next(r["ns"] for r in phases if r["event"] == "phase_end")
    histogram = {}
    for row in phases:
        if row["event"] == "outcomes":
            key = f"sample/{row['worker']}"
            ranges = [
                r
                for r in phases
                if r["event"] == "range" and r["worker"] == row["worker"]
            ]
            expected = 1
            counts = {k: 0 for k in ("completed", "rejected", "unknown", "not_sent")}
            successful = []
            for r in ranges:
                assert r["first"] == expected and r["last"] >= r["first"]
                expected = r["last"] + 1
                counts[r["outcome"]] += r["last"] - r["first"] + 1
                if r["outcome"] == "completed":
                    successful.append([r["first"], r["last"]])
            assert expected == row["last"] + 1 and all(
                row[k] == v for k, v in counts.items()
            )
            assert row["unknown"] == 0 and row["not_sent"] == 0, row
            assert successful == peer.wire_ranges.get(key, []), (
                key,
                successful,
                peer.wire_ranges.get(key, []),
            )
        if row["event"] == "bucket":
            histogram[row["upper_us"]] = (
                histogram.get(row["upper_us"], 0) + row["count"]
            )
    count = sum(r["completed"] for r in phases if r["event"] == "outcomes")
    cycles = sum(1 for row in phases if row["event"] == "cycle")
    if settings["kind"] == "flow":
        assert peer.wire["sample/0"] == peer.wire["sample/1"] == cycles

    result = {
        "settings": settings,
        "native": identity,
        "warmup_seconds": warmup,
        "sample_seconds": seconds,
        "actual_sample_seconds": (end - start) / 1e9,
        "completed": count,
        "outcomes": {
            k: sum(r[k] for r in phases if r["event"] == "outcomes")
            for k in ("accepted", "rejected", "completed", "unknown", "not_sent")
        },
        "throughput_per_s": count / ((end - start) / 1e9),
        "completion_semantics": "socket write; no MQTT acknowledgement"
        if settings.get("qos", 1) == 0
        else "QoS1 PUBACK",
        "latency_us": {
            k: percentile(histogram, f)
            for k, f in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
        },
        "latency_censored_above_100ms": histogram.get(100010, 0),
        "cycles_ns": percentiles([r["ns"] for r in phases if r["event"] == "cycle"]),
        "nonpublish_ns": percentiles(
            [r["ns"] for r in phases if r["event"] == "nonpublish"]
        ),
        "wire_accounting": peer.wire,
        "wire_ranges": peer.wire_ranges,
        "restore_cycles": len(peer.filters),
        "connections": peer.connections,
        "flow_cycles": peer.flow_count,
        "flow_sample_outcomes": {
            "accepted": cycles * 2,
            "acknowledged": cycles * 2,
            "rejected": 0,
            "unknown": 0,
        }
        if settings["kind"] == "flow"
        else None,
        "resources": {
            "before_scope": before,
            "warmed": warmed,
            "live": samples,
            "post_scope": post,
            "fields": ["rss_kib", "numeric_fds", "OS_threads"],
            "internal_task_census": "unavailable; no public instrumentation added",
        },
    }
    if settings["kind"] in ("stages", "durable"):
        evidence = {}
        for file in (directory / "wire.jsonl", directory / "sqlite.jsonl"):
            if file.exists():
                for line in file.read_text().splitlines():
                    x = json.loads(line)
                    evidence.setdefault(x["id"], {})[x["stage"]] = x["ns"]
        durations = {
            "admission": [],
            "ack_to_completion_lower_bound": [],
            "ack_to_completion_upper_bound": [],
            "total": [],
            "admission_commit": [],
            "delete_commit": [],
        }
        for row in rows:
            if row["event"] != "stages" or not row["id"].startswith("bench-sample-"):
                continue
            x = evidence[row["id"]]
            begin = row["begin_ns"]
            done = row["completed_ns"]
            assert begin <= row["admitted_ns"] <= done and begin <= x["wire"] <= done
            # ACK send completion is an upper bound; scheduling can let the
            # native reader finish while Python returns from sendall.
            durations["admission"].append(row["admitted_ns"] - begin)
            durations["ack_to_completion_lower_bound"].append(
                max(0, done - x["ack_sent"])
            )
            durations["ack_to_completion_upper_bound"].append(
                done - x["ack_send_begin"]
            )
            assert x["ack_send_begin"] <= done
            durations["total"].append(done - begin)
            if settings["kind"] == "durable":
                assert begin <= x["admission_commit"] <= row["admitted_ns"]
                assert (
                    x["admission_commit"]
                    <= x["wire"]
                    <= x["ack_send_begin"]
                    <= x["delete_commit"]
                    <= done
                )
                durations["admission_commit"].append(x["admission_commit"] - begin)
                durations["delete_commit"].append(x["delete_commit"] - x["wire"])
        result["stage_duration_ns"] = {
            k: percentiles(v) for k, v in durations.items() if v
        }
        result["stage_semantics"] = (
            "ACK is peer send-completion upper bound; delete duration starts at wire receive, not business processing. SQLite PROFILE follows COMMIT. Trace adds I/O overhead."
        )
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    workloads = [w for w in WORKLOADS if not args.only or w["name"] in args.only]
    assert workloads and (
        not args.only or set(args.only) <= {w["name"] for w in WORKLOADS}
    )
    out = (
        args.artifacts
        or Path(os.environ.get("MQTT_EVIDENCE_DIR", ROOT / "_build/pro-audit"))
        / f"benchmark-{time.time_ns()}"
    ).resolve()
    out.mkdir(parents=True, exist_ok=False)
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
    binary = out / "benchmark_driver.exe"
    shutil.copyfile(
        next(
            (ROOT / "_build/native/debug/build").glob(
                "**/benchmark_driver/benchmark_driver.exe"
            )
        ),
        binary,
    )
    binary.chmod(0o700)
    report = {
        "platform": platform.platform(),
        "clock": "CLOCK_MONOTONIC ns in native and peer; same host",
        "results": [],
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "cpu": platform.processor(),
        "python": platform.python_version(),
        "moon": subprocess.check_output(
            [os.environ.get("MOON", str(ROOT / "scripts/moon.sh")), "version"],
            cwd=ROOT,
            text=True,
        ).strip(),
        "limitations": [
            "local raw Python peer overhead included; not a maximum client throughput claim",
            "instrumented stages include stdout/SQLite trace cost",
            "debug native executable, pinned dependencies; thresholds await Pro",
        ],
        "full_default_matrix": not args.only
        and args.warmup == 5
        and args.seconds == 30
        and args.repeats == 3,
    }
    for w in workloads:
        for i in range(args.repeats):
            print(f"RUN {w['name']} repeat {i + 1}", flush=True)
            report["results"].append(
                trial(
                    binary, w, args.warmup, args.seconds, out / f"{w['name']}-{i + 1}"
                )
            )
            (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Benchmark evidence: {out}", flush=True)


if __name__ == "__main__":
    main()
