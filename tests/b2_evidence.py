#!/usr/bin/env python3
"""Synthetic classifier cases only; never physical B2 acceptance."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b2_evidence", ROOT / "examples/esp32/b2_evidence.py")
b2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b2)


class Classification(unittest.TestCase):
    def test_calibration_coverage_and_count_categories(self):
        with tempfile.TemporaryDirectory(prefix="b2-classifier-") as temporary:
            root = Path(temporary)
            files = {}
            for name in ("candidate", "firmware", "consumer", "calibration", "capture"):
                path = root / name
                path.write_bytes(name.encode())
                files[name] = b2.file_identity(path)
            manifest = {
                **{name: files[name] for name in ("candidate", "firmware", "consumer")},
                "calibration": {"two_distinct_applies_resolved": False,
                                "raw_capture": files["calibration"]},
                "cases": {},
            }
            commands = [{"id": f"id-{i}", "case": "baseline"} for i in range(5)]
            physical = [
                {"id": f"id-{i}", "apply_count": i, "independent_observer": True,
                 "coverage_complete": i != 3, "raw_capture": files["capture"]}
                for i in range(4)
            ]
            (root / "run.json").write_text(json.dumps(manifest))
            (root / "commands.jsonl").write_text("".join(json.dumps(x) + "\n" for x in commands))
            (root / "physical.jsonl").write_text("".join(json.dumps(x) + "\n" for x in physical))
            result = b2.classify_run(root)
            self.assertEqual([x["execution"] for x in result["commands"]],
                             ["evidence_insufficient"] * 5)
            manifest["calibration"]["two_distinct_applies_resolved"] = True
            (root / "run.json").write_text(json.dumps(manifest))
            result = b2.classify_run(root)
            self.assertEqual([x["execution"] for x in result["commands"]],
                             ["zero", "once", "multiple", "evidence_insufficient",
                              "evidence_insufficient"])
            self.assertFalse(result["b2_complete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
