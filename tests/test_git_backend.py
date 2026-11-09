import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from retulo.git_backend import GitBackend
from retulo.fs import EngineError


class GitBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-b", "main")
        self.git(self.repo, "config", "user.email", "fixture@example.invalid")
        self.git(self.repo, "config", "user.name", "Fixture")
        (self.repo / "tracked.txt").write_text("base\n")
        self.git(self.repo, "add", "tracked.txt")
        self.git(self.repo, "commit", "-m", "initial")
        self.backend = GitBackend()
        self.head = self.git(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, path, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.run(["git", "-C", str(path), *args], env=env,
                              capture_output=True, check=True).stdout.decode()

    def error(self, code, method, *args):
        with self.assertRaises(EngineError) as caught:
            method(*args)
        self.assertEqual(caught.exception.code, code)

    def create(self, name="feature"):
        path = self.base / name
        return path, self.backend.create_worktree(self.repo, path, name, self.head)

    def test_status_is_local_and_does_not_refresh_index(self):
        index = self.repo / ".git/index"
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        state = self.backend.status(self.repo)
        self.assertEqual(state["head"], self.head)
        self.assertEqual(state["branch"], "main")
        self.assertFalse(state["dirty"])
        self.assertEqual(before, (index.read_bytes(), index.stat().st_mtime_ns))

    def test_hidden_untracked_ignored_and_newline_rename(self):
        self.git(self.repo, "config", "status.showUntrackedFiles", "no")
        (self.repo / "new\n한글.txt").write_text("new")
        (self.repo / ".gitignore").write_text("local.env\n")
        (self.repo / "local.env").write_text("synthetic")
        self.git(self.repo, "mv", "tracked.txt", "renamed\n.txt")
        state = self.backend.status(self.repo)
        self.assertEqual(state["untracked"], 2)
        self.assertEqual(state["ignored"], 1)
        renamed = next(x for x in state["files"] if x.get("original_path"))
        self.assertEqual(renamed["path"], "renamed\n.txt")
        self.assertEqual(renamed["original_path"], "tracked.txt")

    def test_inherited_git_environment_is_ignored(self):
        with patch.dict(os.environ, {"GIT_DIR": "/not-real", "GIT_WORK_TREE": "/not-real", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "bad-command"}):
            self.assertEqual(self.backend.status(self.repo)["head"], self.head)

    def test_filters_block_before_status_executes_them(self):
        marker = self.base / "filter-ran"
        self.git(self.repo, "config", "filter.evil.clean", "touch " + str(marker))
        (self.repo / ".gitattributes").write_text("* filter=evil\n")
        (self.repo / "tracked.txt").write_text("modified")
        self.error("git_filter_unsupported", self.backend.status, self.repo)
        self.assertFalse(marker.exists())

    def test_conditional_gitdir_filter_cannot_execute_during_create(self):
        marker = self.base / "conditional-filter-ran"
        extra = self.base / "conditional-config"
        extra.write_text('[filter "evil"]\n    smudge = touch "' + str(marker) + '"; cat\n')
        (self.repo / ".gitattributes").write_text("tracked.txt filter=evil\n")
        self.git(self.repo, "add", ".gitattributes")
        self.git(self.repo, "commit", "-m", "attribute fixture")
        head = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.git(self.repo, "config", "includeIf.gitdir:" + str(self.repo / ".git/worktrees") + "/.path", str(extra))
        target = self.base / "conditional"
        self.error("git_include_unsupported", self.backend.preflight_create,
                   self.repo, target, "conditional", head)
        self.error("git_include_unsupported", self.backend.create_worktree,
                   self.repo, target, "conditional", head)
        self.assertFalse(marker.exists())
        self.assertFalse(target.exists())
        self.assertEqual(len(self.git(self.repo, "worktree", "list", "--porcelain").split("worktree ")) - 1, 1)

    def test_onbranch_and_worktree_local_includes_fail_closed(self):
        extra = self.base / "included-config"
        extra.write_text('[filter "evil"]\n    smudge = false\n')
        self.git(self.repo, "config", "includeIf.onbranch:future.path", str(extra))
        self.error("git_include_unsupported", self.backend.status, self.repo)
        self.git(self.repo, "config", "--unset", "includeIf.onbranch:future.path")
        self.git(self.repo, "config", "extensions.worktreeConfig", "true")
        self.git(self.repo, "config", "--worktree", "include.path", str(extra))
        self.error("git_include_unsupported", self.backend.status, self.repo)

    def test_fsmonitor_and_hooks_never_run(self):
        marker = self.base / "hook-ran"
        script = self.base / "hook"
        script.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\n")
        script.chmod(0o755)
        self.git(self.repo, "config", "core.fsmonitor", str(script))
        hooks = self.repo / ".git/hooks"
        (hooks / "post-checkout").write_bytes(script.read_bytes())
        (hooks / "post-checkout").chmod(0o755)
        self.backend.status(self.repo)
        self.create()
        self.assertFalse(marker.exists())

    def test_newline_worktree_path(self):
        path = self.base / "work\n"
        state = self.backend.create_worktree(self.repo, path, "newline", self.head)
        self.assertEqual(state["path"], str(path))
        self.assertIn(str(path), [r["path"] for r in self.backend.list_worktrees(self.repo)])

    def test_create_rejects_overlap_and_existing_path(self):
        self.error("git_path_overlap", self.backend.create_worktree, self.repo, self.repo / "child", "child", self.head)
        self.error("git_path_exists", self.backend.create_worktree, self.repo, self.repo, "same", self.head)
        self.error("git_invalid_oid", self.backend.create_worktree, self.repo, self.base / "no", "no", "HEAD")

    def test_preflight_does_not_write_and_rejects_existing_branch(self):
        path = self.base / "preflight"
        plan = self.backend.preflight_create(self.repo, path, "preflight", self.head)
        self.assertEqual(plan["path"], str(path))
        self.assertFalse(path.exists())
        self.error("git_branch_exists", self.backend.preflight_create, self.repo, path, "main", self.head)
        self.assertFalse(path.exists())

    def test_locked_dirty_ignored_and_hidden_flags_prevent_remove(self):
        path, _ = self.create()
        self.git(self.repo, "worktree", "lock", str(path))
        self.assertTrue(self.backend.status(path)["locked"])
        self.error("git_unsafe_state", self.backend.remove_worktree, self.repo, path)
        self.git(self.repo, "worktree", "unlock", str(path))
        self.git(path, "update-index", "--assume-unchanged", "tracked.txt")
        (path / "tracked.txt").write_text("hidden edit")
        self.assertTrue(self.backend.status(path)["unsafe_index_flags"])
        self.error("git_unsafe_state", self.backend.remove_worktree, self.repo, path)
        self.git(path, "update-index", "--no-assume-unchanged", "tracked.txt")
        self.error("git_unsafe_state", self.backend.remove_worktree, self.repo, path)
        self.git(path, "checkout", "--", "tracked.txt")
        (path / ".gitignore").write_text("secret\n")
        self.git(path, "add", ".gitignore")
        self.git(path, "commit", "-m", "ignore")
        (path / "secret").write_text("synthetic")
        self.error("git_unsafe_state", self.backend.remove_worktree, self.repo, path)

    def test_fast_forward_and_retained_branch(self):
        path, _ = self.create()
        (path / "tracked.txt").write_text("updated\n")
        self.git(path, "commit", "-am", "change")
        source = self.backend.status(path)
        result = self.backend.integrate(path, self.repo, source["head"], self.head)
        self.assertEqual(result["head"], source["head"])
        self.assertEqual((self.repo / "tracked.txt").read_text(), "updated\n")
        removed = self.backend.remove_worktree(self.repo, path, source["head"])
        self.assertTrue(removed["branch_retained"])
        self.assertEqual(self.git(self.repo, "rev-parse", "feature").strip(), source["head"])

    def test_stale_and_diverged_integration_rejected(self):
        path, _ = self.create()
        (path / "feature").write_text("feature")
        self.git(path, "add", "feature")
        self.git(path, "commit", "-m", "feature")
        source_head = self.backend.status(path)["head"]
        self.error("git_stale_plan", self.backend.integrate, path, self.repo, self.head, self.head)
        (self.repo / "main").write_text("main")
        self.git(self.repo, "add", "main")
        self.git(self.repo, "commit", "-m", "main")
        target_head = self.backend.status(self.repo)["head"]
        self.error("git_non_fast_forward", self.backend.integrate, path, self.repo, source_head, target_head)
        self.assertEqual(self.backend.status(self.repo)["head"], target_head)

    def test_primary_remove_missing_git_and_timeout(self):
        self.error("git_primary_worktree", self.backend.remove_worktree, self.repo, self.repo)
        self.error("git_path_missing", self.backend.status, self.base / "missing")
        with patch("retulo.git_backend.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 1)):
            self.error("git_timeout", self.backend.status, self.repo)
        self.backend.executable = None
        self.error("git_unavailable", self.backend.status, self.repo)

    def test_skip_worktree_and_submodule_require_external_workflow(self):
        self.git(self.repo, "update-index", "--skip-worktree", "tracked.txt")
        self.assertTrue(self.backend.status(self.repo)["unsafe_index_flags"])
        self.git(self.repo, "update-index", "--no-skip-worktree", "tracked.txt")
        self.git(self.repo, "update-index", "--add", "--cacheinfo", "160000," + self.head + ",nested")
        self.git(self.repo, "commit", "-m", "gitlink fixture")
        state = self.backend.status(self.repo)
        self.assertEqual(state["submodules"], ["nested"])
        self.error("git_unsafe_state", self.backend.create_worktree, self.repo,
                   self.base / "submodule-task", "submodule-task", state["head"])

    def test_conflict_and_operation_marker_are_reported(self):
        path, _ = self.create()
        (path / "tracked.txt").write_text("feature\n")
        self.git(path, "commit", "-am", "feature")
        (self.repo / "tracked.txt").write_text("main\n")
        self.git(self.repo, "commit", "-am", "main")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git(self.repo, "merge", "feature")
        state = self.backend.status(self.repo)
        self.assertEqual(state["conflicts"], 1)
        self.assertIn("MERGE_HEAD", state["operation_in_progress"])
        self.assertEqual(next(x for x in state["files"] if x["kind"] == "conflict")["path"], "tracked.txt")

    def test_detached_and_unborn_are_distinct(self):
        path = self.base / "detached"
        self.git(self.repo, "worktree", "add", "--detach", str(path), self.head)
        self.assertTrue(self.backend.status(path)["detached"])
        unborn = self.base / "unborn"
        unborn.mkdir()
        self.git(unborn, "init", "-b", "main")
        state = self.backend.status(unborn)
        self.assertIsNone(state["head"])
        self.assertEqual(state["branch"], "main")


if __name__ == "__main__":
    unittest.main()
