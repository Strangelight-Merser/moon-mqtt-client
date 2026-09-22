#!/usr/bin/env python3
"""Run the shared acceptance list; retain every attempt without automatic retries."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def source_identity():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
             for name in sorted(set(names)) if name and (ROOT / name).is_file()}
    return {"commit": git("rev-parse", "HEAD").decode().strip(),
            "tree": git("rev-parse", "HEAD^{tree}").decode().strip(),
            "dirty": bool(git("status", "--porcelain")),
            "files": files, "platform": platform.platform(), "python": sys.version}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", nargs="?", choices=("core", "integration", "release"), default="integration")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--only", nargs="*", help="Explicit diagnostic subset; never reported as a full level pass")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "tests/scenarios.json").read_text())
    level = manifest["levels"].index(args.level)
    scenarios = [s for s in manifest["scenarios"] if manifest["levels"].index(s["level"]) <= level
                 and ("only_levels" not in s or args.level in s["only_levels"])]
    if args.only:
        unknown = set(args.only) - {s["id"] for s in scenarios}
        if unknown:
            parser.error(f"unknown scenarios: {sorted(unknown)}")
        scenarios = [s for s in scenarios if s["id"] in args.only]
    if args.list:
        print(json.dumps(scenarios, indent=2)); return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = (args.evidence or ROOT / "_build/acceptance") / f"{args.level}-{stamp}-{os.getpid()}"
    directory.mkdir(parents=True, exist_ok=False)
    source = source_identity()
    (directory / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    variables = {"moon": os.environ.get("MOON", str(ROOT / "scripts/moon.sh")), "python": sys.executable}
    env = {**os.environ, "MOON": variables["moon"], "PYTHON": sys.executable,
           "MOONBIT_ASYNC_CHECK_FD_LEAK": "1", "MQTT_EVIDENCE_DIR": str(directory)}
    records = []
    result = {"level": args.level, "subset": args.only, "source_commit": source["commit"],
              "platform": platform.system(), "attempts": records, "successful": False}
    for scenario in scenarios:
        row = {"id": scenario["id"], "attempt": 1}
        records.append(row)
        if "platforms" in scenario and platform.system() not in scenario["platforms"]:
            row.update(status="platform-not-applicable", reason=scenario["platform_reason"])
            print(f"N/A {scenario['id']}: {row['reason']}", flush=True)
            continue
        command = [x.format(**variables) for x in scenario["command"]]
        log = directory / f"{scenario['id']}.log"
        row.update(command=command, log=log.name)
        print(f"RUN {scenario['id']} → {log}", flush=True)
        start = time.monotonic()
        try:
            with log.open("w") as output:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = process.wait(timeout=scenario.get("timeout", 600))
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
            row.update(status="passed" if code == 0 else "failed", returncode=code)
        except (OSError, subprocess.TimeoutExpired) as error:
            row.update(status="failed", error=str(error))
        row["seconds"] = round(time.monotonic() - start, 3)
        if log.exists():
            row["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
        print(f"{row['status'].upper()} {scenario['id']} ({row['seconds']}s)", flush=True)
        if row["status"] == "failed" and log.exists():
            # Surface the first failure while longer independent scenarios run.
            # Keep the complete log and its hash in the attempt directory.
            print(log.read_text(errors="replace")[-12000:], flush=True)
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    result["successful"] = all(r["status"] in ("passed", "platform-not-applicable") for r in records)
    result["full_level"] = not bool(args.only)
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Evidence: {directory}")
    if not result["successful"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
