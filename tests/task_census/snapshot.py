"""Read-only, exact-binary active-coroutine census; never returns fake zero."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import struct
import subprocess
import time

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
    if platform.system() == "Linux" and platform.machine() == "x86_64":
        return linux_binary_layout(binary, sha, nm)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise Unsupported("no inspected census layout for this platform")
    matches = re.findall(r"^([0-9a-fA-F]+) [bB] " + re.escape(GLOBAL) + r"$", nm, re.M)
    if len(matches) != 1:
        raise Unsupported("exact scheduler global symbol unavailable")
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


def linux_binary_layout(binary, sha, nm):
    data = binary.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise Unsupported("expected little-endian ELF64")
    if struct.unpack_from("<HH", data, 16) != (3, 62):
        raise Unsupported("expected x86-64 PIE executable")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    if phentsize < 56 or phoff + phentsize * phnum > len(data):
        raise Unsupported("invalid ELF program headers")
    loads = [struct.unpack_from("<IIQQQQQQ", data, phoff + phentsize * n)
             for n in range(phnum)]
    if not any(row[0] == 1 and row[2] == row[3] == 0 for row in loads):
        raise Unsupported("PIE has no zero-offset, zero-vaddr PT_LOAD")
    names = [symbol[1:] for symbol in (GLOBAL, ACCESSOR, SET_EMPTY)]
    patterns = ("[dD]", "[tT]", "[tT]")
    addresses = []
    for symbol, kind in zip(names, patterns):
        found = re.findall(r"^([0-9a-fA-F]+) " + kind + " " +
                           re.escape(symbol) + r"$", nm, re.M)
        if len(found) != 1:
            raise Unsupported("exact ELF symbol unavailable: " + symbol)
        addresses.append(int(found[0], 16))
    _, accessor, set_empty = addresses

    def disassemble(start, length):
        return subprocess.check_output(
            ["objdump", "-d", "--no-show-raw-insn",
             f"--start-address={start}", f"--stop-address={start + length}",
             str(binary)], text=True)

    accessor_code = disassemble(accessor, 48)
    set_empty_code = disassemble(set_empty, 32)
    if (not re.search(r"0x18\(%rdi\),\s*%rbx", accessor_code) or
            names[0] not in accessor_code or
            not re.search(r"0x8\(%rdi\),\s*%edi", set_empty_code)):
        raise Unsupported("ELF accessor or Set count layout differs from inspected binary")
    return {"binary": str(binary), "sha256": sha, "format": "ELF64 x86-64 PIE",
            "scheduler_symbol": names[0], "scheduler_symbol_address": hex(addresses[0]),
            "all_coros_offset": 24, "set_size_offset": 8,
            "accessor_symbol": names[1], "set_empty_symbol": names[2],
            "accessor_disassembly": accessor_code[:1500],
            "set_empty_disassembly": set_empty_code[:600],
            "scope": "registered active coroutines only; not all Task objects, FFI work or allocator state"}


def linux_snapshot(pid, layout):
    status = Path(f"/proc/{pid}/status")
    maps = Path(f"/proc/{pid}/maps")
    os.kill(pid, signal.SIGSTOP)
    started = time.monotonic()
    try:
        deadline = started + 2
        while True:
            state = next((line for line in status.read_text().splitlines()
                          if line.startswith("State:")), "")
            if "\tT " in state:
                break
            if time.monotonic() >= deadline:
                raise Unsupported("controlled child did not enter SIGSTOP state")
            time.sleep(.005)
        binary = str(Path(layout["binary"]).resolve())
        bases = []
        for line in maps.read_text().splitlines():
            fields = line.split(maxsplit=5)
            if (len(fields) == 6 and fields[2] == "00000000" and
                    fields[5] == binary and "r" in fields[1]):
                bases.append(int(fields[0].split("-", 1)[0], 16))
        if len(bases) != 1:
            raise Unsupported("exact executable PIE base unavailable in /proc maps")
        address = bases[0] + int(layout["scheduler_symbol_address"], 16)
        try:
            with open(f"/proc/{pid}/mem", "rb", buffering=0) as memory:
                def read_int(where, size):
                    raw = os.pread(memory.fileno(), size, where)
                    if len(raw) != size:
                        raise Unsupported("short read from stopped child")
                    return int.from_bytes(raw, "little")

                scheduler = read_int(address, 8)
                if scheduler < 0x10000:
                    raise Unsupported("scheduler pointer is null or implausible")
                active_set = read_int(scheduler + layout["all_coros_offset"], 8)
                if active_set < 0x10000:
                    raise Unsupported("active coroutine Set pointer is null or implausible")
                count = read_int(active_set + layout["set_size_offset"], 4)
                if count > 1_000_000:
                    raise Unsupported("active coroutine count is implausible")
        except OSError as error:
            raise Unsupported("read-only /proc child memory access failed: " + str(error)) from error
        return {"count": count, "pid": pid, "binary_sha256": layout["sha256"],
                "method": "SIGSTOP and read-only /proc/pid/mem; SIGCONT in finally",
                "pie_base": hex(bases[0]), "scheduler_address": hex(address),
                "pause_ms": round((time.monotonic() - started) * 1000, 3)}
    finally:
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass


def snapshot(pid, layout):
    if hashlib.sha256(Path(layout["binary"]).read_bytes()).hexdigest() != layout["sha256"]:
        raise Unsupported("binary SHA changed after layout verification")
    if layout.get("format") == "ELF64 x86-64 PIE":
        return linux_snapshot(pid, layout)
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
