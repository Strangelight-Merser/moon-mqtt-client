#!/usr/bin/env python3
"""Bounded loopback calibration for the isolated c52 dial-stage diagnostic build."""

import hashlib
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
import time


SOURCE = Path(__file__).resolve().parents[3]
BASE = SOURCE / "_build" / "dial-stage-hosted"
CLI = SOURCE / "_build/native/debug/build/Strangelight-Merser/moon-mqtt-client/examples/mqtt_demo/cli/cli.exe"
PKI = BASE / "mtls-short"


def read_packet(conn):
    head = conn.recv(1)
    if not head:
        return b""
    multiplier = 1
    size = 0
    length = bytearray()
    while True:
        byte = conn.recv(1)
        if not byte:
            raise EOFError("remaining length incomplete")
        length.extend(byte)
        size += (byte[0] & 127) * multiplier
        if byte[0] < 128:
            break
        multiplier *= 128
    payload = bytearray()
    while len(payload) < size:
        chunk = conn.recv(size - len(payload))
        if not chunk:
            raise EOFError("packet body incomplete")
        payload.extend(chunk)
    return head + length + payload


def trial(mode):
    row = {"mode": mode}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        if mode != "tcp_refused":
            listener.listen(1)
            listener.settimeout(8)

        def serve():
            try:
                raw, endpoint = listener.accept()
                row["peer_endpoint"] = f"{endpoint[0]}:{endpoint[1]}"
                with raw:
                    raw.settimeout(7)
                    if mode == "tls_bad_trust":
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        context.load_cert_chain(PKI / "server.pem", PKI / "server.key")
                        context.load_verify_locations(PKI / "client-ca.pem")
                        context.verify_mode = ssl.CERT_REQUIRED
                        try:
                            with context.wrap_socket(raw, server_side=True):
                                pass
                        except ssl.SSLError as error:
                            row["peer_tls_error"] = type(error).__name__
                    elif mode == "tls_timeout":
                        time.sleep(6)
                    else:
                        row["connect_hex"] = read_packet(raw).hex()
                        if mode == "connack_03":
                            raw.sendall(b"\x20\x02\x00\x03")
                            row["peer_connack_hex"] = "20020003"
                        elif mode != "pre_connack_eof":
                            raise AssertionError(mode)
            except Exception as error:
                row["peer_error"] = repr(error)

        thread = None
        if mode != "tcp_refused":
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
        args = [str(CLI), "subscribe", "--host", "127.0.0.1", "--port", str(port),
                "--topic", "dial/calibration", "--qos", "1", "--count", "1",
                "--timeout-ms", "1000"]
        if mode.startswith("tls_"):
            args += ["--tls", "--ca", str(PKI / ("unrelated-ca.pem" if mode == "tls_bad_trust" else "ca.pem")),
                     "--cert", str(PKI / "client.pem"), "--key", str(PKI / "client.key")]
        result = subprocess.run(args, cwd=SOURCE, capture_output=True, text=True, timeout=12)
        if thread:
            thread.join(8)
        row.update(port=port, returncode=result.returncode, stdout=result.stdout,
                   stderr=result.stderr, peer_finished=(thread is None or not thread.is_alive()))
    return row


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    assert CLI.is_file() and (PKI / "server.pem").is_file()
    rows = []
    report = {"source_commit": "c52ba9b9f7e8a8aee5ccb2e8b186feeecb6faee6",
              "diagnostic_source_sha256": hashlib.sha256((SOURCE / "runtime.mbt").read_bytes()).hexdigest(),
              "cli_sha256": hashlib.sha256(CLI.read_bytes()).hexdigest(), "rows": rows}
    for mode in ("connack_03", "pre_connack_eof", "tcp_refused", "tls_bad_trust", "tls_timeout"):
        row = trial(mode)
        rows.append(row)
        (BASE / "calibration.json").write_text(json.dumps(report, indent=2) + "\n")
        assert row["returncode"] != 0 and row["peer_finished"], row
        stages = re.findall(r"stage=([a-z0-9_]+)", row["stderr"])
        assert "attempt_start" in stages and "supervisor_error" in stages, row
        if mode == "connack_03":
            assert row["peer_connack_hex"] == "20020003" and "code=3" in row["stderr"], row
        elif mode == "pre_connack_eof":
            assert "mqtt_connect_error" in stages and "connack_311" not in stages, row
        elif mode == "tcp_refused":
            assert "tcp_connected" not in stages, row
        elif mode == "tls_bad_trust":
            assert "tcp_connected" in stages and "tls_handshake_ok" not in stages, row
        elif mode == "tls_timeout":
            assert "tcp_connected" in stages and "tls_handshake_ok" not in stages, row
        print(mode, stages, flush=True)


if __name__ == "__main__":
    main()
