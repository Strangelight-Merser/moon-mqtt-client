#!/usr/bin/env python3
"""Prove native benchmark overflow handling with held QoS 1 acknowledgements."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from benchmark import ROOT, percentile_with_overflow, trial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    out = (args.artifacts or Path(os.environ.get("MQTT_EVIDENCE_DIR", ROOT / "_build" / "measurement-validity"))
           / f"measurement-validity-{time.time_ns()}").resolve()
    out.mkdir(parents=True, exist_ok=False)
    subprocess.run([os.environ.get("MOON", str(ROOT / "scripts/moon.sh")),
                    "build", "--target", "native"], cwd=ROOT, check=True)
    binary = out / "benchmark_driver.exe"
    shutil.copyfile(next((ROOT / "_build/native/debug/build").glob(
        "**/benchmark_driver/benchmark_driver.exe")), binary)
    binary.chmod(0o700)

    assert percentile_with_overflow({10: 1, 99990: 1}, 1, .5) == 99990
    assert percentile_with_overflow({10: 1, 99990: 1}, 1, 1) == {
        "lower_bound_us": 100000, "upper_bound_us": None}
    results = []
    for delay_ms in (100, 160, 500):
        settings = {"name": f"held-ack-{delay_ms}", "kind": "publish",
                    "qos": 1, "payload": 32, "workers": 1, "inflight": 1,
                    "ack_delay_ms": delay_ms}
        result = trial(binary, settings, 0, 2, out / settings["name"])
        assert result["latency_overflow_count"] > 0, result
        assert result["latency_max_ns"] >= delay_ms * 1_000_000, result
        assert result["latency_us"]["p50"] == {
            "lower_bound_us": 100000, "upper_bound_us": None}, result
        results.append({"delay_ms": delay_ms, "completed": result["completed"],
                        "overflow": result["latency_overflow_count"],
                        "max_ns": result["latency_max_ns"]})
        (out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"passed": True, "results": results, "artifacts": str(out)}, indent=2))


if __name__ == "__main__":
    main()
