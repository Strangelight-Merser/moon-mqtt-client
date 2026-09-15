#!/usr/bin/env python3
"""Loopback MQTT 3.1.1 protocol fault injector; it is not a broker."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def recv_exact(conn: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise EOFError("peer closed")
        data.extend(chunk)
    return bytes(data)


def recv_packet(conn: socket.socket) -> bytes:
    first = recv_exact(conn, 1)
    length = 0
    multiplier = 1
    encoded = bytearray()
    while True:
        digit = recv_exact(conn, 1)[0]
        encoded.append(digit)
        length += (digit & 0x7F) * multiplier
        if not digit & 0x80:
            break
        multiplier *= 128
    return first + bytes(encoded) + recv_exact(conn, length)


def body_offset(packet: bytes) -> int:
    pos = 1
    while packet[pos] & 0x80:
        pos += 1
    return pos + 1


def publish_id(packet: bytes) -> int:
    pos = body_offset(packet)
    topic_len = int.from_bytes(packet[pos : pos + 2], "big")
    pos += 2 + topic_len
    return int.from_bytes(packet[pos : pos + 2], "big")


def leading_packet_id(packet: bytes) -> int:
    pos = body_offset(packet)
    return int.from_bytes(packet[pos : pos + 2], "big")


def accept_client(listener: socket.socket) -> socket.socket:
    conn, _ = listener.accept()
    conn.settimeout(3)
    assert recv_packet(conn)[0] >> 4 == 1
    conn.sendall(b"\x20\x02\x00\x00")
    return conn


def inject_wrong_puback(listener: socket.socket, evidence: dict) -> None:
    conn = accept_client(listener)
    packet = recv_packet(conn)
    actual = publish_id(packet)
    wrong = 1 if actual == 65535 else actual + 1
    conn.sendall(b"\x40\x02" + wrong.to_bytes(2, "big"))
    evidence["wrong_for"] = actual
    try:
        evidence["closed"] = conn.recv(1) == b""
    except ConnectionResetError:
        evidence["closed"] = True
    conn.close()


def inject_timeout_reconnect(listener: socket.socket, evidence: dict) -> None:
    first = accept_client(listener)
    packet1 = recv_packet(first)
    evidence["first_id"] = publish_id(packet1)
    time.sleep(0.35)
    try:
        assert first.recv(1) == b"", "ACK timeout did not close the old connection"
    except ConnectionResetError:
        pass
    evidence["closed_before_late_ack"] = True
    try:
        first.sendall(b"\x40\x02" + evidence["first_id"].to_bytes(2, "big"))
    except OSError:
        pass
    first.close()
    second = accept_client(listener)
    packet2 = recv_packet(second)
    evidence["second_id"] = publish_id(packet2)
    second.sendall(b"\x40\x02" + evidence["second_id"].to_bytes(2, "big"))
    assert recv_packet(second) == b"\xe0\x00"
    evidence["disconnect"] = True
    second.close()


def inject_full_inflight(listener: socket.socket, evidence: dict) -> None:
    conn = accept_client(listener)
    pending = recv_packet(conn)
    evidence["pending_id"] = publish_id(pending)
    evidence["disconnect_packet"] = recv_packet(conn)
    conn.close()


def inject_partial_suback(listener: socket.socket, evidence: dict) -> None:
    conn = accept_client(listener)
    subscribe = recv_packet(conn)
    assert subscribe[0] == 0x82
    packet_id = leading_packet_id(subscribe)
    conn.sendall(b"\x90\x04" + packet_id.to_bytes(2, "big") + b"\x00\x80")
    evidence["results"] = [0, 0x80]
    evidence["disconnect_packet"] = recv_packet(conn)
    conn.close()


def inject_slow_suback_header(listener: socket.socket, evidence: dict) -> None:
    conn = accept_client(listener)
    subscribe = recv_packet(conn)
    packet_id = leading_packet_id(subscribe)
    conn.sendall(b"\x90")
    time.sleep(0.15)
    conn.sendall(b"\x03" + packet_id.to_bytes(2, "big") + b"\x00")
    evidence["delay_ms"] = 150
    evidence["disconnect_packet"] = recv_packet(conn)
    conn.close()


def inject_slow_suback_body(listener: socket.socket, evidence: dict) -> None:
    conn = accept_client(listener)
    subscribe = recv_packet(conn)
    packet_id = leading_packet_id(subscribe)
    conn.sendall(b"\x90\x03" + packet_id.to_bytes(2, "big")[:1])
    time.sleep(0.11)
    conn.sendall(packet_id.to_bytes(2, "big")[1:])
    time.sleep(0.11)
    conn.sendall(b"\x00")
    evidence["delays_ms"] = [110, 110]
    evidence["disconnect_packet"] = recv_packet(conn)
    conn.close()


def inject_idle_then_suback(listener: socket.socket, evidence: dict) -> None:
    conn = accept_client(listener)
    time.sleep(0.35)
    subscribe = recv_packet(conn)
    packet_id = leading_packet_id(subscribe)
    conn.sendall(b"\x90\x03" + packet_id.to_bytes(2, "big") + b"\x00")
    evidence["idle_ms"] = 350
    evidence["disconnect_packet"] = recv_packet(conn)
    conn.close()


def inject_cancel_reconnect(listener: socket.socket, evidence: dict) -> None:
    first = accept_client(listener)
    first_packet = recv_packet(first)
    evidence["first_id"] = publish_id(first_packet)
    try:
        evidence["closed"] = first.recv(1) == b""
    except ConnectionResetError:
        evidence["closed"] = True
    first.close()
    second = accept_client(listener)
    second_packet = recv_packet(second)
    evidence["second_id"] = publish_id(second_packet)
    second.sendall(b"\x40\x02" + evidence["second_id"].to_bytes(2, "big"))
    assert recv_packet(second) == b"\xe0\x00"
    evidence["disconnect"] = True
    second.close()


def qos0_publish(topic: str, payload: bytes) -> bytes:
    topic_bytes = topic.encode()
    body = len(topic_bytes).to_bytes(2, "big") + topic_bytes + payload
    assert len(body) < 128
    return b"\x30" + bytes([len(body)]) + body


def inject_slow_consumer_overflow(listener: socket.socket, evidence: dict) -> None:
    conn = accept_client(listener)
    for index in range(3):
        conn.sendall(qos0_publish(f"fault/flood/{index}", bytes([index])))
    try:
        evidence["closed"] = conn.recv(1) == b""
    except ConnectionResetError:
        evidence["closed"] = True
    evidence["sent"] = 3
    conn.close()


CASES = {
    "wrong_puback": inject_wrong_puback,
    "timeout_reconnect": inject_timeout_reconnect,
    "full_inflight_disconnect": inject_full_inflight,
    "partial_suback": inject_partial_suback,
    "slow_suback_header": inject_slow_suback_header,
    "slow_suback_body": inject_slow_suback_body,
    "idle_then_suback": inject_idle_then_suback,
    "cancel_reconnect": inject_cancel_reconnect,
    "slow_consumer_overflow": inject_slow_consumer_overflow,
}


def run_case(name: str) -> None:
    evidence: dict = {}
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(5)
        errors = []
        def inject():
            try:
                CASES[name](listener, evidence)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=inject)
        thread.start()
        env = os.environ | {
            "MQTT_FAULT_CASE": name,
            "MQTT_FAULT_PORT": str(listener.getsockname()[1]),
        }
        result = subprocess.run(
            [os.environ.get("MOON", str(ROOT / "scripts/moon.sh")), "run", "examples/fault_driver", "--target", "native"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
        )
        thread.join(5)
        if thread.is_alive():
            raise AssertionError(f"{name}: injector did not finish")
    assert not errors, (name, errors, evidence)
    assert result.returncode == 0, (name, result.stdout, result.stderr, evidence)
    assert "FAULT_CASE_PASS" in result.stdout, (name, result.stdout, result.stderr)
    if name == "wrong_puback":
        assert evidence.get("closed")
    elif name == "timeout_reconnect":
        assert evidence.get("closed_before_late_ack")
        assert evidence.get("disconnect")
    elif name == "full_inflight_disconnect":
        assert evidence.get("disconnect_packet") == b"\xe0\x00", evidence
    elif name == "partial_suback":
        assert evidence.get("results") == [0, 0x80]
        assert evidence.get("disconnect_packet") == b"\xe0\x00", evidence
    elif name in {"slow_suback_header", "slow_suback_body", "idle_then_suback"}:
        assert evidence.get("disconnect_packet") == b"\xe0\x00", evidence
    elif name == "cancel_reconnect":
        assert evidence.get("closed"), evidence
        assert evidence.get("disconnect"), evidence
    elif name == "slow_consumer_overflow":
        assert evidence.get("sent") == 3
        assert evidence.get("closed"), evidence
    print(f"PASS {name}: {evidence}")


if __name__ == "__main__":
    for case in CASES:
        run_case(case)
