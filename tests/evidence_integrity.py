#!/usr/bin/env python3
"""Final Release inventory: refuse stale sources or incomplete required evidence."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def inventory_files(directory):
    # Root result/log files are updated after this process exits. Nested
    # per-trial result.json files are already final and must be sealed.
    excluded_root = {"result.json", "evidence-integrity.log", "inventory.json"}
    excluded_suffixes = {".key", ".pem", ".mqttrec", ".dmg", ".sqlite3"}
    return {
        str(relative): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
        for relative in (path.relative_to(directory),)
        if str(relative) not in excluded_root
        and path.suffix not in excluded_suffixes
        and ".sqlite3-" not in path.name
    }


def write_inventory(directory, state, complete):
    inventory = {
        "source_commit": state.get("source_commit"),
        "candidate": state.get("candidate"),
        "source_unchanged": state.get("source_unchanged", False),
        "required_measurements_complete": complete,
        "files": inventory_files(directory),
    }
    (directory / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")


def validate(directory, state):
    start = read(directory / "source.json")
    state["source_commit"] = start["commit"]
    spec = importlib.util.spec_from_file_location(
        "audit_acceptance", ROOT / "scripts/acceptance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    end = module.source_identity()
    state["source_unchanged"] = (
        start["commit"] == end["commit"] and start["files"] == end["files"]
    )
    assert state["source_unchanged"], (
        "source changed during acceptance"
    )
    candidate = read(directory / "candidate.json")
    state["candidate"] = candidate["candidate"]
    assert (
        candidate["source"]["files"] == start["files"]
        and candidate["source"]["commit"] == start["commit"]
    )
    assert candidate["operator_version"] == (ROOT / "scripts/operator-version.txt").read_text().strip()
    assert candidate["operator_candidate"].startswith(candidate["operator_version"] + "-audit-")
    for name in ("library-consumer.json", "operator-consumer.json"):
        result = read(directory / name)
        assert result["successful"], (name, result)
    operator_consumer = read(directory / "operator-consumer.json")
    assert operator_consumer["operator_version"] == candidate["operator_version"]
    assert operator_consumer["provenance"]["operator_candidate"] == candidate["operator_candidate"]
    benchmarks = list(directory.glob("benchmark-*/summary.json"))
    assert len(benchmarks) == 1
    benchmark = read(benchmarks[0])
    assert benchmark["full_default_matrix"] and len(benchmark["results"]) == 39
    assert benchmark["trace_ab_requested"]
    assert len(benchmark["trace_ab"]["pairs"]) == 3
    assert benchmark["trace_ab"]["comparison"]["verdict"] == "within_trigger", (
        "trace A/B requires investigation or is indeterminate", benchmark["trace_ab"]["comparison"])
    for trial in benchmark["results"]:
        assert trial["native"]["pid"] > 0 and len(trial["native"]["sha256"]) == 64
        assert trial["resources"]["live"] and trial["resources"]["post_scope"]
    validity = list(directory.glob("measurement-validity-*/summary.json"))
    assert len(validity) == 1, "E06 needs one native held-ACK measurement run"
    held = read(validity[0])
    assert [row["delay_ms"] for row in held] == [100, 160, 500]
    assert all(row["completed"] > 0 and row["overflow"] == row["completed"]
               and row["max_ns"] >= row["delay_ms"] * 1_000_000 for row in held)
    recovery_io = list(directory.glob("recovery-io-*/summary.json"))
    assert len(recovery_io) == 1
    assert {row["window"] for row in read(recovery_io[0])["results"]} == {
        "prepared_journal", "source_retirement", "target_directory_publish"}
    for transport in ("tcp", "mtls"):
        matches = list(directory.glob(f"soak-{transport}-*/summary.json"))
        assert len(matches) == 1
        soak = read(matches[0])
        assert soak["gate"]["passed"]
        assert (
            soak["parameters"]["duration_seconds"] == 1800
            and soak["recovery_seconds"]["count"] == 100
        )
        assert (
            soak["resources"]["resource_evidence_available"]
            and len(soak["resources"]["post_scope_rss_fds_threads"]) == 3
        )
    storage = read(directory / "storage-faults.json")
    assert not storage["power_loss_tested"] and storage["full_gate"] and storage["passed"]
    boundaries = list(directory.glob("durable-boundaries-*/summary.json"))
    assert len(boundaries) == 1
    assert len(read(boundaries[0])["results"]) == 6
    assert (directory / "b2-consumer.json").is_file()
    attempts = read(directory / "result.json")["attempts"]
    assert all(
        row["status"] in ("passed", "platform-not-applicable") for row in attempts
    ), attempts


def main():
    directory = Path(os.environ["MQTT_EVIDENCE_DIR"])
    state = {}
    complete = False
    try:
        validate(directory, state)
        complete = True
    finally:
        write_inventory(directory, state, complete)
    print(
        "Final source, candidate consumers, 39 benchmarks, 2 long soaks, six crash windows and storage evidence verified"
    )


if __name__ == "__main__":
    main()
