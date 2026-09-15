#!/usr/bin/env python3
"""Long-running local MQTT soak with broker restarts and independent observation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import statistics
import subprocess
import tempfile
import threading
import time

import paho.mqtt.client as paho


ROOT = Path(__file__).resolve().parents[1]
MOON = Path(os.environ.get("MOON", ROOT / "scripts/moon.sh"))
BROKER = Path(os.environ.get("MOSQUITTO", ROOT / ".tools/mosquitto"))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port: int, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return
        except OSError:
            time.sleep(.025)
    raise TimeoutError(f"broker port {port} did not open")


class Broker:
    def __init__(self, port: int, artifact_dir: Path, log_level: str = "quiet",
                 log_limit_bytes: int = 64 * 1024 * 1024):
        self.port = port
        self.artifact_dir = artifact_dir
        self.log_level = log_level
        self.log_limit_bytes = log_limit_bytes
        self.process: subprocess.Popen[str] | None = None
        self.log_path = artifact_dir / "broker.log"
        self.log = open(self.log_path, "a", encoding="utf-8")
        # A 30-minute, 16-worker QoS 1 run can emit an enormous per-packet broker
        # log. The default is quiet (errors and warnings only); `normal` adds
        # connection notices and `debug` restores `log_type all` for diagnosis.
        # The file is also capped in `_cap_log`, so a long run cannot leave
        # multi-gigabyte artifacts behind.
        self.config = artifact_dir / "mosquitto.conf"
        self.config.write_text(
            f"listener {port} 127.0.0.1\nallow_anonymous true\n"
            + self._log_directives()
        )

    def _log_directives(self) -> str:
        if self.log_level == "debug":
            return "log_type all\n"
        if self.log_level == "normal":
            return "log_type notice\nlog_type warning\nlog_type error\n"
        return "log_type warning\nlog_type error\n"

    def _cap_log(self) -> None:
        """Keep the newest evidence within the configured size limit."""
        if self.log_limit_bytes <= 0:
            return
        try:
            if self.log_path.stat().st_size <= self.log_limit_bytes:
                return
            self.log.flush()
            self.log.close()
            keep_from = self.log_path.stat().st_size - self.log_limit_bytes // 2
            with open(self.log_path, "rb") as source:
                source.seek(keep_from)
                tail = source.read()
            with open(self.log_path, "wb") as target:
                target.write(b"[soak.py] broker log truncated to newest bytes\n")
                target.write(tail)
            self.log = open(self.log_path, "a", encoding="utf-8")
        except OSError:
            # Evidence capping must never fail the soak itself.
            if self.log.closed:
                self.log = open(self.log_path, "a", encoding="utf-8")

    def start(self) -> None:
        # No `-v` here: `log_type` in the config already selects what is
        # logged, and `-v` would force per-packet output regardless.
        self.process = subprocess.Popen(
            [str(BROKER), "-c", str(self.config)],
            stdout=self.log, stderr=subprocess.STDOUT, text=True,
        )
        wait_port(self.port)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._cap_log()

    def close(self) -> None:
        self.stop()
        if not self.log.closed:
            self.log.close()


class SamplingUnavailable(RuntimeError):
    """Raised when the platform denies the process inspection this needs."""


def process_sample(pid: int) -> tuple[int, int, int]:
    """Return (rss_kib, open_fds, threads) for a live process.

    Raises `SamplingUnavailable` when neither `/proc` nor `ps` can be read, so
    the caller can record the gap instead of reporting zeros as measurements.
    """
    if subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode:
        return 0, 0, 0
    proc = Path(f"/proc/{pid}")
    rss_kib = 0
    if (proc / "status").is_file():
        for line in (proc / "status").read_text(errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                rss_kib = int(line.split()[1])
                break
    else:
        try:
            rss_text = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True,
                text=True, check=True,
            ).stdout.strip()
            rss_kib = int(rss_text or 0)
        except (OSError, subprocess.CalledProcessError) as error:
            raise SamplingUnavailable(f"cannot read RSS: {error}") from error
    fd_count = 0
    if (proc / "fd").is_dir():
        fd_count = len(list((proc / "fd").iterdir()))
    else:
        lsof = shutil.which("lsof")
        if lsof:
            output = subprocess.run(
                [lsof, "-n", "-p", str(pid)], capture_output=True, text=True
            ).stdout
            fd_count = max(0, len(output.splitlines()) - 1)
    task_count = 0
    if (proc / "task").is_dir():
        task_count = len(list((proc / "task").iterdir()))
    else:
        threads_text = subprocess.run(
            ["ps", "-M", "-p", str(pid)], capture_output=True, text=True
        ).stdout
        task_count = max(0, len(threads_text.splitlines()) - 1)
    return rss_kib, fd_count, task_count


def percentile(histogram: dict[int, int], fraction: float) -> int | None:
    total = sum(histogram.values())
    if not total:
        return None
    target = max(1, int(total * fraction + .999999))
    cumulative = 0
    for latency, count in sorted(histogram.items()):
        cumulative += count
        if cumulative >= target:
            return latency
    raise AssertionError("histogram accounting error")


def run_soak(duration: int, cycles: int, payload_bytes: int, concurrency: int,
             downtime: float, artifacts: Path, broker_log: str = "quiet",
             broker_log_limit_mb: int = 64) -> dict:
    artifacts.mkdir(parents=True, exist_ok=True)
    port = free_port()
    broker = Broker(port, artifacts, log_level=broker_log,
                    log_limit_bytes=broker_log_limit_mb * 1024 * 1024)
    broker.start()
    observed = {"messages": 0, "invalid": 0, "connections": 0}
    oracle_ready = threading.Event()
    oracle = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="paho-soak-oracle")
    oracle.reconnect_delay_set(min_delay=1, max_delay=2)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            observed["connections"] += 1
            client.subscribe("soak/#", qos=1)
            oracle_ready.set()

    def on_message(client, userdata, message):
        observed["messages"] += 1
        payload = message.payload
        if len(payload) != payload_bytes or any(
            value != index % 251 for index, value in enumerate(payload)
        ):
            observed["invalid"] += 1

    oracle.on_connect = on_connect
    oracle.on_message = on_message
    oracle.connect("127.0.0.1", port)
    oracle.loop_start()
    if not oracle_ready.wait(4):
        raise TimeoutError("Paho observer did not subscribe")

    log_path = artifacts / "driver.jsonl"
    stderr_path = artifacts / "driver.stderr.log"
    log_file = open(log_path, "w", encoding="utf-8")
    stderr_file = open(stderr_path, "w", encoding="utf-8")
    env = os.environ | {
        "MQTT_SOAK_PORT": str(port),
        "MQTT_SOAK_DURATION_SECONDS": str(duration),
        "MQTT_SOAK_CONCURRENCY": str(concurrency),
        "MQTT_SOAK_PAYLOAD_BYTES": str(payload_bytes),
    }
    driver = subprocess.Popen(
        [str(MOON), "run", "examples/soak_driver", "--target", "native"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=stderr_file,
        text=True, bufsize=1,
    )
    assert driver.stdout
    events: queue.Queue[dict] = queue.Queue()

    def read_driver() -> None:
        for line in driver.stdout:
            log_file.write(line)
            log_file.flush()
            try:
                events.put(json.loads(line))
            except json.JSONDecodeError:
                events.put({"event": "invalid_json", "line": line.rstrip()})

    reader = threading.Thread(target=read_driver, daemon=True)
    reader.start()

    def next_event(kind: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            seen.append(event)
            if event.get("event") == kind:
                return event
        raise TimeoutError(f"did not receive {kind}; recent events={seen[-5:]}")

    ready = next_event("ready", 15)
    wall_started = time.monotonic()
    samples: list[dict] = []
    sampling = True
    sample_errors: list[str] = []

    def sample_resources() -> None:
        while sampling and driver.poll() is None:
            try:
                rss, fds, tasks = process_sample(driver.pid)
                samples.append({
                    "elapsed_s": time.monotonic() - wall_started,
                    "rss_kib": rss, "fds": fds, "tasks": tasks,
                })
            except Exception as error:  # noqa: BLE001 - evidence must not crash
                # Sandboxes can deny `ps`/`lsof`. Record it once and keep the
                # soak running; a dead sampler thread would silently drop all
                # resource evidence instead.
                if len(sample_errors) < 3:
                    sample_errors.append(f"{type(error).__name__}: {error}")
            time.sleep(1)

    sampler = threading.Thread(target=sample_resources, daemon=True)
    sampler.start()
    recoveries = []
    latest_generation = 1
    try:
        for cycle in range(cycles):
            target = wall_started + (cycle + 1) * duration / (cycles + 1)
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            broker.stop()
            stopped = time.monotonic()
            time.sleep(downtime)
            oracle_ready.clear()
            broker.start()
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                event = next_event("connected", max(.1, deadline - time.monotonic()))
                generation = int(event["generation"])
                if generation > latest_generation:
                    latest_generation = generation
                    recoveries.append(time.monotonic() - stopped)
                    break
            else:
                raise TimeoutError(f"cycle {cycle + 1} did not reconnect")
        final = next_event("final", duration + 30)
        code = driver.wait(15)
        if code != 0:
            raise RuntimeError(f"soak driver exited {code}; see {stderr_path}")
    finally:
        sampling = False
        sampler.join(3)
        if driver.poll() is None:
            driver.kill()
            driver.wait()
        driver.stdout.close()
        reader.join(3)
        oracle.disconnect()
        oracle.loop_stop()
        broker.close()
        log_file.close()
        stderr_file.close()

    histogram: dict[int, int] = {}
    while not events.empty():
        event = events.get_nowait()
        if event.get("event") == "latency_bucket":
            histogram[int(event["ms"])] = int(event["count"])
    # Buckets normally precede `final` and were consumed by next_event.
    for line in log_path.read_text().splitlines():
        event = json.loads(line)
        if event.get("event") == "latency_bucket":
            histogram[int(event["ms"])] = int(event["count"])

    elapsed = time.monotonic() - wall_started
    after_exit_rss, after_exit_fds, after_exit_tasks = process_sample(driver.pid)
    evidence_bytes = {
        path.name: path.stat().st_size
        for path in sorted(artifacts.iterdir()) if path.is_file()
    }
    result = {
        "parameters": {
            "duration_seconds": duration, "disconnect_cycles": cycles,
            "payload_bytes": payload_bytes, "concurrency": concurrency,
            "downtime_seconds": downtime,
            "broker_log_level": broker_log,
            "broker_log_limit_mb": broker_log_limit_mb,
        },
        "evidence_bytes": evidence_bytes,
        "driver": final,
        "elapsed_seconds": elapsed,
        "throughput_ack_per_second": final["acknowledged"] / elapsed,
        "latency_ms": {
            "p50": percentile(histogram, .50),
            "p95": percentile(histogram, .95),
            "p99": percentile(histogram, .99),
            "max": max(histogram, default=None),
        },
        "recovery_seconds": {
            "count": len(recoveries),
            "min": min(recoveries, default=None),
            "median": statistics.median(recoveries) if recoveries else None,
            "p95": sorted(recoveries)[max(0, int(len(recoveries) * .95) - 1)] if recoveries else None,
            "max": max(recoveries, default=None),
        },
        "observer": observed,
        "resources": {
            "error": sample_errors[0] if sample_errors else None,
            "samples": len(samples),
            "rss_start_kib": samples[0]["rss_kib"] if samples else None,
            "rss_end_kib": samples[-1]["rss_kib"] if samples else None,
            "rss_peak_kib": max((s["rss_kib"] for s in samples), default=None),
            "fd_start": samples[0]["fds"] if samples else None,
            "fd_end": samples[-1]["fds"] if samples else None,
            "fd_peak": max((s["fds"] for s in samples), default=None),
            "tasks_start": samples[0]["tasks"] if samples else None,
            "tasks_end": samples[-1]["tasks"] if samples else None,
            "tasks_peak": max((s["tasks"] for s in samples), default=None),
            "process_exit_code": code,
            "after_exit_rss_kib": after_exit_rss,
            "after_exit_fds": after_exit_fds,
            "after_exit_tasks": after_exit_tasks,
        },
        "ready": ready,
    }
    failures = []
    expected = {
        "active_workers": 0,
        "pending": 0,
        "business": 0,
        "control": 0,
        "event_queue": 0,
        "generation": cycles + 1,
        "reconnects": cycles,
        "disconnects": cycles,
    }
    for field, wanted in expected.items():
        if final.get(field) != wanted:
            failures.append(f"final.{field}={final.get(field)!r}, expected {wanted!r}")
    if len(recoveries) != cycles:
        failures.append(f"recovery count={len(recoveries)}, expected {cycles}")
    if observed["invalid"] != 0:
        failures.append(f"observer.invalid={observed['invalid']}, expected 0")
    if observed["messages"] <= 0:
        failures.append("observer saw no messages")
    if code != 0:
        failures.append(f"driver exit code={code}, expected 0")
    if after_exit_fds != 0 or after_exit_tasks != 0:
        failures.append(
            f"driver retained resources after exit: fds={after_exit_fds}, tasks={after_exit_tasks}"
        )
    if sample_errors and not samples:
        # The soak can still be valid, but it must not imply resource evidence
        # it does not have.
        result["resources"]["resource_evidence_available"] = False
    elif samples:
        result["resources"]["resource_evidence_available"] = True
        start_rss, end_rss = samples[0]["rss_kib"], samples[-1]["rss_kib"]
        if start_rss and end_rss > start_rss * 2 + 65536:
            failures.append(
                f"rss grew from {start_rss} KiB to {end_rss} KiB during the soak"
            )
    result["gate"] = {"passed": not failures, "failures": failures}
    (artifacts / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    (artifacts / "resources.jsonl").write_text(
        "".join(json.dumps(sample) + "\n" for sample in samples)
    )
    if failures:
        raise AssertionError("; ".join(failures))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=1800)
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--downtime", type=float, default=.2)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument(
        "--broker-log", choices=["quiet", "normal", "debug"], default="quiet",
        help="mosquitto verbosity; quiet keeps only warnings and errors",
    )
    parser.add_argument(
        "--broker-log-limit-mb", type=int, default=64,
        help="truncate broker.log to this many megabytes (0 disables the cap)",
    )
    args = parser.parse_args()
    artifacts = args.artifacts or Path(tempfile.mkdtemp(prefix="moon-mqtt-soak-"))
    result = run_soak(
        args.duration, args.cycles, args.payload_bytes, args.concurrency,
        args.downtime, artifacts, args.broker_log, args.broker_log_limit_mb,
    )
    print(json.dumps(result, indent=2), flush=True)
    print(f"soak evidence: {artifacts}", flush=True)


if __name__ == "__main__":
    main()
