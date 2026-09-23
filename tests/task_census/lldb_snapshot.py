"""LLDB read-only snapshot for a SHA-verified MoonBit native executable."""

import json
import lldb
import sys


def snapshot(debugger, command, result, _internal_dict):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    module = target.GetModuleAtIndex(0)
    header = module.GetObjectFileHeaderAddress().GetLoadAddress(target)
    symbol_address = int(command.strip(), 0)
    # MoonBit's native Mach-O image has a 0x100000000 preferred header.
    slide = header - 0x100000000
    error = lldb.SBError()

    def read(address, length):
        value = process.ReadMemory(address, length, error)
        if error.Fail() or len(value) != length:
            raise RuntimeError(f"read failed at {address:#x}: {error.GetCString()}")
        return value

    try:
        scheduler = int.from_bytes(read(symbol_address + slide, 8), sys.byteorder)
        if not scheduler:
            raise RuntimeError("scheduler global is null")
        all_coros = int.from_bytes(read(scheduler + 0x18, 8), sys.byteorder)
        if not all_coros:
            raise RuntimeError("all_coros Set pointer is null")
        count = int.from_bytes(read(all_coros + 0x8, 4), sys.byteorder)
        if count > 100000:
            raise RuntimeError("Set count exceeds sanity bound")
        result.PutCString("CENSUS_JSON " + json.dumps({
            "count": count, "symbol_address": hex(symbol_address),
            "slide": hex(slide), "scheduler_address": hex(scheduler),
            "set_address": hex(all_coros), "status": "sampled"}))
    except Exception as exc:
        result.SetError(str(exc))


def __lldb_init_module(debugger, _internal_dict):
    debugger.HandleCommand("command script add -f lldb_snapshot.snapshot census-snapshot")
