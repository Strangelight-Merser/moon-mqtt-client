#!/usr/bin/env python3
"""Build a persistent HA consumer against an explicitly selected candidate ZIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    manifest = json.loads(args.manifest.read_text())
    package = Path(manifest["library"])
    assert sha(package) == manifest["sha256"][package.name], "Candidate package hash mismatch"
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    library = out / "library"
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            assert not Path(name).is_absolute() and ".." not in Path(name).parts
        archive.extractall(library)
    consumer = out / "consumer"
    consumer.mkdir()
    (consumer / "moon.mod").write_text('name = "acceptance/stage-ha"\npreferred_target = "native"\nimport {\n  "Strangelight-Merser/moon-mqtt-client@0.7.1",\n  "moonbitlang/async@0.21.3",\n}\n')
    source = library / "examples/ha_relay/controller"
    entries = {}
    for name in ("main.mbt", "moon.pkg"):
        shutil.copy2(source / name, consumer / name)
        entries[name] = sha(consumer / name)
    env = dict(os.environ)
    env.pop("MOON_WORK", None)
    moon = env.get("MOON", str(Path(__file__).resolve().parent / "moon.sh"))
    subprocess.run([moon, "work", "init", "library", "consumer"], cwd=out, env=env, check=True)
    subprocess.run([moon, "build", "--target", "native"], cwd=out, env=env, check=True)
    binary = out / "_build/native/debug/build/acceptance/stage-ha/stage-ha.exe"
    assert binary.is_file(), binary
    result = {"source": "extracted candidate package", "registry_acceptance": False,
              "workspace_override": True, "candidate": manifest["candidate"],
              "source_commit": manifest["source"]["commit"], "source_tree": manifest["source"]["tree"],
              "library": str(package), "library_sha256": sha(package),
              "entry_sha256": entries, "binary": str(binary), "binary_sha256": sha(binary)}
    (out / "consumer-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
