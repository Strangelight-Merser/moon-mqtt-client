"""Small process/port/evidence helpers. Protocol assertions stay in each suite."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def integration_fixture():
    spec = importlib.util.spec_from_file_location("mqtt_integration_fixture", ROOT / "tests/integration/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_process(process, timeout=2):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def retain_log(path, label):
    destination = os.environ.get("MQTT_EVIDENCE_DIR")
    if destination and Path(path).is_file():
        directory = Path(destination) / "broker-logs"
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, directory / f"{label}-{uuid.uuid4().hex}.log")


class NativeProcess:
    """Unbuffered line barriers, exact executable identity and bounded cleanup."""
    def __init__(self, command, *, env=None, cwd=ROOT, events=None):
        self.command = [str(x) for x in command]
        self.lines = []
        self.events = events if events is not None else queue.Queue()
        self.process = subprocess.Popen(self.command, cwd=cwd, env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True)
        self.identity = {"pid": self.process.pid, "command": self.command,
                         "binary_sha256": hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()}
        self.reader = threading.Thread(target=self._collect, daemon=True)
        self.reader.start()

    def _collect(self):
        for line in self.process.stdout:
            self.lines.append(line)
            self.events.put(line.rstrip())

    def expect(self, prefix, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.events.get(timeout=max(.001, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line.startswith(prefix):
                return line
        raise AssertionError(f"missing {prefix!r}; output={''.join(self.lines)!r}")

    def finish(self, timeout=10):
        self.process.wait(timeout)
        self.reader.join(2)
        assert not self.reader.is_alive(), "process output reader did not finish"
        return subprocess.CompletedProcess(self.command, self.process.returncode, "".join(self.lines), "")

    def close(self):
        if self.process.stdout.closed:
            return
        stop_process(self.process)
        self.reader.join(2)
        self.process.stdout.close()
        destination = os.environ.get("MQTT_EVIDENCE_DIR")
        if destination:
            directory = Path(destination) / "processes"
            directory.mkdir(parents=True, exist_ok=True)
            name = f"{Path(self.command[0]).stem}-{self.process.pid}-{uuid.uuid4().hex}"
            (directory / (name + ".log")).write_text("".join(self.lines))
            (directory / (name + ".json")).write_text(json.dumps(self.identity, indent=2) + "\n")
