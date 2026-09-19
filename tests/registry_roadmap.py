#!/usr/bin/env python3
"""Run unchanged behavioral suites using fresh consumer executables.

Default: install this version from Mooncakes without a workspace override.
--package ZIP: prepublication check of the extracted package in an isolated
workspace; the dependency TLS module still comes from the registry.
Only executable entry sources are copied. Runtime/library code comes entirely
from the selected dependency. Existing test assertions and budgets are reused.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

import mqtt5_runtime
import ws_protocol_faults
import ws_interop
import ha_relay

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    version = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "moon.mod").read_text(), re.M).group(1)
    moon = os.environ.get("MOON", str(ROOT / "scripts/moon.sh"))
    env = {**os.environ, "MOONBIT_ASYNC_CHECK_FD_LEAK": "1"}
    env.pop("MOON_WORK", None)
    with tempfile.TemporaryDirectory(prefix="mqtt-roadmap-consumer-") as temp:
        work = Path(temp)
        consumer = work / "consumer"
        consumer.mkdir()
        (consumer / "moon.mod").write_text(f'''name = "acceptance/roadmap"
preferred_target = "native"
import {{
  "Strangelight-Merser/moon-mqtt-client@{version}",
  "moonbitlang/async@0.21.3",
}}
''')
        entries = {"mqtt5_runtime_driver": "examples/mqtt5_runtime_driver",
                   "cli": "examples/mqtt_demo/cli",
                   "ha_relay/controller": "examples/ha_relay/controller",
                   "ha_relay/simulator": "examples/ha_relay/simulator"}
        for destination, source in entries.items():
            target = consumer / destination
            target.mkdir(parents=True)
            for filename in ("main.mbt", "moon.pkg"):
                shutil.copy2(ROOT / source / filename, target / filename)
        if args.package:
            library = work / "library"
            library.mkdir()
            with zipfile.ZipFile(args.package) as archive:
                assert archive.testzip() is None
                for name in archive.namelist():
                    path = Path(name)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("unsafe package path")
                archive.extractall(library)
            assert f'version = "{version}"' in (library / "moon.mod").read_text()
            subprocess.run([moon, "work", "init", "library", "consumer"], cwd=work, env=env, check=True)
            build_root = work
            binary_root = work / "_build/native/debug/build/acceptance/roadmap"
            source = "extracted package"
        else:
            assert not (work / "moon.work").exists()
            subprocess.run([moon, "update"], cwd=consumer, env=env, check=True)
            build_root = consumer
            binary_root = consumer / "_build/native/debug/build"
            source = "Mooncakes registry"
        subprocess.run([moon, "build", "--target", "native"], cwd=build_root, env=env, check=True)
        runtime = binary_root / "mqtt5_runtime_driver/mqtt5_runtime_driver.exe"
        cli = binary_root / "cli/cli.exe"
        for executable in (runtime, cli, binary_root / "ha_relay/controller/controller.exe", binary_root / "ha_relay/simulator/simulator.exe"):
            assert executable.is_file(), executable
        class ConsumerRuntime(mqtt5_runtime.RuntimePeers):
            @classmethod
            def setUpClass(cls):
                cls.driver = runtime
        class ConsumerFraming(ws_protocol_faults.WsFaults):
            @classmethod
            def setUpClass(cls):
                cls.cli = cli
        class ConsumerWebSocket(ws_interop.WsInterop):
            @classmethod
            def setUpClass(cls):
                cls.cli = cli
                cls.broker = ws_interop.WsBroker()
                cls.addClassCleanup(cls.broker.close)
                cls.broker.start()
        class ConsumerHa(ha_relay.HaRelay):
            @classmethod
            def setUpClass(cls):
                cls.binary = binary_root / "ha_relay"
        suite = unittest.TestSuite()
        for case in (ConsumerRuntime, ConsumerFraming, ConsumerWebSocket, ConsumerHa):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        print(f"consumer source: {source}; version: {version}; binary root: {binary_root}", flush=True)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        record = {"source": source, "version": version, "tests_run": result.testsRun,
                  "failures": len(result.failures), "errors": len(result.errors),
                  "skipped": len(result.skipped), "successful": result.wasSuccessful(),
                  "workspace_override": bool(args.package)}
        if args.evidence:
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(json.dumps(record, indent=2) + "\n")
        if not result.wasSuccessful() or result.skipped:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
