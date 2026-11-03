"""Bootstrap must stop before invoking an installer on invalid release bytes."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"
WHEEL = "retulo-0.5.0rc1-py3-none-any.whl"


class BootstrapTests(unittest.TestCase):
    def test_preview_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(["sh", str(SCRIPT), "--dry-run"], cwd=tmp,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("persistent", result.stdout)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def run_assets(self, checksums):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / WHEEL).write_bytes(b"synthetic release wheel")
            (root / "SHA256SUMS").write_text(checksums)
            uv = root / "uv"
            marker = root / "executed"
            uv.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
            uv.chmod(0o700)
            result = subprocess.run(["sh", str(SCRIPT), "--asset-dir", tmp,
                                     "--uv", str(uv)], capture_output=True, text=True,
                                    env={**os.environ, "TMPDIR": tmp})
            return result, marker.exists(), sorted(p.name for p in root.iterdir())

    def test_corrupt_wheel_never_runs_uv(self):
        result, ran, files = self.run_assets(f"{'0' * 64}  {WHEEL}\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse(ran)
        self.assertFalse(any(name.startswith("retulo-install.") for name in files))

    def test_duplicate_checksum_never_runs_uv(self):
        digest = hashlib.sha256(b"synthetic release wheel").hexdigest()
        result, ran, _ = self.run_assets(f"{digest}  {WHEEL}\n" * 2)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(ran)

    def test_valid_wheel_propagates_install_failure(self):
        digest = hashlib.sha256(b"synthetic release wheel").hexdigest()
        result, ran, _ = self.run_assets(f"{digest}  {WHEEL}\n")
        self.assertEqual(result.returncode, 99)
        self.assertTrue(ran)


if __name__ == "__main__":
    unittest.main()
