#!/usr/bin/env python3
"""A failed acceptance still seals stable nested evidence files."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from evidence_integrity import inventory_files, write_inventory


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nested = root / "benchmark-1"
        nested.mkdir()
        (nested / "result.json").write_text('{"trial":1}\n')
        (root / "result.json").write_text('{"successful":false}\n')
        (root / "evidence-integrity.log").write_text("still being written\n")
        (root / "secret.key").write_text("fixture secret\n")
        (root / "store.sqlite3-journal").write_text("raw store\n")
        files = inventory_files(root)
        assert files == {
            "benchmark-1/result.json":
                hashlib.sha256((nested / "result.json").read_bytes()).hexdigest()
        }, files
        write_inventory(root, {"source_commit": "frozen", "source_unchanged": True}, False)
        sealed = json.loads((root / "inventory.json").read_text())
        assert sealed["files"] == files
        assert sealed["source_commit"] == "frozen"
        assert sealed["required_measurements_complete"] is False
        assert "result.json" not in sealed["files"]
        (root / "source.json").write_text(
            json.dumps({"commit": "frozen", "files": {}}) + "\n"
        )
        process = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("evidence_integrity.py"))],
            env={**os.environ, "MQTT_EVIDENCE_DIR": str(root)},
            capture_output=True, text=True,
        )
        assert process.returncode != 0, process.stdout
        sealed = json.loads((root / "inventory.json").read_text())
        assert sealed["required_measurements_complete"] is False
        assert "benchmark-1/result.json" in sealed["files"]
        assert "result.json" not in sealed["files"]
    print("Failure inventory and nested result sealing passed")


if __name__ == "__main__":
    main()
