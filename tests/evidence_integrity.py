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


def main():
    directory = Path(os.environ["MQTT_EVIDENCE_DIR"])
    start = read(directory / "source.json")
    spec = importlib.util.spec_from_file_location(
        "audit_acceptance", ROOT / "scripts/acceptance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    end = module.source_identity()
    assert start["commit"] == end["commit"] and start["files"] == end["files"], (
        "source changed during acceptance"
    )
    candidate = read(directory / "candidate.json")
    assert (
        candidate["source"]["files"] == start["files"]
        and candidate["source"]["commit"] == start["commit"]
    )
    for name in ("library-consumer.json", "operator-consumer.json"):
        result = read(directory / name)
        assert result["successful"], (name, result)
    benchmarks = list(directory.glob("benchmark-*/summary.json"))
    assert len(benchmarks) == 1
    benchmark = read(benchmarks[0])
    assert benchmark["full_default_matrix"] and len(benchmark["results"]) == 39
    for trial in benchmark["results"]:
        assert trial["native"]["pid"] > 0 and len(trial["native"]["sha256"]) == 64
        assert trial["resources"]["live"] and trial["resources"]["post_scope"]
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
    # Exclude secrets/test PKI, raw stores and recovery archives from the share
    # inventory. The local evidence directory may still contain synthetic data.
    excluded = {".key", ".pem", ".mqttrec", ".dmg", ".sqlite3"}
    inventory = {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
        and p.suffix not in excluded
        and ".sqlite3-" not in p.name
        and p.name not in ("result.json", "evidence-integrity.log", "inventory.json")
    }
    (directory / "inventory.json").write_text(
        json.dumps(
            {
                "source_commit": start["commit"],
                "candidate": candidate["candidate"],
                "source_unchanged": True,
                "required_measurements_complete": True,
                "files": inventory,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        "Final source, candidate consumers, 39 benchmarks, 2 long soaks, six crash windows and storage evidence verified"
    )


if __name__ == "__main__":
    main()
