"""Read-only, exact-binary active-coroutine census; never returns fake zero."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess

GLOBAL = "_mir.global._x24_moonbitlang_x2f_async_x2f_internal_x2f_coroutine_x2e_scheduler"
ACCESSOR = "__M0FP411moonbitlang5async8internal9coroutine15all__coroutines"
SET_EMPTY = "__M0MPC13set3Set9is__emptyGRP411moonbitlang5async8internal9coroutine9CoroutineE"
SCRIPT = Path(__file__).with_name("lldb_snapshot.py")


class Unsupported(RuntimeError):
    pass


def binary_layout(binary):
    binary = Path(binary).resolve()
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    nm = subprocess.check_output(["nm", "-a", str(binary)], text=True)
    matches = re.findall(r"^([0-9a-fA-F]+) [bB] " + re.escape(GLOBAL) + r"$", nm, re.M)
    if len(matches) != 1:
        raise Unsupported("exact scheduler global symbol unavailable")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise Unsupported("this inspected layout is Mach-O arm64 only")
    disassembly = subprocess.check_output(
        ["xcrun", "llvm-objdump", "--macho", "--disassemble", str(binary)], text=True)
    accessor = disassembly.split(ACCESSOR + ":", 1)[-1].split("\n__", 1)[0]
    set_empty = disassembly.split(SET_EMPTY + ":", 1)[-1].split("\n__", 1)[0]
    if "[x0, #0x18]" not in accessor or "[x0, #0x8]" not in set_empty:
        raise Unsupported("accessor or Set count offset differs from confirmed layout")
    return {"binary": str(binary), "sha256": sha,
            "scheduler_symbol": GLOBAL, "scheduler_symbol_address": "0x" + matches[0],
            "all_coros_offset": 24, "set_size_offset": 8,
            "accessor_symbol": ACCESSOR, "set_empty_symbol": SET_EMPTY,
            "accessor_disassembly": accessor[:1000],
            "set_empty_disassembly": set_empty[:450],
            "scope": "registered active coroutines only; not all Task objects, FFI work or allocator state"}


def snapshot(pid, layout):
    if hashlib.sha256(Path(layout["binary"]).read_bytes()).hexdigest() != layout["sha256"]:
        raise Unsupported("binary SHA changed after layout verification")
    try:
        result = subprocess.run(
            ["lldb", "--batch", "-p", str(pid), "-o", f"command script import {SCRIPT}",
             "-o", f"census-snapshot {layout['scheduler_symbol_address']}",
             "-o", "process detach"], text=True, capture_output=True, timeout=20)
        if result.returncode != 0 or "Process " + str(pid) + " detached" not in result.stdout:
            raise Unsupported("LLDB attach/read/detach failed: " +
                              (result.stdout + result.stderr)[-800:])
        lines = [line.split("CENSUS_JSON ", 1)[1] for line in result.stdout.splitlines()
                 if "CENSUS_JSON " in line]
        if len(lines) != 1:
            raise Unsupported("LLDB produced no unique census row")
        row = json.loads(lines[0])
        row.update(pid=pid, binary_sha256=layout["sha256"])
        return row
    finally:
        # The controlled child must never be left stopped after a debugger error.
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
