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
    operator_version = (ROOT / "scripts/operator-version.txt").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", operator_version), operator_version
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
             "README.md": ROOT / "docs/OPERATOR-INSTALL.md", "LICENSE": ROOT / "LICENSE",
             "VERSION": ROOT / "scripts/operator-version.txt"}
    payloads = {name: path.read_bytes() for name, path in files.items()}
    payloads["FORMAT-SUPPORT.json"] = (json.dumps({
        "durable_schema": [2], "recovery_request": [1], "archive_manifest": [1],
        "recovery_journal": [1], "online_recovery_transport": ["plain_tcp"],
        "resolve_resume": "experimental"}, indent=2) + "\n").encode()
    operator_digest = hashlib.sha256(b"".join(name.encode() + b"\0" + payloads[name]
                                             for name in sorted(payloads))).hexdigest()
    operator_candidate = f"{operator_version}-audit-{identity['commit'][:8]}-{operator_digest[:12]}"
    payloads["provenance.json"] = (json.dumps({"candidate": candidate,
        "operator_candidate": operator_candidate, "operator_version": operator_version,
        "module_version": version,
        "source_commit": identity["commit"], "source_dirty": identity["dirty"],
        "source_files_sha256": tree_digest, "published": False}, indent=2) + "\n").encode()
    payloads["SHA256SUMS.json"] = (json.dumps({name: hashlib.sha256(data).hexdigest()
        for name, data in payloads.items()}, indent=2) + "\n").encode()
    operator = output / f"moon-mqtt-operator-{operator_candidate}.zip"
    with zipfile.ZipFile(operator, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    manifest = {"candidate": candidate, "module_version": version,
        "operator_candidate": operator_candidate, "operator_version": operator_version,
        "published": False,
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
