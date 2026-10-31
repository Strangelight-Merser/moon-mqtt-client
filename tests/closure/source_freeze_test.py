"""Exercise real acceptance/package entrypoints in disposable Git repositories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SourceFreeze(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mqtt-source-freeze-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("scripts", "tests/integration", "docs/architecture"):
            (self.root / name).mkdir(parents=True)
        for name in ("acceptance.py", "candidate.py", "verify-candidate.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)
        for name, content in {
            ".gitignore": "_build/\n__pycache__/\n",
            "moon.mod": 'version = "0.7.1"\n',
            "client.mbt": "original source\n",
            "scripts/operator-version.txt": "0.1.0\n",
            "scripts/durable_recovery.py": "# synthetic operator\n",
            "tests/integration/requirements.txt": "",
            "docs/architecture/DURABLE-RECOVERY.md": "synthetic docs\n",
            "docs/OPERATOR-INSTALL.md": "synthetic docs\n",
            "LICENSE": "synthetic license\n",
            "tests/registry_roadmap.py": "pass\n",
            "tests/operator_consumer.py": "pass\n",
            "fake_moon.py": """#!PYTHON
import os, pathlib, sys, zipfile
out = pathlib.Path(sys.argv[sys.argv.index('--target-dir') + 1])
out.mkdir(parents=True)
with zipfile.ZipFile(out / 'module.zip', 'w') as z:
    z.write('client.mbt')
if os.environ.get('MUTATE_SOURCE') == '1':
    pathlib.Path('client.mbt').write_text('changed during package\\n')
""".replace("PYTHON", sys.executable),
        }.items():
            (self.root / name).write_text(content)
        (self.root / "fake_moon.py").chmod(0o755)
        self.git("init", "-q")
        self.git("config", "user.name", "Source Freeze Test")
        self.git("config", "user.email", "source-freeze@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic baseline")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def run_script(self, name, *args, mutate=False):
        return subprocess.run([sys.executable, "scripts/" + name, *args],
                              cwd=self.root, text=True, capture_output=True,
                              env={**os.environ, "MOON": str(self.root / "fake_moon.py"),
                                   "MUTATE_SOURCE": "1" if mutate else "0"})

    def test_package_refuses_mid_build_edit(self):
        result = self.run_script("candidate.py", mutate=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(list((self.root / "_build").rglob("candidate.json")))
        self.assertFalse((self.root / "_build/candidate/latest.json").exists())

    def test_verifier_refuses_different_commit_with_identical_files(self):
        built = self.run_script("candidate.py")
        self.assertEqual(built.returncode, 0, built.stderr)
        self.git("commit", "--allow-empty", "-qm", "different source identity")
        result = self.run_script("verify-candidate.py")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("commit", result.stderr)

    def test_acceptance_refuses_mid_run_edit(self):
        self.acceptance_case(mutate=True)

    def test_unchanged_acceptance_passes(self):
        self.acceptance_case(mutate=False)

    def acceptance_case(self, mutate):
        command = [sys.executable, "-c", "from pathlib import Path; Path('client.mbt').write_text('edited')" if mutate else "pass"]
        (self.root / "tests/scenarios.json").write_text(json.dumps({
            "levels": ["core", "integration", "release"],
            "scenarios": [{"id": "controlled", "level": "core", "command": command}]}))
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic scenario")
        result = self.run_script("acceptance.py", "core")
        report = json.loads(next((self.root / "_build").rglob("result.json")).read_text())
        self.assertEqual(result.returncode == 0, not mutate, result.stdout + result.stderr)
        self.assertEqual(report["successful"], not mutate)
        self.assertEqual(report["source_unchanged"], not mutate)

    def test_unchanged_package_and_consumers_pass(self):
        built = self.run_script("candidate.py")
        self.assertEqual(built.returncode, 0, built.stderr)
        result = self.run_script("verify-candidate.py")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_verifier_refuses_consumer_source_edit(self):
        (self.root / "tests/registry_roadmap.py").write_text(
            "from pathlib import Path; Path('client.mbt').write_text('consumer edit')\n")
        built = self.run_script("candidate.py")
        self.assertEqual(built.returncode, 0, built.stderr)
        result = self.run_script("verify-candidate.py")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("source changed during", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
