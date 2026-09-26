#!/usr/bin/env python3
"""Correlate isolated client dial stages with Mosquitto source-port notices."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re


STAGE = re.compile(r"^DIAL_STAGE attempt=(\d+) stage=(\S+) at_ms=(\d+)(?: (.*))?$")
TCP = re.compile(r"^(\d+): New connection from (\S+) on port (\d+)\.")
CLIENT = re.compile(r"^(\d+): New client connected from (\S+) as (\S+) ")
CONNACK = re.compile(r"^(\d+): Sending CONNACK to (\S+) \((\d+), (\d+)\)")
START = re.compile(r"^(\d+): mosquitto version \S+ starting$")


def fields(text):
    return dict(re.findall(r"([a-z_]+)=([^ ]+)", text or ""))


def build(directory):
    directory = directory.resolve()
    result = json.loads((directory / "result.json").read_text())
    process = result["native_executable"]
    instance = f"{directory.name}|pid={process['pid']}|sha256={process['sha256']}"
    attempts = defaultdict(list)
    for line in (directory / "driver.stderr.log").read_text().splitlines():
        match = STAGE.match(line)
        if match:
            number, stage, at_ms, detail = match.groups()
            attempts[int(number)].append({"stage": stage, "at_ms": int(at_ms),
                                          "fields": fields(detail), "raw": line})
    broker_epoch = 0
    accepted = []
    by_endpoint = defaultdict(list)
    last_connected = defaultdict(list)
    for line in (directory / "broker.log").read_text(errors="replace").splitlines():
        if START.match(line):
            broker_epoch += 1
        elif match := TCP.match(line):
            second, endpoint, port = match.groups()
            item = {"broker_instance": broker_epoch, "broker_epoch_second": int(second),
                    "endpoint": endpoint, "listener_port": int(port),
                    "mqtt_identity": None, "broker_connack_code": None}
            accepted.append(item)
            by_endpoint[endpoint].append(item)
        elif match := CLIENT.match(line):
            _, endpoint, identity = match.groups()
            options = [item for item in by_endpoint[endpoint]
                       if item["broker_instance"] == broker_epoch]
            if options:
                options[-1]["mqtt_identity"] = identity
                last_connected[(broker_epoch, identity)].append(options[-1])
        elif match := CONNACK.match(line):
            _, identity, _, code = match.groups()
            options = last_connected[(broker_epoch, identity)]
            options = [item for item in options if item["broker_connack_code"] is None]
            if options:
                options[-1]["broker_connack_code"] = int(code)

    rows = []
    for number, stages in sorted(attempts.items()):
        started = [stage for stage in stages if stage["stage"] == "attempt_start"]
        assert len(started) == 1, (number, stages)
        tcp = next((stage for stage in stages if stage["stage"] == "tcp_connected"), None)
        local = tcp["fields"].get("local") if tcp else None
        port = int(tcp["fields"]["remote_port"]) if tcp else int(started[0]["fields"]["remote_port"])
        connected = [] if local is None else [item for item in by_endpoint[local]
                                             if item["listener_port"] == port and
                                             abs(item["broker_epoch_second"] * 1000 - tcp["at_ms"]) < 2000]
        if len(connected) > 1:
            raise AssertionError(f"ambiguous socket-to-broker mapping: attempt {number} {connected}")
        peer = connected[0] if connected else None
        connack = next((stage for stage in stages if stage["stage"] in ("connack_311", "connack_5")), None)
        if peer and connack and peer["broker_connack_code"] is not None:
            assert peer["broker_connack_code"] == int(connack["fields"]["code"]), (number, peer, connack)
        rows.append({"process_instance": instance, "attempt_id": number,
                     "socket_tuple": f"{local}->{port}" if local else None,
                     "broker_instance": peer["broker_instance"] if peer else None,
                     "broker_accept": peer,
                     "client_connack_code": int(connack["fields"]["code"]) if connack else None,
                     "stage_names": [stage["stage"] for stage in stages],
                     "stage_rows": stages})
    emitted_rows = [stage for row in rows for stage in row["stage_rows"]
                    if stage["stage"] == "disconnected_emitted"]
    consumed_rows = [event for event in json.loads((directory / "client-events.json").read_text())
                     if event["event"] == "disconnected"]
    emitted, consumed = len(emitted_rows), len(consumed_rows)
    assert emitted == consumed == result["disconnect_classification"]["consumer_events"], (
        emitted, consumed, result["disconnect_classification"]["consumer_events"])
    for marker, event in zip(emitted_rows, consumed_rows):
        assert int(marker["fields"]["generation"]) == event["generation"], (marker, event)
        assert 0 <= event["at_ms"] - marker["at_ms"] <= 1000, (marker, event)
    for row in rows:
        if "dial_ready" in row["stage_names"]:
            peer = row["broker_accept"]
            assert peer is not None and peer["broker_instance"] > 0, (
                "ready dial lacks broker source-port mapping", row["attempt_id"])
            assert peer["mqtt_identity"] == "moon-soak-driver", (
                "ready dial lacks independent MQTT identity", row["attempt_id"])
            assert peer["broker_connack_code"] == row["client_connack_code"] == 0, (
                "ready dial lacks agreeing numeric CONNACK", row["attempt_id"])
    output = {"source_commit": "c52ba9b9f7e8a8aee5ccb2e8b186feeecb6faee6",
              "diagnostic_build": True, "artifact": str(directory),
              "driver_binary_sha256": process["sha256"],
              "stage_log_sha256": hashlib.sha256((directory / "driver.stderr.log").read_bytes()).hexdigest(),
              "broker_log_sha256": hashlib.sha256((directory / "broker.log").read_bytes()).hexdigest(),
              "broker_instances": broker_epoch, "accepted_sockets": len(accepted),
              "attempts": len(rows), "disconnected_emitted": emitted,
              "disconnected_consumed": consumed, "matched_event_order": True,
              "rows": rows}
    (directory / "dial-stage-ledger.json").write_text(json.dumps(output, indent=2) + "\n")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    data = build(parser.parse_args().artifact)
    print({key: data[key] for key in ("broker_instances", "accepted_sockets", "attempts",
                                      "disconnected_emitted", "disconnected_consumed")})
