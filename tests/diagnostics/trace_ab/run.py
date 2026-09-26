#!/usr/bin/env python3
"""One preregistered six-pair hosted Mac trace observation-cost study."""

import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import traceback


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).resolve().parent
OUT = ROOT / "_build" / "trace-ab-hosted"
sys.path.insert(0, str(ROOT / "tests"))
from benchmark import WORKLOADS, trial  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cv(values):
    return statistics.pstdev(values) / statistics.mean(values)


def compare(pairs, protocol):
    throughputs = {mode: [pair[mode]["throughput_per_s"] for pair in pairs]
                   for mode in ("off", "on")}
    p99 = {mode: [pair[mode]["latency_us"]["p99"] for pair in pairs]
           for mode in ("off", "on")}
    if any(not isinstance(value, int) for values in p99.values() for value in values):
        return {"verdict": "indeterminate", "reason": "censored p99",
                "throughput": throughputs, "p99_us": p99}
    variation = {"throughput": {mode: cv(values) for mode, values in throughputs.items()},
                 "p99": {mode: cv(values) for mode, values in p99.items()}}
    if any(value > protocol["variation_limit_population_cv"]
           for group in variation.values() for value in group.values()):
        return {"verdict": "indeterminate", "reason": "population CV above 15%",
                "variation": variation, "throughput": throughputs, "p99_us": p99}
    off_t, on_t = (statistics.median(throughputs[mode]) for mode in ("off", "on"))
    off_p, on_p = (statistics.median(p99[mode]) for mode in ("off", "on"))
    return {"verdict": "investigate" if on_t < off_t * (1 - protocol["throughput_drop_trigger"])
            or on_p > off_p * (1 + protocol["p99_rise_trigger"]) else "within_trigger",
            "variation": variation, "throughput": throughputs, "p99_us": p99,
            "throughput_drop_fraction": (off_t - on_t) / off_t,
            "p99_rise_fraction": (on_p - off_p) / off_p}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    protocol_path = SCRIPT / "protocol.json"
    protocol_hash = digest(protocol_path)
    shutil.copyfile(protocol_path, OUT / "protocol.json")
    protocol = json.loads(protocol_path.read_text())
    assert platform.system() == "Darwin" and platform.machine() == "arm64"
    assert subprocess.check_output(["git", "rev-parse", protocol["source_commit"]],
                                   cwd=ROOT, text=True).strip() == protocol["source_commit"]
    changed = subprocess.check_output(["git", "diff", "--name-only",
                                       protocol["source_commit"], "HEAD"],
                                      cwd=ROOT, text=True).splitlines()
    assert changed and all(name == ".github/workflows/integration.yml" or
                           name.startswith("tests/diagnostics/trace_ab/") for name in changed), changed
    assert digest(ROOT / "tests/benchmark.py") == protocol["benchmark_py_sha256"]
    assert protocol["orders"] == [["off", "on"], ["on", "off"]] * 3
    assert not (OUT / "summary.json").exists(), "one preregistered attempt only"
    settings = next(w for w in WORKLOADS if w["name"] == protocol["workload"])
    summary = {"status": "preparing", "protocol_sha256": protocol_hash,
               "source_commit": protocol["source_commit"],
               "diagnostic_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                            cwd=ROOT, text=True).strip(),
               "platform": platform.platform(), "python": platform.python_version(),
               "pairs": []}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    try:
        subprocess.run(["moon", "update"], cwd=ROOT, check=True)
        subprocess.run(["moon", "build", "--target", "native"], cwd=ROOT, check=True)
        binaries = list((ROOT / "_build/native/debug/build").glob(
            "**/benchmark_driver/benchmark_driver.exe"))
        assert len(binaries) == 1, binaries
        binary = OUT / "benchmark_driver.exe"
        shutil.copyfile(binaries[0], binary)
        binary.chmod(0o700)
        summary["binary_sha256"] = digest(binary)
        summary["status"] = "running"
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        for index, order in enumerate(protocol["orders"], 1):
            pair = {}
            for mode in order:
                print(f"PAIR {index}/6 {mode}", flush=True)
                pair[mode] = trial(binary, settings, protocol["warmup_seconds"],
                                   protocol["sample_seconds"],
                                   OUT / f"pair-{index}-{mode}", trace_enabled=mode == "on")
                summary["current_pair"] = index
                summary["pairs"] = summary["pairs"][:index - 1] + [pair]
                (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        summary["comparison"] = compare(summary["pairs"], protocol)
        summary["status"] = "complete"
    except BaseException as error:
        summary["status"] = "failed"
        summary["error"] = repr(error)
        summary["traceback"] = traceback.format_exc()
        raise
    finally:
        assert digest(protocol_path) == protocol_hash, "protocol changed during run"
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["comparison"], indent=2), flush=True)


if __name__ == "__main__":
    main()
