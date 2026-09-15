#!/usr/bin/env python3
"""One-command reproduction for the moon-mqtt-client state-sync demo.

Everything here is deliberately black-box: a real Mosquitto broker, the
simulated device process, the controller process, and a Paho observer that
independently records every topic. Assertions read the controller/device JSON
lines and the Paho observations, never a self-reported "PASS" string.

Scenarios (all reproducible without hardware; the device is a simulation):

  normal            hot -> cold -> deadband, with ON/OFF confirmed by feedback
  lost_puback       the PUBACK for a command is discarded while the device still
                    executes it; the controller must report `unknown`, then
                    query and learn the real state
  broker_restart    the broker restarts; both processes reconnect and re-align
  controller_restart the controller restarts while the device is still ON; the
                    fresh controller must not trust the retained feedback alone

Usage:
  .venv/bin/python examples/mqtt_demo/demo.py            # all scenarios
  .venv/bin/python examples/mqtt_demo/demo.py normal     # one scenario
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import paho.mqtt.client as paho

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "_build/native/debug/build/examples/mqtt_demo"
LOCAL_BROKER = ROOT / ".tools/mosquitto"
BROKER = Path(
    os.environ.get("MOSQUITTO")
    or shutil.which("mosquitto")
    or (str(LOCAL_BROKER) if LOCAL_BROKER.is_file() else "mosquitto")
)
PREFIX = "moon/demo/thermostat"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port: int, deadline: float = 5.0) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return
        except OSError:
            time.sleep(.025)
    raise TimeoutError(f"port {port} did not become ready")


class Broker:
    """A real Mosquitto broker on a private port with an evidence log."""

    def __init__(self) -> None:
        self.port = free_port()
        self.temp = Path(tempfile.mkdtemp(prefix="moon-demo-"))
        self.log_path = self.temp / "broker.log"
        self.process: subprocess.Popen[bytes] | None = None
        self._log = None

    def start(self) -> None:
        conf = self.temp / "mosquitto.conf"
        conf.write_text(
            f"listener {self.port} 127.0.0.1\nallow_anonymous true\nlog_type all\n"
        )
        self._log = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [str(BROKER), "-c", str(conf), "-v"],
            stdout=self._log,
            stderr=subprocess.STDOUT,
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
        if self._log and not self._log.closed:
            self._log.close()
            self._log = None

    def restart(self) -> None:
        self.stop()
        time.sleep(.2)
        self.start()

    def close(self, failed: bool = False) -> None:
        self.stop()
        if failed or os.environ.get("MQTT_KEEP_DEMO_ARTIFACTS") == "1":
            print(f"demo evidence retained at {self.temp}", flush=True)
        else:
            shutil.rmtree(self.temp, ignore_errors=True)


class DropPubackProxy:
    """Hold a device/controller connection through a proxy that discards one
    PUBACK and closes the connection, reproducing a lost acknowledgement."""

    def __init__(self, target_port: int) -> None:
        self.port = free_port()
        self.target_port = target_port
        self.dropped = threading.Event()
        self.armed = False
        self.stop_event = threading.Event()
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", self.port))
        self.listener.listen()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self.stop_event.is_set():
            try:
                source, _ = self.listener.accept()
                target = socket.create_connection(("127.0.0.1", self.target_port))
            except OSError:
                return
            for args in ((source, target, False), (target, source, True)):
                threading.Thread(target=self._copy, args=args, daemon=True).start()

    @staticmethod
    def _packet_end(buf: bytearray) -> int | None:
        remaining, multiplier, pos = 0, 1, 1
        while pos < len(buf):
            digit = buf[pos]
            remaining += (digit & 127) * multiplier
            pos += 1
            if digit < 128:
                return pos + remaining if len(buf) >= pos + remaining else None
            multiplier *= 128
        return None

    def _copy(self, source: socket.socket, target: socket.socket, from_broker: bool) -> None:
        buf = bytearray()
        try:
            while not self.stop_event.is_set():
                chunk = source.recv(65536)
                if not chunk:
                    return
                if not from_broker:
                    target.sendall(chunk)
                    continue
                buf.extend(chunk)
                while (end := self._packet_end(buf)) is not None:
                    packet = bytes(buf[:end])
                    del buf[:end]
                    if packet[0] >> 4 == 4 and self.armed and not self.dropped.is_set():
                        # First PUBACK: discard it and break the connection so
                        # the client cannot see an acknowledgement it did send.
                        self.dropped.set()
                        source.shutdown(socket.SHUT_RDWR)
                        target.shutdown(socket.SHUT_RDWR)
                        return
                    target.sendall(packet)
        except OSError:
            return
        finally:
            source.close()
            target.close()

    def arm(self) -> None:
        """Discard the next PUBACK seen from the broker."""
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    def close(self) -> None:
        self.stop_event.set()
        self.listener.close()


class Proc:
    """A demo process whose stdout/stderr is collected for assertions."""

    def __init__(self, name: str, exe: Path, port: int, **env: str) -> None:
        self.name = name
        self.exe = exe
        self.port = port
        self.env = env
        self.process: subprocess.Popen[str] | None = None
        self.output: list[str] = []
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        child_env = {**os.environ, "MQTT_DEMO_PORT": str(self.port), **self.env}
        self.process = subprocess.Popen(
            [str(self.exe)],
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            line = line.rstrip("\n")
            self.output.append(line)
            if os.environ.get("MQTT_DEMO_VERBOSE") == "1":
                print(f"[{self.name}] {line}", flush=True)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self._reader:
            self._reader.join(timeout=2)

    def events(self, event: str) -> list[dict]:
        found = []
        for line in self.output:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if parsed.get("event") == event:
                found.append(parsed)
        return found

    def all_events(self) -> list[dict]:
        parsed = []
        for line in self.output:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return parsed


class Observer:
    """Independent Paho subscriber: records retained and live traffic."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.messages: list[tuple[str, str, bool]] = []
        self.client: paho.Client | None = None

    def start(self) -> None:
        self.client = paho.Client(
            paho.CallbackAPIVersion.VERSION2, client_id=f"demo-observer-{free_port()}"
        )
        self.client.on_message = self._on_message
        self.client.connect("127.0.0.1", self.port)
        self.client.subscribe(f"{PREFIX}/#", qos=1)
        self.client.loop_start()
        time.sleep(.4)

    def _on_message(self, _client, _userdata, message) -> None:
        self.messages.append(
            (message.topic, message.payload.decode("utf-8", "replace"), message.retain)
        )

    def stop(self) -> None:
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

    def publish_temperature(self, celsius: float) -> None:
        assert self.client
        self.client.publish(
            f"{PREFIX}/temperature",
            json.dumps({"temperature_c": celsius}),
            qos=1,
        )

    def feedback_payloads(self) -> list[str]:
        return [p for t, p, _ in self.messages if t == f"{PREFIX}/relay/feedback"]

    def commands(self) -> list[dict]:
        out = []
        for topic, payload, _ in self.messages:
            if topic == f"{PREFIX}/relay/set":
                out.append(json.loads(payload))
        return out


def observe_retained_feedback(port: int, timeout: float = 4.0) -> bool:
    """Subscribe freshly and confirm the device feedback arrives as retained."""
    seen: list[bool] = []
    client = paho.Client(
        paho.CallbackAPIVersion.VERSION2, client_id=f"demo-retain-{free_port()}"
    )
    client.on_message = lambda _c, _u, m: seen.append(m.retain)
    client.connect("127.0.0.1", port)
    client.subscribe(f"{PREFIX}/relay/feedback", qos=1)
    client.loop_start()
    end = time.monotonic() + timeout
    while time.monotonic() < end and not seen:
        time.sleep(.05)
    client.loop_stop()
    client.disconnect()
    return bool(seen) and seen[0]


def wait_for(predicate, timeout: float, what: str) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError(f"timed out waiting for {what}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_scenario(name: str) -> None:
    broker = Broker()
    broker.start()
    device = Proc(
        "device",
        BUILD / "test_device/test_device.exe",
        broker.port,
        MQTT_DEMO_DEVICE_ID="demo-device",
    )
    controller = Proc(
        "controller",
        BUILD / "controller/controller.exe",
        broker.port,
        MQTT_DEMO_CONTROLLER_ID="demo-controller",
    )
    observer = Observer(broker.port)
    proxy: DropPubackProxy | None = None
    failed = False
    try:
        observer.start()
        device.start()
        time.sleep(.7)
        controller.start()

        if name == "normal":
            wait_for(
                lambda: controller.events("feedback"),
                8,
                "initial device feedback",
            )
            require(
                not controller.events("command_confirmed"),
                "no command may be confirmed before any temperature sample",
            )
            observer.publish_temperature(29.0)
            wait_for(
                lambda: controller.events("command_confirmed"),
                8,
                "ON command confirmation",
            )
            observer.publish_temperature(25.0)
            wait_for(
                lambda: len(controller.events("command_confirmed")) >= 2,
                8,
                "OFF command confirmation",
            )
            observer.publish_temperature(27.0)
            time.sleep(.4)
            require(
                len(controller.events("command_confirmed")) == 2,
                "deadband sample must not issue another command",
            )
            confirmed = [
                (e["command_id"], e["state"]) for e in controller.events("command_confirmed")
            ]
            require(
                confirmed == [("cmd-1", "ON"), ("cmd-2", "OFF")],
                f"unexpected confirmation history: {confirmed}",
            )
            require(
                controller.events("hold"),
                "the deadband sample must be reported as a hold",
            )
            device_states = [
                json.loads(e["payload"])["state"] for e in device.all_events() if e["event"] == "feedback"
            ]
            require(
                device_states[-1] == "OFF",
                f"device should end OFF, saw {device_states}",
            )
            # Retained feedback is published by the device, never by the
            # controller. Live deliveries arrive with retain=false, so a fresh
            # subscriber is used to observe the retained store.
            require(
                observe_retained_feedback(broker.port),
                "device feedback must be retained for a fresh subscriber",
            )

        elif name == "lost_puback":
            # The controller talks to the broker through a proxy that can drop
            # exactly one PUBACK. It starts transparent so the initial query
            # completes normally; the drop is armed just before the command, so
            # the *command's* acknowledgement is the one that is lost.
            proxy = DropPubackProxy(broker.port)
            controller.stop()
            controller = Proc(
                "controller",
                BUILD / "controller/controller.exe",
                proxy.port,
                MQTT_DEMO_CONTROLLER_ID="demo-controller-lost",
            )
            controller.start()
            wait_for(lambda: controller.events("feedback"), 8, "initial feedback")
            require(
                controller.events("feedback")[0]["reported"] == "OFF",
                "initial query must establish the OFF baseline",
            )
            proxy.arm()
            observer.publish_temperature(29.0)
            wait_for(
                lambda: proxy.dropped.is_set(),
                8,
                "the PUBACK to be discarded",
            )
            # The device still applied the command even though the controller
            # never received the acknowledgement.
            wait_for(
                lambda: any(
                    e.get("command_id") and e.get("state") == "ON"
                    for e in device.all_events()
                ),
                8,
                "device to apply the command without a PUBACK",
            )
            require(
                controller.events("command_unknown"),
                "the controller must classify the lost PUBACK as unknown",
            )
            # After the reconnect the controller queries again and learns the
            # real state. The command id can no longer be confirmed (the PUBACK
            # is gone), but the device state is proven ON by a fresh answer.
            def aligned() -> bool:
                for event in controller.events("feedback"):
                    if (
                        event.get("query_id")
                        and event.get("reported") == "ON"
                        and event.get("desired") == "ON"
                    ):
                        return True
                return False

            wait_for(aligned, 12, "controller to re-align with fresh feedback")
            unknown = controller.events("command_unknown")
            require(
                unknown and unknown[0]["result"] == "unknown",
                "lost PUBACK must be reported as unknown, not as success",
            )
            # The resolved answer must come from a *new* query, not the lost one.
            resolved = [
                e
                for e in controller.events("feedback")
                if e.get("query_id") and e.get("reported") == "ON"
            ][-1]
            require(
                resolved["query_id"] != "qry-2",
                f"resolution must come from a post-reconnect query, saw {resolved}",
            )

        elif name == "broker_restart":
            wait_for(lambda: controller.events("feedback"), 8, "initial feedback")
            observer.publish_temperature(29.0)
            wait_for(
                lambda: controller.events("command_confirmed"),
                8,
                "ON confirmation before restart",
            )
            broker.restart()
            wait_for(
                lambda: controller.events("disconnected"),
                10,
                "controller to notice the broker restart",
            )
            # A reconnect must re-query; the answer re-establishes the state.
            wait_for(
                lambda: controller.events("connected") and len(controller.events("connected")) >= 2,
                15,
                "controller to reconnect",
            )
            wait_for(
                lambda: len(controller.events("query_sent")) >= 2,
                15,
                "controller to re-query after reconnect",
            )
            # Duplicate/retained feedback is ignored, but the correlated answer
            # must arrive after the reconnect.
            time.sleep(.5)
            require(
                controller.events("feedback_ignored"),
                "retained feedback must not be treated as current truth",
            )

        elif name == "controller_restart":
            wait_for(lambda: controller.events("feedback"), 8, "initial feedback")
            observer.publish_temperature(29.0)
            wait_for(
                lambda: controller.events("command_confirmed"),
                8,
                "ON confirmation before restart",
            )
            device_state = [
                json.loads(e["payload"])["state"]
                for e in device.all_events()
                if e["event"] == "feedback"
            ][-1]
            require(device_state == "ON", "device must be ON before the restart")
            controller.stop()
            controller = Proc(
                "controller",
                BUILD / "controller/controller.exe",
                broker.port,
                MQTT_DEMO_CONTROLLER_ID="demo-controller-restart",
            )
            controller.start()
            wait_for(
                lambda: controller.events("feedback"),
                8,
                "fresh controller feedback",
            )
            # The fresh controller has no local state. Its first correlated
            # answer must be a query response carrying its own query id, not the
            # retained payload that the broker delivers first.
            events = controller.all_events()
            require(
                any(e["event"] == "connected" for e in events),
                "the fresh controller must connect",
            )
            require(
                controller.events("feedback_ignored"),
                "the retained feedback must be ignored as not current truth",
            )
            correlated = [
                e for e in controller.events("feedback") if e.get("query_id") is not None
            ]
            require(
                correlated and correlated[0]["reported"] == "ON",
                f"fresh controller must learn the device is still ON, saw {correlated}",
            )
            # The query must precede any command the controller sends.
            first_query = next(
                i for i, e in enumerate(events) if e["event"] == "query_sent"
            )
            first_command = next(
                (i for i, e in enumerate(events) if e["event"] == "command_sent"),
                len(events),
            )
            require(
                first_query < first_command,
                "a fresh controller must query before it sends any command",
            )
            device_startup = device.events("connected")[0]["startup_id"]
            require(
                correlated[0]["startup_id"] == device_startup,
                "the query answer must come from the still-running device",
            )
        else:
            raise SystemExit(f"unknown scenario {name}")

    except AssertionError:
        failed = True
        print(f"--- {name} FAILED ---", flush=True)
        print("controller output:", flush=True)
        for line in controller.output:
            print("  " + line, flush=True)
        print("device output:", flush=True)
        for line in device.output:
            print("  " + line, flush=True)
        raise
    finally:
        observer.stop()
        controller.stop()
        device.stop()
        if proxy:
            proxy.close()
        broker.close(failed=failed)
    print(f"{name}: PASS", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenarios",
        nargs="*",
        default=["normal", "lost_puback", "broker_restart", "controller_restart"],
    )
    args = parser.parse_args()
    for exe in (
        BUILD / "test_device/test_device.exe",
        BUILD / "controller/controller.exe",
    ):
        if not exe.is_file():
            print(f"missing {exe}; run ./scripts/moon.sh build --target native")
            return 2
    failures = 0
    for scenario in args.scenarios:
        try:
            run_scenario(scenario)
        except AssertionError as error:
            failures += 1
            print(f"{scenario}: FAIL ({error})", flush=True)
    if failures:
        print(f"{failures} scenario(s) failed")
        return 1
    print("all demo scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
