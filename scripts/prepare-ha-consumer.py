#!/usr/bin/env python3
"""Create and build a fresh controller consumer from Mooncakes 0.7.1.

The output directory must not exist and must be outside every ``moon.work``
ancestor.  A tiny bootstrap package imports the published dependency only to
materialize it.  The final controller entrypoint is copied from the fetched
Mooncakes package and checked against the frozen R8 hashes below.  This script
only creates a consumer and builds it.  It does not start a broker, Home
Assistant, a simulator, GPIO, serial I/O, or any hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MOON = ROOT / "scripts/moon.sh"
PUBLISHED_VERSION = "0.7.1"
PUBLISHED_PACKAGE_ROOT = "Strangelight-Merser/moon-mqtt-client"
EXPECTED_CONTROLLER_HASHES = {
    "main.mbt": "ebcc484a95b22ad63609570497cbbfc624cf1310be681bee2b8e370c0ff27163",
    "moon.pkg": "2f3ad51309e2ff11c0d5a1c00f5215e0a7421a118d11022e9da2895e13b5b206",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def published_root(output: Path) -> Path:
    candidate = output / ".mooncakes" / "Strangelight-Merser" / "moon-mqtt-client"
    if not candidate.is_dir():
        raise RuntimeError(f"published dependency was not materialized at {candidate}")
    return candidate


def reject_moon_work_ancestor(output: Path) -> None:
    ancestor = output.parent
    while True:
        if (ancestor / "moon.work").is_file():
            raise SystemExit(
                f"refusing output below moon.work ancestor {ancestor}; "
                "use a new directory under /tmp or another isolated parent"
            )
        if ancestor.parent == ancestor:
            return
        ancestor = ancestor.parent


def write_consumer_module(output: Path) -> Path:
    (output / "moon.mod").write_text(
        f'''name = "acceptance/ha-controller"
preferred_target = "native"
import {{
  "Strangelight-Merser/moon-mqtt-client@{PUBLISHED_VERSION}",
  "moonbitlang/async@0.21.3",
}}
'''
    )
    (output / "moon.pkg").write_text(
        '''import {
  "Strangelight-Merser/moon-mqtt-client"
}
supported_targets = "+native"
pkgtype(kind: "executable")
'''
    )
    (output / "main.mbt").write_text(
        'fn main { println("Mooncakes dependency bootstrap") }\n'
    )
    return output / "controller"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_dir",
        type=Path,
        help="new output directory; it must not already exist",
    )
    args = parser.parse_args()

    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise SystemExit(f"refusing to overwrite existing output directory: {args.output_dir}")
    output = args.output_dir.resolve()
    reject_moon_work_ancestor(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()

    env = {**os.environ, "MOONBIT_ASYNC_CHECK_FD_LEAK": "1"}
    env.pop("MOON_WORK", None)
    write_consumer_module(output)
    print(f"consumer output: {output}", flush=True)
    print(f"package: Strangelight-Merser/moon-mqtt-client@{PUBLISHED_VERSION}", flush=True)
    print("phase: update and bootstrap build", flush=True)
    subprocess.run([str(MOON), "update"], cwd=output, env=env, check=True)
    subprocess.run([str(MOON), "build", "--target", "native"], cwd=output, env=env, check=True)

    fetched_package = published_root(output)
    dependency_mod = fetched_package / "moon.mod"
    dependency_text = dependency_mod.read_text()
    dependency_name = re.search(r'^name\s*=\s*"([^"]+)"', dependency_text, re.M)
    dependency_version = re.search(r'^version\s*=\s*"([^"]+)"', dependency_text, re.M)
    if dependency_name is None or dependency_name.group(1) != PUBLISHED_PACKAGE_ROOT:
        raise RuntimeError(f"unexpected fetched dependency name in {dependency_mod}")
    if dependency_version is None or dependency_version.group(1) != PUBLISHED_VERSION:
        raise RuntimeError(f"unexpected fetched dependency version in {dependency_mod}")
    fetched = fetched_package / "examples/ha_relay/controller"
    controller = output / "controller"
    controller.mkdir()
    hashes = {
        "main.mbt": {
            "fetched_mooncakes": sha256(fetched / "main.mbt"),
            "expected_r8": EXPECTED_CONTROLLER_HASHES["main.mbt"],
        },
        "moon.pkg": {
            "fetched_mooncakes": sha256(fetched / "moon.pkg"),
            "expected_r8": EXPECTED_CONTROLLER_HASHES["moon.pkg"],
        },
    }
    if any(values["fetched_mooncakes"] != values["expected_r8"] for values in hashes.values()):
        raise RuntimeError(f"fetched controller source differs from frozen R8 hashes: {hashes}")
    shutil.copy2(fetched / "main.mbt", controller / "main.mbt")
    shutil.copy2(fetched / "moon.pkg", controller / "moon.pkg")
    print(f"fetched package root: {fetched.parents[2]}", flush=True)
    print(f"controller hashes: {json.dumps(hashes, sort_keys=True)}", flush=True)
    print("phase: final build from fetched controller entrypoint", flush=True)
    subprocess.run([str(MOON), "build", "--target", "native"], cwd=output, env=env, check=True)

    binary = output / "_build/native/debug/build/controller/controller.exe"
    if not binary.is_file():
        raise RuntimeError(f"native controller executable was not produced: {binary}")
    manifest = {
        "package": f"{PUBLISHED_PACKAGE_ROOT}@{PUBLISHED_VERSION}",
        "dependency_name": dependency_name.group(1),
        "dependency_version": dependency_version.group(1),
        "dependency_source": str(fetched_package),
        "controller_source": "fetched .mooncakes/examples/ha_relay/controller",
        "controller_hashes": hashes,
        "binary": str(binary.relative_to(output)),
        "binary_sha256": sha256(binary),
        "broker_started": False,
        "home_assistant_started": False,
        "simulator_included": False,
        "gpio_or_serial_used": False,
    }
    (output / "consumer-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"built controller: {binary}", flush=True)
    print(f"manifest: {output / 'consumer-manifest.json'}", flush=True)
    print("HOST_CONSUMER_BUILD=PASS; HIL=NOT_RUN", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"prepare-ha-consumer: ERROR: {error}", file=sys.stderr)
        raise
