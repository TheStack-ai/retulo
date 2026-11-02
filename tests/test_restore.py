import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "restorer", Path(__file__).resolve().parents[1] / "scripts/restore_codex.py")
restorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restorer)
installer = restorer.installer


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        repo = self.root / "repo"
        (repo / ".venv/bin").mkdir(parents=True)
        (repo / ".venv/bin/python").write_text("synthetic interpreter")
        (repo / "adapters/codex").mkdir(parents=True)
        (repo / "adapters/codex/SKILL.md").write_text("synthetic default skill")
        (repo / "adapters/codex/AGENTS.md").write_bytes(
            installer.AGENTS_START + b"\nSynthetic guidance.\n" + installer.AGENTS_END + b"\n")
        self.kw = {"repo": repo, "codex_home": self.root / "codex",
                   "engine_config": self.root / "engine/config.json", "skills_home": self.root / "skills",
                   "workspace_home": self.root / "workspaces", "source_roots": [],
                   "snapshot_home": self.root / "snapshots"}
        codex = self.kw["codex_home"]
        codex.mkdir()
        (codex / "config.toml").write_text('model="preserved-model"\n[mcp_servers.other]\ncommand="other"\n')
        (codex / "config.toml").chmod(0o640)
        (codex / "AGENTS.md").write_text("Keep this instruction.\n")
        (codex / "AGENTS.md").chmod(0o600)
        (codex / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "synthetic-stop"}]}]}}))
        (codex / "hooks.json").chmod(0o644)
        self.original = {str(path): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                         for path in codex.iterdir()}
        self.installation = installer.install(**self.kw, apply=True)
        self.snapshot = Path(self.installation["snapshot"])
        self.opts = {key: self.kw[key] for key in
                     ("codex_home", "engine_config", "skills_home", "snapshot_home")}
        self.opts["snapshot"] = self.snapshot
        self.artifact = self.kw["workspace_home"] / "user-result.txt"
        self.artifact.write_text("preserved result")

    def tree(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in self.root.rglob("*") if p.is_file()}

    def manifest(self):
        return json.loads((self.snapshot / "manifest.json").read_text())

    def write_manifest(self, value):
        (self.snapshot / "manifest.json").write_text(json.dumps(value))

    def test_preview_is_readonly(self):
        before = self.tree()
        result = restorer.restore(**self.opts)
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["apply"])
        self.assertEqual(self.tree(), before)

    def test_restore_preserves_original_bytes_modes_results_and_snapshots(self):
        snapshots = {p: p.read_bytes() for p in self.snapshot.rglob("*") if p.is_file()}
        result = restorer.restore(**self.opts, apply=True)
        self.assertEqual(result["status"], "restored")
        for path, (raw, mode) in self.original.items():
            self.assertEqual(Path(path).read_bytes(), raw)
            self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), mode)
        self.assertFalse(self.kw["engine_config"].exists())
        self.assertFalse((self.kw["skills_home"] / "retulo/SKILL.md").exists())
        self.assertFalse((self.kw["snapshot_home"] / "managed-skill.json").exists())
        self.assertEqual(self.artifact.read_text(), "preserved result")
        self.assertEqual({p: p.read_bytes() for p in snapshots}, snapshots)
        self.assertEqual(restorer.restore(**self.opts, apply=True)["status"], "already_at_before_state")

    def test_later_user_edit_blocks_all_changes(self):
        (self.kw["codex_home"] / "AGENTS.md").write_text("later user edit")
        before = self.tree()
        with self.assertRaisesRegex(ValueError, "changed"):
            restorer.restore(**self.opts, apply=True)
        self.assertEqual(self.tree(), before)

    def test_tampered_backup_blocks_all_changes(self):
        (self.snapshot / "00.before").write_text("tampered backup")
        before = self.tree()
        with self.assertRaisesRegex(ValueError, "backup hash"):
            restorer.restore(**self.opts, apply=True)
        self.assertEqual(self.tree(), before)

    def test_forged_target_cannot_escape_explicit_target_set(self):
        unrelated = self.root / "unrelated.txt"
        unrelated.write_text("untouched")
        receipt = self.manifest()
        receipt["files"][0]["path"] = str(unrelated)
        receipt["files"][0]["after_sha256"] = installer.digest(unrelated.read_bytes())
        self.write_manifest(receipt)
        before = self.tree()
        with self.assertRaisesRegex(ValueError, "explicit target set"):
            restorer.restore(**self.opts, apply=True)
        self.assertEqual(self.tree(), before)

    def test_missing_duplicate_and_unfinished_manifests_are_rejected(self):
        original = self.manifest()
        variants = []
        missing = json.loads(json.dumps(original)); missing["files"].pop(); variants.append(missing)
        duplicate = json.loads(json.dumps(original)); duplicate["files"][1] = duplicate["files"][0]; variants.append(duplicate)
        unfinished = json.loads(json.dumps(original)); unfinished["status"] = "prepared"; variants.append(unfinished)
        for receipt in variants:
            with self.subTest(receipt=receipt.get("status")):
                self.write_manifest(receipt)
                before = self.tree()
                with self.assertRaises(ValueError):
                    restorer.restore(**self.opts, apply=True)
                self.assertEqual(self.tree(), before)

    def test_backup_symlink_and_outside_backup_are_rejected(self):
        path = self.snapshot / "00.before"
        raw = path.read_bytes()
        outside = self.root / "copied-backup"
        outside.write_bytes(raw)
        path.unlink(); path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            restorer.restore(**self.opts, apply=True)
        path.unlink(); path.write_bytes(raw)
        receipt = self.manifest(); receipt["files"][0]["backup"] = str(outside); self.write_manifest(receipt)
        with self.assertRaisesRegex(ValueError, "indexed file"):
            restorer.restore(**self.opts, apply=True)

    def test_target_and_parent_symlinks_are_rejected(self):
        path = self.kw["codex_home"] / "config.toml"
        outside = self.root / "copied-config"
        outside.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            restorer.restore(**self.opts, apply=True)
        path.unlink(); path.write_bytes(outside.read_bytes())
        real = self.root / "real-codex"
        self.kw["codex_home"].rename(real)
        self.kw["codex_home"].symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            restorer.restore(**self.opts, apply=True)

    def test_apply_failure_rolls_back_installed_bytes_and_modes(self):
        before = self.tree()
        atomic = installer.atomic
        calls = 0
        def fail_second(path, raw, mode=0o600, expected=installer._UNSET):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected restore failure")
            return atomic(path, raw, mode, expected=expected)
        with mock.patch.object(installer, "atomic", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "injected"):
                restorer.restore(**self.opts, apply=True)
        self.assertEqual(self.tree(), before)

    def test_failure_after_replacement_is_also_rolled_back(self):
        before = self.tree()
        atomic = installer.atomic
        calls = 0
        def write_then_fail(path, raw, mode=0o600, expected=installer._UNSET):
            nonlocal calls
            calls += 1
            atomic(path, raw, mode, expected=expected)
            if calls == 1:
                raise OSError("injected post-write failure")
        with mock.patch.object(installer, "atomic", side_effect=write_then_fail):
            with self.assertRaisesRegex(OSError, "post-write"):
                restorer.restore(**self.opts, apply=True)
        self.assertEqual(self.tree(), before)

    def test_nonregular_target_is_rejected_without_reading_it(self):
        path = self.kw["codex_home"] / "config.toml"
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(ValueError, "regular file"):
            restorer.restore(**self.opts, apply=True)

    def test_rollback_never_overwrites_a_late_user_edit(self):
        target = self.kw["codex_home"] / "config.toml"
        atomic = installer.atomic
        calls = 0
        def edit_then_fail(path, raw, mode=0o600, expected=installer._UNSET):
            nonlocal calls
            calls += 1
            if calls == 2:
                target.write_text("late user edit")
                raise OSError("injected late edit")
            return atomic(path, raw, mode, expected=expected)
        with mock.patch.object(installer, "atomic", side_effect=edit_then_fail):
            with self.assertRaisesRegex(OSError, "injected") as failure:
                restorer.restore(**self.opts, apply=True)
        self.assertEqual(target.read_text(), "late user edit")
        self.assertTrue(any("for review" in note for note in failure.exception.__notes__))
        self.assertTrue(self.kw["engine_config"].is_file())
        self.assertTrue((self.kw["skills_home"] / "retulo/SKILL.md").is_file())

    def test_updates_are_unwound_newest_to_oldest(self):
        (self.kw["repo"] / "adapters/codex/SKILL.md").write_text("new synthetic default")
        update = installer.install(**self.kw, apply=True)
        before = self.tree()
        with self.assertRaisesRegex(ValueError, "latest"):
            restorer.restore(**self.opts, apply=True)
        self.assertEqual(self.tree(), before)
        result = restorer.restore(**{**self.opts, "snapshot": Path(update["snapshot"])}, apply=True)
        self.assertEqual(result["status"], "restored")
        self.assertEqual(restorer.restore(**self.opts, apply=True)["status"], "restored")
        self.assertEqual((self.kw["codex_home"] / "config.toml").read_bytes(),
                         self.original[str(self.kw["codex_home"] / "config.toml")][0])

    def test_cli_requires_all_explicit_paths(self):
        result = subprocess.run([sys.executable, str(Path(restorer.__file__))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        for option in ("--snapshot", "--codex-home", "--engine-config", "--skills-home", "--snapshot-home"):
            self.assertIn(option, result.stderr)


if __name__ == "__main__":
    unittest.main()
