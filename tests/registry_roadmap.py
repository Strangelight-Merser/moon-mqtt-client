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
import hashlib
import platform
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
import reason_reconnect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--version", help="Published registry version; required without --package")
    args = parser.parse_args()
    if not args.package and not args.version:
        parser.error("registry verification requires explicit --version after publication")
    module_text = (ROOT / "moon.mod").read_text()
    if args.package:
        with zipfile.ZipFile(args.package) as archive:
            module_text = archive.read("moon.mod").decode()
    version = args.version or re.search(r'^version\s*=\s*"([^"]+)"', module_text, re.M).group(1)
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
                   "reason_reconnect_driver": "tests/reason_reconnect_driver",
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
        class ConsumerMaintenance(reason_reconnect.ReasonReconnect):
            @classmethod
            def setUpClass(cls):
                cls.driver = binary_root / "reason_reconnect_driver/reason_reconnect_driver.exe"
                assert cls.driver.is_file(), cls.driver
        suite = unittest.TestSuite()
        cases = [ConsumerRuntime, ConsumerFraming, ConsumerHa, ConsumerMaintenance]
        excluded = []
        if platform.system() == "Linux":
            cases.append(ConsumerWebSocket)
        else:
            excluded.append("EMQX WS/WSS requires supported Linux Docker service; framing still runs")
        for case in cases:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        print(f"consumer source: {source}; version: {version}; binary root: {binary_root}", flush=True)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        record = {"source": source, "version": version, "tests_run": result.testsRun,
                  "failures": len(result.failures), "errors": len(result.errors),
                  "skipped": len(result.skipped), "successful": result.wasSuccessful(),
                  "workspace_override": bool(args.package), "platform_excluded": excluded,
                  "MOON_WORK_removed": "MOON_WORK" not in env,
                  "package_sha256": hashlib.sha256(args.package.read_bytes()).hexdigest() if args.package else None,
                  "entry_sha256": {source + "/" + name: hashlib.sha256((ROOT / source / name).read_bytes()).hexdigest()
                                    for source in entries.values() for name in ("main.mbt", "moon.pkg")}}
        if args.evidence:
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(json.dumps(record, indent=2) + "\n")
        if not result.wasSuccessful() or result.skipped:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
