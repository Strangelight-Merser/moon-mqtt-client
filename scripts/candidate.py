#!/usr/bin/env python3
"""Build local review assets. This command never uploads or publishes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from acceptance import ROOT, source_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_build/candidate")
    args = parser.parse_args()
    identity = source_identity()
    version = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "moon.mod").read_text(), re.M).group(1)
    tree_digest = hashlib.sha256(json.dumps(identity["files"], sort_keys=True).encode()).hexdigest()
    candidate = f"{version}-audit-{identity['commit'][:8]}-{tree_digest[:12]}"
    output = args.output.resolve() / candidate
    if (output / "candidate.json").exists():
        existing = json.loads((output / "candidate.json").read_text())
        assert existing["source"]["files"] == identity["files"]
        assert existing["source"]["commit"] == identity["commit"]
        for name, digest in existing["sha256"].items():
            assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
        (args.output / "latest.json").write_text(json.dumps({"manifest": str(output / "candidate.json")}) + "\n")
        print(output / "candidate.json")
        return
    output.mkdir(parents=True, exist_ok=False)
    # Package the real module using the pinned compiler's package selection.
    package_root = output / "package-build"
    subprocess.run([os.environ.get("MOON", str(ROOT / "scripts/moon.sh")), "package",
                    "--target-dir", str(package_root)], cwd=ROOT, check=True)
    packages = list(package_root.rglob("*.zip"))
    assert len(packages) == 1, packages
    library = output / f"moon-mqtt-client-{candidate}.zip"
    shutil.copy2(packages[0], library)
    files = {"durable_recovery.py": ROOT / "scripts/durable_recovery.py",
             "requirements.txt": ROOT / "tests/integration/requirements.txt",
             "DURABLE-RECOVERY.md": ROOT / "docs/architecture/DURABLE-RECOVERY.md",
             "README.md": ROOT / "docs/OPERATOR-INSTALL.md", "LICENSE": ROOT / "LICENSE"}
    payloads = {name: path.read_bytes() for name, path in files.items()}
    payloads["provenance.json"] = (json.dumps({"candidate": candidate, "module_version": version,
        "source_commit": identity["commit"], "source_dirty": identity["dirty"],
        "source_files_sha256": tree_digest, "published": False}, indent=2) + "\n").encode()
    payloads["SHA256SUMS.json"] = (json.dumps({name: hashlib.sha256(data).hexdigest()
        for name, data in payloads.items()}, indent=2) + "\n").encode()
    operator = output / f"moon-mqtt-operator-{candidate}.zip"
    with zipfile.ZipFile(operator, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    manifest = {"candidate": candidate, "module_version": version, "published": False,
        "source": identity, "library": str(library), "operator": str(operator),
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (library, operator)}}
    (output / "candidate.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in manifest["sha256"].items()))
    # A pointer is convenience only; consumers verify the immutable manifest.
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "latest.json").write_text(json.dumps({"manifest": str(output / "candidate.json")}) + "\n")
    print(output / "candidate.json")


if __name__ == "__main__":
    main()
