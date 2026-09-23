#!/usr/bin/env python3
"""Check the two native worker calibrations and rejected fake samplers."""

import json
import os
from pathlib import Path

from calibration import require_pair, require_worker_sample


def rejected(callback):
    try:
        callback()
    except AssertionError:
        return
    raise AssertionError("invalid calibration was accepted")


def main():
    for workers in (1, 16):
        require_worker_sample(1, [workers + 2], workers)
        rejected(lambda: require_worker_sample(1, [1], workers))
        rejected(lambda: require_worker_sample(0, [0], workers))
        rejected(lambda: require_worker_sample(1, [workers + 3], workers))

    root = Path(os.environ["MQTT_EVIDENCE_DIR"])
    reports = [
        json.loads(path.read_text())
        for path in root.glob("census-*/summary.json")
    ]
    by_workers = {row["workers"]: row for row in reports
                  if row["workers"] in (1, 16)}
    assert len(by_workers) == 2, by_workers
    require_pair(by_workers[1], by_workers[16])
    rejected(lambda: require_pair(
        by_workers[1],
        {**by_workers[16], "scope_open_counts": [by_workers[1]["scope_open_counts"][0]]},
    ))
    print("Native one/sixteen-worker calibration and negative oracles passed")


if __name__ == "__main__":
    main()
