#!/usr/bin/env python3
"""Bind a private B2 run and conservatively classify independent observations."""

import argparse
import hashlib
import json
from pathlib import Path

CASES = [
    "baseline",
    "same-id-duplicate",
    "conflicting-id",
    "expired",
    "retained-replay",
    "device-offline",
    "broker-stop",
    "broker-kill",
    "controller-restart",
    "ha-restart",
    "session-expiry",
    "full-puback-delay",
    "before-admission-commit",
    "after-admission-commit",
    "after-possible-write-commit",
    "after-publish",
    "after-puback-before-delete",
    "after-delete-commit",
]


def file_identity(path):
    return {
        "file": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    new = sub.add_parser("prepare")
    new.add_argument("--candidate", type=Path, required=True)
    new.add_argument("--firmware", type=Path, required=True)
    new.add_argument("--consumer", type=Path, required=True)
    new.add_argument("--output", type=Path, required=True)
    finish = sub.add_parser("classify")
    finish.add_argument("run", type=Path)
    args = p.parse_args()
    if args.command == "prepare":
        args.output.mkdir(parents=True, exist_ok=False)
        manifest = {
            "candidate": file_identity(args.candidate),
            "firmware": file_identity(args.firmware),
            "consumer": file_identity(args.consumer),
            "board": None,
            "wiring": None,
            "network": None,
            "observer": None,
            "calibration": {
                "two_distinct_applies_resolved": False,
                "raw_capture": None,
            },
            "cases": {case: "waiting_for_field_conditions" for case in CASES},
        }
        (args.output / "run.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (args.output / "commands.jsonl").touch()
        (args.output / "physical.jsonl").touch()
        print(args.output / "run.json")
        return
    root = args.run
    manifest = json.loads((root / "run.json").read_text())
    for key in ("candidate", "firmware", "consumer"):
        item = manifest[key]
        assert file_identity(Path(item["file"]))["sha256"] == item["sha256"], key
    physical = {}
    for line in (root / "physical.jsonl").read_text().splitlines():
        row = json.loads(line)
        assert row["id"] not in physical, "ambiguous duplicate observation record"
        physical[row["id"]] = row
    commands = [
        json.loads(line) for line in (root / "commands.jsonl").read_text().splitlines()
    ]
    calibration = manifest["calibration"]
    calibrated = calibration["two_distinct_applies_resolved"] is True and bool(
        calibration["raw_capture"]
    )
    if calibrated:
        item = calibration["raw_capture"]
        calibrated = file_identity(Path(item["file"]))["sha256"] == item["sha256"]
    result = []
    for command in commands:
        row = physical.get(command["id"])
        outcome = "evidence_insufficient"
        if (
            calibrated
            and row
            and row.get("independent_observer")
            and row.get("coverage_complete")
        ):
            raw = row["raw_capture"]
            assert file_identity(Path(raw["file"]))["sha256"] == raw["sha256"]
            count = row["apply_count"]
            assert type(count) is int and count >= 0
            outcome = "zero" if count == 0 else "once" if count == 1 else "multiple"
        result.append(
            {"id": command["id"], "case": command["case"], "execution": outcome}
        )
    output = {
        "commands": result,
        "unrun_cases": [c for c in CASES if c not in {x["case"] for x in commands}],
        "observer_scope": "Independent capture provenance is supplied by the operator; software cannot certify wiring or mechanical action.",
        "b2_a_ui_evidence": manifest.get("b2_a_ui_evidence"),
        "b2_complete": False,
    }
    # No automatic full B2 PASS: UI, observer calibration and physical scope
    # require review of the actual captures, not just JSON declarations.
    with (root / "classification.json").open("x") as stream:
        json.dump(output, stream, indent=2)
        stream.write("\n")
    print(root / "classification.json")


if __name__ == "__main__":
    main()
