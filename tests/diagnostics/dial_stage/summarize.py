#!/usr/bin/env python3
"""Verify fixed-budget diagnostic soaks without promoting them to Release evidence."""

from collections import Counter
import hashlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parents[3] / "_build" / "dial-stage-hosted"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def one(name):
    directory = BASE / name
    result = json.loads((directory / "result.json").read_text())
    ledger = json.loads((directory / "dial-stage-ledger.json").read_text())
    assert result["gate"] == {"passed": True, "failures": []}, name
    assert result["parameters"]["duration_seconds"] == 1800, name
    assert result["parameters"]["disconnect_cycles"] == 100, name
    assert result["recovery_seconds"]["count"] == 100, name
    assert result["driver"]["reconnects"] == 100, name
    assert result["driver"]["connected_events"] == 101, name
    assert result["driver"]["collector_complete"] is True, name
    assert ledger["disconnected_emitted"] == ledger["disconnected_consumed"] == result["driver"]["disconnects"], name
    events = json.loads((directory / "client-events.json").read_text())
    assert sum(event["event"] == "disconnected" for event in events) == ledger["disconnected_consumed"], name
    assert [row["attempt_id"] for row in ledger["rows"]] == list(range(1, ledger["attempts"] + 1)), name
    assert ledger["broker_instances"] == 101, name
    assert not any("[soak.py] broker log truncated" in line for line in
                   (directory / "broker.log").read_text(errors="replace").splitlines()), name
    stage_patterns = Counter("|".join(s for s in row["stage_names"]
                                      if s in ("tcp_connected", "tls_handshake_ok",
                                               "mqtt_connect_error", "connack_311", "connack_5",
                                               "dial_ready", "supervisor_error",
                                               "disconnected_emitted"))
                             for row in ledger["rows"])
    ready = [row for row in ledger["rows"] if "dial_ready" in row["stage_names"]]
    failed = [row for row in ledger["rows"] if "dial_ready" not in row["stage_names"]]
    return {
        "platform": result["environment"]["platform"],
        "binary_sha256": result["native_executable"]["sha256"],
        "elapsed_seconds": result["elapsed_seconds"],
        "attempts": ledger["attempts"], "broker_instances": ledger["broker_instances"],
        "ready_dials": len(ready), "failed_dials": len(failed),
        "failed_dial_attempt_ids": [row["attempt_id"] for row in failed],
        "disconnected": ledger["disconnected_consumed"],
        "stage_patterns": dict(stage_patterns),
        "checksums": {n: sha(directory / n) for n in (
            "result.json", "dial-stage-ledger.json", "driver.stderr.log",
            "driver.jsonl", "broker.log", "client-events.json", "fault-plan.json")},
    }


if __name__ == "__main__":
    protocol = json.loads((BASE / "protocol.json").read_text())
    names = [run["name"] for run in protocol["runs"]]
    report = {"source_commit": protocol["frozen_source_commit"],
              "diagnostic_only": True,
              "patch_sha256": protocol["patch_sha256"],
              "parser_sha256": protocol["parser_sha256"],
              "runs": {name: one(name) for name in names},
              "limits": protocol["limits"]}
    (BASE / "result-summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print({name: {k: value[k] for k in ("attempts", "ready_dials", "failed_dials", "disconnected")}
           for name, value in report["runs"].items()})
