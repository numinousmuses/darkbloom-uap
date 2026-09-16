import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UAP = ROOT / "node_modules/.bin/uap"


@unittest.skipUnless(UAP.exists(), "Run npm ci for official UAP conformance checks")
class BundleTests(unittest.TestCase):
    def test_official_validator_and_idempotent_native_render(self):
        for role in ("implementer", "reviewer"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp:
                bundle = ROOT / "bundles" / role
                subprocess.run([str(UAP), "validate", str(bundle), "--strict"], check=True, capture_output=True)
                command = [str(UAP), "unpack", "--to", "claude-code", temp, "-b", str(bundle),
                           "--on-unsupported", "error", "--json"]
                report = json.loads(subprocess.check_output(command))
                self.assertFalse(report["lossy"])
                before = (Path(temp) / "CLAUDE.md").read_bytes()
                subprocess.run(command, check=True, capture_output=True)
                self.assertEqual(before, (Path(temp) / "CLAUDE.md").read_bytes())

    def test_missing_instruction_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "agent.uap.yaml").write_text((ROOT / "bundles/implementer/agent.uap.yaml").read_text())
            result = subprocess.run([str(UAP), "validate", temp, "--strict"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
