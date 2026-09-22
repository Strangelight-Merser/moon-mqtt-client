#!/usr/bin/env python3
"""Verify a prepared immutable candidate and its actual clean consumers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from acceptance import source_identity

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    path = args.manifest or Path(json.loads((ROOT / "_build/candidate/latest.json").read_text())["manifest"])
    manifest = json.loads(path.read_text())
    assert source_identity()["files"] == manifest["source"]["files"], "candidate source or consumer entrypoints changed"
    for key in ("library", "operator"):
        package = Path(manifest[key])
        assert hashlib.sha256(package.read_bytes()).hexdigest() == manifest["sha256"][package.name]
    evidence = Path(os.environ.get("MQTT_EVIDENCE_DIR", path.parent))
    (evidence / "candidate.json").write_text(json.dumps(manifest, indent=2) + "\n")
    commands = [
        [sys.executable, "tests/registry_roadmap.py", "--package", manifest["library"],
         "--evidence", str(evidence / "library-consumer.json")],
        [sys.executable, "tests/operator_consumer.py", "--package", manifest["library"],
         "--operator", manifest["operator"], "--evidence", str(evidence / "operator-consumer.json")],
    ]
    failed = False
    for command in commands:
        failed |= subprocess.run(command, cwd=ROOT).returncode != 0
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
