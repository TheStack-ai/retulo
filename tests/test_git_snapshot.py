import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from retulo.engine import Engine
from retulo.fs import EngineError
from retulo.git_snapshot import record_git_snapshot
from retulo.git_workspaces import GitWorkspaces
import retulo.git_snapshot as snapshot_module


def git(path, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "-C", str(path), *args], env=env, check=True,
                          capture_output=True).stdout.decode().strip()


class GitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "code.py").write_text("answer = 42\n")
        git(self.repo, "add", "code.py")
        git(self.repo, "commit", "-m", "Initial")
        self.engine = Engine(self.base / "workspaces", allowed_source_roots=[self.repo])
        self.task = self.engine.begin_task("start", "Snapshot", "Keep selected code", source_root=str(self.repo))
        self.task_id = self.task["task_id"]
        GitWorkspaces(self.engine).bind(self.task_id, "bind", 0, str(self.repo))

    def tearDown(self):
        self.tmp.cleanup()

    def assertError(self, code, function, *args, **kwargs):
        with self.assertRaises(EngineError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def snapshot(self, op="snapshot", revision=1, paths=None):
        return record_git_snapshot(self.engine, self.task_id, op, revision, paths or ["code.py"])

    def test_commits_selected_bytes_with_manifest_without_rebinding_source(self):
        (self.repo / "do-not-copy.env").write_text("not selected")
        result = self.snapshot()
        self.assertEqual(4, result["revision"])
        location = Path(result["workspace_path"]) / result["artifact"]["path"]
        with zipfile.ZipFile(location) as archive:
            self.assertEqual(["manifest.json", "files/code.py"], archive.namelist())
            self.assertEqual(b"answer = 42\n", archive.read("files/code.py"))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(git(self.repo, "rev-parse", "HEAD"), manifest["git"]["head"])
            self.assertEqual(hashlib.sha256(b"answer = 42\n").hexdigest(), manifest["files"][0]["sha256"])
            self.assertIn("not tests", manifest["claim"])
        current = self.engine._load(self.engine._path(self.task_id), self.task_id)
        self.assertEqual(str(self.repo), current["source_root"])
        self.assertEqual(1, current["schema_version"])
        self.assertFalse(result["artifact"]["external"])
        self.assertTrue(self.engine.verify(self.task_id)["ok"])

    def test_same_operation_replays_and_different_paths_conflict(self):
        first = self.snapshot()
        second = self.snapshot()
        self.assertEqual(first["artifact"], second["artifact"])
        self.assertTrue(second["replayed"])
        self.assertError("idempotency_conflict", self.snapshot, paths=["different.py"])

    def test_replay_survives_removed_worktree_and_original_core_source_preserved(self):
        # Use a real secondary worktree rather than deleting a primary repository.
        worktree = self.base / "feature"
        git(self.repo, "worktree", "add", "-b", "feature", str(worktree))
        engine = Engine(self.base / "other-workspaces", allowed_source_roots=[self.repo, worktree])
        task = engine.begin_task("secondary", "Secondary", "Snapshot", source_root=str(self.repo))
        GitWorkspaces(engine).bind(task["task_id"], "bind", 0, str(worktree))
        first = record_git_snapshot(engine, task["task_id"], "snap", 1, ["code.py"])
        git(self.repo, "worktree", "remove", str(worktree))
        replay = record_git_snapshot(engine, task["task_id"], "snap", 1, ["code.py"])
        self.assertEqual(first["artifact"], replay["artifact"])
        self.assertTrue(engine.verify(task["task_id"])["ok"])

    def test_interrupted_commit_retries_same_archive_and_reservation(self):
        with patch.object(self.engine, "commit_artifact", side_effect=OSError("power loss")):
            with self.assertRaises(OSError):
                self.snapshot()
        state = self.engine._load(self.engine._path(self.task_id))
        self.assertEqual(3, state["revision"])
        self.assertEqual(1, len(state["reservations"]))
        result = self.snapshot(revision=3)
        self.assertEqual(4, result["revision"])
        self.assertEqual(1, len(self.engine._load(self.engine._path(self.task_id))["artifacts"]))

    def test_changed_bytes_after_interruption_refuse_commit_and_keep_reservation(self):
        with patch.object(self.engine, "commit_artifact", side_effect=OSError("power loss")):
            with self.assertRaises(OSError):
                self.snapshot()
        (self.repo / "code.py").write_text("changed\n")
        self.assertError("snapshot_changed", self.snapshot, revision=3)
        state = self.engine._load(self.engine._path(self.task_id))
        self.assertEqual([], state["artifacts"])
        reservation = next(iter(state["reservations"].values()))
        self.assertEqual("reserved", reservation["status"])
        self.assertTrue((self.engine._path(self.task_id) / reservation["write_path"]).exists())

    def test_changed_head_after_interruption_refuse_commit(self):
        with patch.object(self.engine, "commit_artifact", side_effect=OSError("power loss")):
            with self.assertRaises(OSError):
                self.snapshot()
        git(self.repo, "commit", "--allow-empty", "-m", "Another commit")
        self.assertError("snapshot_changed", self.snapshot, revision=3)

    def test_path_traversal_git_metadata_absolute_and_duplicates_refused(self):
        for path in ["../secret", "/tmp/secret", ".git/config", "a/.GiT/config", "a/../code.py", "a\\..\\b", "a//b"]:
            with self.subTest(path=path):
                self.assertError("unsafe_path", record_git_snapshot, self.engine, self.task_id, "bad", 1, [path])
        self.assertError("invalid_input", record_git_snapshot, self.engine, self.task_id, "bad", 1, ["code.py", "code.py"])
        self.assertEqual(1, self.engine._load(self.engine._path(self.task_id))["revision"])

    def test_symlink_directory_symlink_and_directory_refused(self):
        (self.repo / "linked").symlink_to(self.repo / "code.py")
        (self.repo / "directory").symlink_to(self.repo, target_is_directory=True)
        (self.repo / "actual-directory").mkdir()
        for selected in ["linked", "directory/code.py", "actual-directory"]:
            with self.subTest(path=selected):
                with self.assertRaises(EngineError):
                    self.snapshot(paths=[selected])
        self.assertEqual(1, self.engine._load(self.engine._path(self.task_id))["revision"])

    def test_limits_reject_before_reserving_artifact(self):
        with patch.object(snapshot_module, "MAX_FILE_BYTES", 3):
            self.assertError("snapshot_limit", self.snapshot)
        self.assertEqual({}, self.engine._load(self.engine._path(self.task_id))["reservations"])

    def test_empty_selected_file_and_unicode_newline_name_roundtrip(self):
        selected = "한글 \n파일.txt"
        (self.repo / selected).write_bytes(b"")
        result = self.snapshot(paths=[selected])
        with zipfile.ZipFile(Path(result["workspace_path"]) / result["artifact"]["path"]) as archive:
            self.assertEqual(b"", archive.read("files/" + selected))

    def test_stale_revision_and_paused_task_refused(self):
        self.assertError("revision_conflict", self.snapshot, revision=0)
        self.engine.checkpoint(self.task_id, "pause", 1, {"status": "paused"})
        self.assertError("task_not_active", self.snapshot, revision=2)


if __name__ == "__main__":
    unittest.main()
