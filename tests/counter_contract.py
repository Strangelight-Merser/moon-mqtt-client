#!/usr/bin/env python3
"""Public-API counter semantics against numeric CONNACK and controlled peers."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from harness import NativeProcess
from protocol_faults import recv_packet

ROOT = Path(__file__).resolve().parents[1]


def listener():
    peer = socket.socket()
    peer.bind(("127.0.0.1", 0))
    peer.listen()
    peer.settimeout(5)
    return peer


def connect(peer, code=0, mqtt5=False):
    connection, _ = peer.accept()
    connection.settimeout(5)
    assert recv_packet(connection)[0] == 0x10
    if mqtt5:
        connection.sendall(b"\x20\x03\x00" + bytes([code]) + b"\x00")
    else:
        connection.sendall(b"\x20\x02\x00" + bytes([code]))
    return connection


def run_rejections(binary, count, directory):
    with listener() as peer:
        driver = NativeProcess([binary], env=os.environ | {
            "COUNTER_PORT": str(peer.getsockname()[1]), "COUNTER_PROTOCOL": "311"})
        try:
            with connect(peer) as first:
                driver.expect("connected 1")
                first.shutdown(socket.SHUT_RDWR)
            driver.expect("disconnected 1 1")
            for number in range(count):
                with connect(peer, 3):
                    driver.expect(f"disconnected 1 {number + 2}")
            with connect(peer) as last:
                driver.expect("connected 2")
                driver.expect(f"stats 2 1 {count + 1}")
                assert recv_packet(last) == b"\xe0\x00"
            result = driver.finish()
            assert result.returncode == 0, result.stdout
            (directory / f"rejections-{count}.log").write_text(result.stdout)
            return {"case": f"established-close-plus-{count}-rc3", "codes": [0, *([3] * count), 0],
                    "generation": 2, "reconnects": 1, "disconnects": count + 1}
        finally:
            driver.close()


def run_initial_refusal(binary, directory):
    with listener() as peer:
        driver = NativeProcess([binary], env=os.environ | {
            "COUNTER_PORT": str(peer.getsockname()[1]), "COUNTER_PROTOCOL": "311"})
        try:
            with connect(peer, 3):
                # MQTT 3.1.1's public initial failure is ConnectionRefused;
                # the numeric rc is known from this independent raw peer.
                driver.expect("initial ConnectionRefused")
            result = driver.finish()
            assert result.returncode == 0
            (directory / "initial-refusal.log").write_text(result.stdout)
            return {"case": "initial-refusal", "numeric_connack": 3,
                    "client_action_entered": False}
        finally:
            driver.close()


def run_maintenance_stop(binary, directory):
    with listener() as peer:
        driver = NativeProcess([binary], env=os.environ | {
            "COUNTER_PORT": str(peer.getsockname()[1]), "COUNTER_PROTOCOL": "5"})
        try:
            with connect(peer, mqtt5=True) as connection:
                driver.expect("connected 1")
                connection.sendall(b"\xe0\x02\x89\x00")
                driver.expect("terminal ServerDisconnected 137")
                driver.expect("stats 1 0 0")
            result = driver.finish()
            assert result.returncode == 0
            (directory / "maintenance-stop.log").write_text(result.stdout)
            return {"case": "maintenance-termination-default", "disconnect_reason": 137,
                    "generation": 1, "reconnects": 0, "disconnects": 0}
        finally:
            driver.close()


def run_restore_rejection(binary, directory):
    with listener() as peer:
        driver = NativeProcess([binary], env=os.environ | {
            "COUNTER_PORT": str(peer.getsockname()[1]), "COUNTER_PROTOCOL": "311",
            "COUNTER_SUBSCRIBE": "1"})
        try:
            with connect(peer) as first:
                driver.expect("connected 1")
                subscribe = recv_packet(first)
                assert subscribe[0] >> 4 == 8
                packet_id = subscribe[2:4]
                first.sendall(b"\x90\x03" + packet_id + b"\x00")
                driver.expect("subscribed")
                first.shutdown(socket.SHUT_RDWR)
            driver.expect("disconnected 1 1")
            with connect(peer) as second:
                subscribe = recv_packet(second)
                assert subscribe[0] >> 4 == 8
                second.sendall(b"\x90\x03" + subscribe[2:4] + b"\x80")
                result = driver.finish(timeout=6)
            # The worker's terminal ProtocolError cancels the client scope;
            # it is not delivered as a second ready/Connected event.
            assert result.returncode != 0 and "ClientError.ProtocolError" in result.stdout, (
                result.returncode, result.stdout)
            assert "connected 2" not in result.stdout
            (directory / "restore-suback-rejected.log").write_text(result.stdout)
            return {"case": "recovery-suback-failure", "suback_reasons": [0, 128],
                    "ready_reconnects": 0, "observed_disconnected_before_restore": 1,
                    "terminal_error": "ProtocolError"}
        finally:
            driver.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    out = (args.artifacts or ROOT / "_build" / "counter-contract" / str(time.time_ns())).resolve()
    out.mkdir(parents=True, exist_ok=False)
    subprocess.run([os.environ.get("MOON", str(ROOT / "scripts/moon.sh")), "build", "--target", "native"],
                   cwd=ROOT, check=True)
    binary = next((ROOT / "_build/native/debug/build").glob(
        "**/counter_driver/counter_driver.exe"))
    report = {"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "display_strings_parsed": False, "cases": []}
    for count in (0, 1, 2):
        report["cases"].append(run_rejections(binary, count, out))
    report["cases"].append(run_initial_refusal(binary, out))
    report["cases"].append(run_maintenance_stop(binary, out))
    report["cases"].append(run_restore_rejection(binary, out))
    (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Counter contract cases passed:", out)


if __name__ == "__main__":
    main()
