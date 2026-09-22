"""Bounded unchanged WSS assertion repetition, both unmodified production versions."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from build_paths import demo_binary
from ws_broker import WsBroker
from ws_interop import WsInterop

BASELINE = "f0a0e9ec33b7aee2aeee26ea20572904d05e3069"
CANDIDATE = "0bf9e31bb53964682770af32ab9261468a106c43"
EVIDENCE = Path(os.environ["MQTT_EVIDENCE_DIR"])
results = []
for revision in (BASELINE, CANDIDATE):
    source = ROOT / "_build" / ("wss-source-" + revision[:8])
    source.mkdir(exist_ok=False)
    archive = subprocess.check_output(["git", "archive", revision], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(source, filter="data")
    for command in ([os.environ["MOON"], "update"],
                    [os.environ["MOON"], "build", "--target", "native"]):
        subprocess.run(command, cwd=source, check=True)
    executable = demo_binary(source, "cli")
    class Measured(WsInterop):
        @classmethod
        def setUpClass(cls):
            cls.cli = executable
            cls.broker = WsBroker()
            cls.addClassCleanup(cls.broker.close)
            cls.broker.start()
    # The existing assertion is reused byte-for-byte. Stop this version on its
    # first failure; continue the other independent version comparison.
    print("BEGIN", revision, flush=True)
    suite = unittest.TestSuite(Measured("test_wss_mtls_native_qos0_qos1_both_directions") for _ in range(100))
    result = unittest.TextTestRunner(verbosity=2, failfast=True).run(suite)
    results.append({"source_commit": revision, "source_modified": False,
                    "native_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                    "attempted": result.testsRun, "budget": 100,
                    "failures": len(result.failures), "errors": len(result.errors),
                    "successful": result.wasSuccessful()})
    (EVIDENCE / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
raise SystemExit(0 if all(r["successful"] for r in results) else 1)
