#!/usr/bin/env python3
"""Install the operator ZIP in a new venv and run every recovery entry point."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

import durable_recovery_admin as acceptance

ROOT = Path(__file__).resolve().parents[1]


def unpack(archive, destination):
    destination.mkdir()
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        for name in package.namelist():
            path = Path(name)
            assert not path.is_absolute() and ".." not in path.parts, name
        package.extractall(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    env = os.environ.copy()
    env.pop("MOON_WORK", None)
    with tempfile.TemporaryDirectory(prefix="moon-operator-consumer-") as temporary:
        work = Path(temporary)
        operator, library, consumer = work / "operator", work / "library", work / "consumer"
        unpack(args.operator, operator)
        unpack(args.package, library)
        checksums = json.loads((operator / "SHA256SUMS.json").read_text())
        for name, expected in checksums.items():
            assert Path(name).name == name, name
            assert hashlib.sha256((operator / name).read_bytes()).hexdigest() == expected, name
        operator_version = (operator / "VERSION").read_text().strip()
        provenance = json.loads((operator / "provenance.json").read_text())
        formats = json.loads((operator / "FORMAT-SUPPORT.json").read_text())
        assert provenance["operator_version"] == operator_version
        assert formats == {"durable_schema": [2], "recovery_request": [1],
                           "archive_manifest": [1], "recovery_journal": [1],
                           "online_recovery_transport": ["plain_tcp"],
                           "resolve_resume": "experimental"}
        subprocess.run([sys.executable, "-m", "venv", str(work / "venv")], check=True, env=env)
        python = work / "venv/bin/python"
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                        "-r", str(operator / "requirements.txt")], check=True, cwd=operator, env=env)
        for entry in ("inspect", "export", "verify", "resolve", "resume", "status"):
            subprocess.run([str(python), str(operator / "durable_recovery.py"), entry, "--help"],
                           cwd=operator, check=True, env=env, stdout=subprocess.DEVNULL)
        version = re.search(r'^version\s*=\s*"([^"]+)"', (library / "moon.mod").read_text(), re.M).group(1)
        consumer.mkdir()
        (consumer / "moon.mod").write_text(f'''name = "acceptance/operator"
preferred_target = "native"
import {{
  "Strangelight-Merser/moon-mqtt-client@{version}",
  "moonbitlang/async@0.21.3",
}}
''')
        for name, source in {"inspector": "tests/recovery_inspector", "durable": "examples/durable_recovery_driver"}.items():
            (consumer / name).mkdir()
            for entry in ("main.mbt", "moon.pkg"):
                shutil.copyfile(ROOT / source / entry, consumer / name / entry)
        moon = os.environ.get("MOON", str(ROOT / "scripts/moon.sh"))
        subprocess.run([moon, "work", "init", "library", "consumer"], cwd=work, env=env, check=True)
        subprocess.run([moon, "build", "--target", "native"], cwd=work, env=env, check=True)
        binaries = work / "_build/native/debug/build/acceptance/operator"
        class OperatorBundle(acceptance.DurableRecoveryAdmin):
            @classmethod
            def setUpClass(cls):
                cls.native_inspector = binaries / "inspector/inspector.exe"
                cls.durable_driver = binaries / "durable/durable.exe"
                assert cls.native_inspector.is_file() and cls.durable_driver.is_file()
        acceptance.CLI = operator / "durable_recovery.py"
        acceptance.PYTHON = python
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(OperatorBundle)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        record = {"source": "extracted library and operator candidates", "workspace_override": True,
            "clean_venv": True, "MOON_WORK_removed": "MOON_WORK" not in env,
            "module_version": version, "operator_version": operator_version,
            "format_support": formats,
            "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "successful": result.wasSuccessful() and not result.skipped,
            "operator_sha256": hashlib.sha256(args.operator.read_bytes()).hexdigest(),
            "library_sha256": hashlib.sha256(args.package.read_bytes()).hexdigest(),
            "provenance": provenance}
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(record, indent=2) + "\n")
        if not record["successful"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
