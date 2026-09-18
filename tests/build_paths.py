#!/usr/bin/env python3
"""Resolve compiled native example binaries regardless of the build layout.

`moon build` writes example packages under different directory shapes depending
on whether the module is built inside a workspace (`moon.work`) or standalone:

  workspace:  _build/native/debug/build/<owner>/<module>/examples/mqtt_demo/<pkg>/<pkg>.exe
  standalone: _build/native/debug/build/examples/mqtt_demo/<pkg>/<pkg>.exe

Hardcoding either shape silently runs a stale binary (or none) after the other
one is produced. The resolver below derives the layout from `moon.work` and the
module name declared in `moon.mod`, and never falls back to the other layout, so
a clean `moon build` and a standalone module build each resolve their own
executable. Callers get the expected concrete path and report it themselves
when it is missing.
"""
from __future__ import annotations

import re
from pathlib import Path

NATIVE_BUILD = Path("_build/native/debug/build")
DEMO_EXAMPLES = Path("examples/mqtt_demo")


def module_name(root: Path) -> str:
    text = (root / "moon.mod").read_text(encoding="utf-8")
    match = re.search(r'^name\s*=\s*"([^"]+)"\s*$', text, re.MULTILINE)
    if not match:
        raise RuntimeError("moon.mod does not declare a parseable module name")
    return match.group(1)


def demo_build_dir(root: Path) -> Path:
    """Directory that holds the compiled `examples/mqtt_demo` packages.

    `moon.work` decides the layout, not the presence of an old build tree: a
    leftover directory from a previous layout must never be preferred over the
    one the current configuration produces.
    """
    base = root / NATIVE_BUILD
    if (root / "moon.work").is_file():
        return base.joinpath(*module_name(root).split("/")) / DEMO_EXAMPLES
    return base / DEMO_EXAMPLES


def demo_binary(root: Path, package: str) -> Path:
    """Path to the compiled `<package>` executable under `examples/mqtt_demo`.

    Only the layout selected by `demo_build_dir` is considered: if the current
    build did not produce the binary, the missing expected path is returned so
    the caller fails loudly instead of running an executable left behind by a
    different (stale) layout. Native builds name the file `<package>.exe`; a
    missing extension is accepted too in case a toolchain drops it.
    """
    directory = demo_build_dir(root) / package
    for name in (f"{package}.exe", package):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return directory / f"{package}.exe"
