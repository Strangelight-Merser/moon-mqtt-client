#!/usr/bin/env python3
"""Record exact ELF symbols and disassembly before attempting a Linux census."""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

from snapshot import ACCESSOR, GLOBAL, SET_EMPTY


ROOT = Path(__file__).resolve().parents[2]


def symbol_lines(disassembly: str, symbol: str) -> list[str]:
    lines = disassembly.splitlines()
    label = f" <{symbol}>:"
    for index, line in enumerate(lines):
        if line.endswith(label):
            result = [line]
            for following in lines[index + 1:index + 45]:
                if following and not following.startswith(" "):
                    break
                result.append(following)
            return result
    return []


def main() -> None:
    assert platform.system() == "Linux" and platform.machine() == "x86_64"
    matches = list((ROOT / "_build/native/debug/build").glob(
        "**/task_census/scope_driver/scope_driver.exe"))
    assert len(matches) == 1, matches
    binary = matches[0]
    nm = subprocess.check_output(["nm", "-a", str(binary)], text=True)
    disassembly = subprocess.check_output(
        ["objdump", "-d", "--no-show-raw-insn", str(binary)], text=True)
    elf_header = subprocess.check_output(["readelf", "-h", str(binary)], text=True)
    symbols = (GLOBAL, ACCESSOR, SET_EMPTY)
    output = Path(os.environ.get("MQTT_EVIDENCE_DIR", ROOT / "_build/task-census"))
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(binary, output / "scope_driver-linux.exe")
    report = {
        "platform": platform.platform(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "elf_type": [line.strip() for line in elf_header.splitlines()
                     if line.strip().startswith("Type:")],
        "nm_line_count": len(nm.splitlines()),
        "symbols": {symbol: {
            spelling: {
                "nm": [line for line in nm.splitlines() if line.endswith(" " + spelling)],
                "disassembly": symbol_lines(disassembly, spelling),
            } for spelling in (symbol, symbol[1:])
        } for symbol in symbols},
        "related_nm": [line for line in nm.splitlines()
                       if "scheduler" in line or "all__coroutines" in line or "is__empty" in line][:100],
        "status": "layout probe only; no task count inferred",
    }
    (output / "census-linux-layout.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
