"""Bounded local Git operations; no shell, hooks, network or model invocation.

The caller owns path authorization and operation receipts. This module independently
checks Git's current state immediately before writes. Cooperative locks cannot stop
an unrelated process from editing a repository between checks; callers must preserve
unknown outcomes and reconcile them instead of assuming a transaction or rollback.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess

from .fs import EngineError


class GitBackend:
    def __init__(self, timeout=10):
        self.timeout = timeout
        self.executable = shutil.which("git")

    def _run(self, path, *arguments, allowed=(0,)):
        if not self.executable:
            raise EngineError("git_unavailable", "Git is not installed")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_PAGER="cat",
                   LC_ALL="C", GIT_PROTOCOL_FROM_USER="0", GIT_NO_LAZY_FETCH="1",
                   GIT_NO_REPLACE_OBJECTS="1")
        options = ["core.hooksPath=" + os.devnull, "core.fsmonitor=false",
                   "core.untrackedCache=false", "core.preloadIndex=false", "core.ignoreStat=false",
                   "submodule.recurse=false", "protocol.allow=never", "credential.helper=",
                   "core.pager=cat", "gc.auto=0", "maintenance.auto=false", "merge.autostash=false",
                   "rebase.autostash=false", "rerere.enabled=false"]
        command = [self.executable, *[part for option in options for part in ("-c", option)],
                   "-C", os.fspath(path), *map(str, arguments)]
        try:
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise EngineError("git_timeout", "Git timed out; a mutating command may need reconciliation") from exc
        except OSError as exc:
            raise EngineError("git_unavailable", "Git could not be started") from exc
        if result.returncode not in allowed:
            # Do not echo configuration, paths, remote URLs or arbitrary hook output.
            raise EngineError("git_failed", "Local Git command failed", {
                "command": str(arguments[0]) if arguments else "", "exit_code": result.returncode})
        if len(result.stdout) > 8 * 1024 * 1024:
            raise EngineError("git_output_limit", "Git output exceeded the supported local limit")
        return result

    @staticmethod
    def _line(data):
        # Git appends one newline; a legitimate path may itself end in a newline.
        return os.fsdecode(data[:-1] if data.endswith(b"\n") else data)

    def _reject_filters(self, path):
        # Includes can become active only after switching branch or constructing a
        # new linked-worktree gitdir. Inspect the raw directives, not merely the
        # currently effective filter values, before any checkout or status command.
        # User/system config is already disabled by _run; this query also covers
        # source worktree config when extensions.worktreeConfig is enabled.
        includes = self._run(path, "config", "--no-includes", "--get-regexp",
                             r"^include(if)?\.", allowed=(0, 1))
        if includes.returncode == 0 and includes.stdout:
            raise EngineError("git_include_unsupported", "Repositories with Git config includes require an explicit external workflow")
        result = self._run(path, "config", "--get-regexp", r"^filter\..*\.(clean|smudge|process)$", allowed=(0, 1))
        if result.returncode == 0 and result.stdout:
            raise EngineError("git_filter_unsupported", "Repositories with executable Git filters require an explicit external workflow")

    def identity(self, path):
        path = Path(path).resolve()
        if not path.is_dir():
            raise EngineError("git_path_missing", "Git working directory does not exist")
        root = Path(self._line(self._run(path, "rev-parse", "--path-format=absolute", "--show-toplevel").stdout))
        common = Path(self._line(self._run(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout))
        git_dir = Path(self._line(self._run(path, "rev-parse", "--absolute-git-dir").stdout))
        return {"path": str(root.resolve()), "common_dir": str(common.resolve()), "git_dir": str(git_dir.resolve())}

    def list_worktrees(self, path):
        data = self._run(path, "worktree", "list", "--porcelain", "-z").stdout
        rows, current = [], {}
        for field in data.split(b"\0"):
            if not field:
                if current:
                    rows.append(current)
                    current = {}
                continue
            name, _, value = field.partition(b" ")
            key = name.decode("ascii")
            if key == "worktree":
                current["path"] = os.fsdecode(value)
            elif key == "HEAD":
                current["head"] = value.decode("ascii")
            elif key == "branch":
                current["branch"] = os.fsdecode(value).removeprefix("refs/heads/")
            elif key in ("locked", "prunable"):
                current[key] = True
                current[key + "_reason"] = os.fsdecode(value)
            else:
                current[key] = True if not value else os.fsdecode(value)
        if current:
            rows.append(current)
        return rows

    def status(self, path):
        identity = self.identity(path)
        root = identity["path"]
        self._reject_filters(root)
        head_result = self._run(root, "rev-parse", "--verify", "HEAD", allowed=(0, 128))
        head = self._line(head_result.stdout) if head_result.returncode == 0 else None
        branch_result = self._run(root, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1))
        branch = self._line(branch_result.stdout) if branch_result.returncode == 0 else None
        raw = self._run(root, "status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored=matching", "--ignore-submodules=all").stdout
        records = raw.split(b"\0")
        files, counts, index = [], {"staged": 0, "modified": 0, "untracked": 0, "ignored": 0, "conflicts": 0}, 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            kind = record[:1]
            if kind in (b"?", b"!"):
                label = "untracked" if kind == b"?" else "ignored"
                counts[label] += 1
                files.append({"path": os.fsdecode(record[2:]), "kind": label})
                continue
            if kind not in (b"1", b"2", b"u"):
                raise EngineError("git_output_invalid", "Unknown Git status record")
            parts = record.split(b" ", 8 if kind == b"1" else 9 if kind == b"2" else 10)
            expected = 9 if kind == b"1" else 10 if kind == b"2" else 11
            if len(parts) != expected:
                raise EngineError("git_output_invalid", "Malformed Git status record")
            xy = parts[1].decode("ascii")
            item = {"path": os.fsdecode(parts[-1]), "kind": "conflict" if kind == b"u" else "tracked", "xy": xy}
            if kind == b"2":
                if index >= len(records) or not records[index]:
                    raise EngineError("git_output_invalid", "Missing rename source")
                item["original_path"] = os.fsdecode(records[index])
                index += 1
            counts["staged"] += xy[0] != "."
            counts["modified"] += xy[1] != "."
            counts["conflicts"] += kind == b"u"
            files.append(item)
        flags = []
        for record in self._run(root, "ls-files", "-v", "-z").stdout.split(b"\0"):
            if record and (record[:1].islower() or record[:1].upper() == b"S"):
                flags.append({"path": os.fsdecode(record[2:]), "flag": record[:1].decode("ascii")})
        submodules = []
        for record in self._run(root, "ls-files", "--stage", "-z").stdout.split(b"\0"):
            if record.startswith(b"160000 "):
                submodules.append(os.fsdecode(record.partition(b"\t")[2]))
        row = next((row for row in self.list_worktrees(root) if Path(row["path"]).resolve() == Path(root)), {})
        operations = [name for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG", "index.lock")
                      if (Path(identity["git_dir"]) / name).exists()]
        return {**identity, **counts, "head": head, "branch": branch, "detached": branch is None,
                "dirty": any(counts[key] for key in ("staged", "modified", "untracked", "conflicts")),
                "locked": row.get("locked", False), "prunable": row.get("prunable", False),
                "unsafe_index_flags": flags, "operation_in_progress": operations,
                "submodules": submodules, "submodule_content": "not_inspected" if submodules else "not_applicable",
                "files": files, "state": "observed",
                "observed_at": datetime.now(timezone.utc).isoformat()}

    def resolve_commit(self, path, value):
        if not isinstance(value, str) or not value or value.startswith("-") or "\x00" in value:
            raise EngineError("git_invalid_ref", "Commit reference is invalid")
        return self._line(self._run(path, "rev-parse", "--verify", "--end-of-options", value + "^{commit}").stdout)

    @staticmethod
    def _oid(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            raise EngineError("git_invalid_oid", "A full pinned commit ID is required")
        return value

    @staticmethod
    def _clean(state):
        if state["dirty"] or state["ignored"] or state["locked"] or state["unsafe_index_flags"] or state["operation_in_progress"] or state["submodules"]:
            raise EngineError("git_unsafe_state", "Git operation requires a clean, unlocked worktree without ignored files, hidden index flags, submodules or an operation in progress")
        if not state["head"]:
            raise EngineError("git_unborn", "An initial commit is required")

    def preflight_create(self, repo, path, branch, base_head):
        state = self.status(repo)
        self._oid(base_head)
        if self.resolve_commit(repo, base_head) != base_head:
            raise EngineError("git_stale_plan", "Base commit changed")
        if any(item.startswith(b"160000 ") for item in self._run(repo, "ls-tree", "-r", "-z", base_head).stdout.split(b"\0")):
            raise EngineError("git_unsafe_state", "Submodule checkouts require an explicit external workflow")
        if not isinstance(branch, str) or not branch or branch.startswith("-"):
            raise EngineError("git_invalid_ref", "Branch name is invalid")
        self._run(repo, "check-ref-format", "refs/heads/" + branch)
        exists = self._run(repo, "show-ref", "--verify", "--quiet", "refs/heads/" + branch, allowed=(0, 1))
        if exists.returncode == 0:
            raise EngineError("git_branch_exists", "New branch already exists")
        target = Path(path).absolute()
        if target.exists() or target.is_symlink():
            raise EngineError("git_path_exists", "New worktree destination must not exist")
        for row in self.list_worktrees(repo):
            existing = Path(row["path"]).resolve()
            candidate = target.resolve()
            if candidate == existing or candidate in existing.parents or existing in candidate.parents:
                raise EngineError("git_path_overlap", "Worktree paths must not overlap")
        common = Path(state["common_dir"])
        if target.resolve() == common or common in target.resolve().parents or target.resolve() in common.parents:
            raise EngineError("git_path_overlap", "Worktree destination overlaps Git metadata")
        if state["operation_in_progress"] or state["submodules"]:
            raise EngineError("git_unsafe_state", "An operation or submodule requires an explicit external workflow")
        return {"path": str(target), "base_head": base_head, "branch": branch, "common_dir": state["common_dir"]}

    def create_worktree(self, repo, path, branch, base_head):
        plan = self.preflight_create(repo, path, branch, base_head)
        target = Path(plan["path"])
        self._run(repo, "worktree", "add", "-b", branch, "--", str(target), base_head)
        created = self.status(target)
        if created["head"] != base_head or created["branch"] != branch:
            raise EngineError("git_stale_plan", "Created worktree changed; inspect before retrying")
        return created

    def is_ancestor(self, repo, ancestor, descendant):
        self._oid(ancestor)
        self._oid(descendant)
        return self._run(repo, "merge-base", "--is-ancestor", ancestor, descendant, allowed=(0, 1)).returncode == 0

    def integrate(self, source_path, target_path, expected_source_head, expected_target_head):
        source, target = self.status(source_path), self.status(target_path)
        self._clean(source)
        self._clean(target)
        if source["common_dir"] != target["common_dir"] or source["path"] == target["path"]:
            raise EngineError("git_identity_mismatch", "Integration requires distinct worktrees of the same repository")
        if source["head"] != self._oid(expected_source_head) or target["head"] != self._oid(expected_target_head):
            raise EngineError("git_stale_plan", "Commit changed after planning")
        if not target["branch"]:
            raise EngineError("git_detached_target", "Integration target must be a branch")
        if not self.is_ancestor(target_path, target["head"], source["head"]):
            raise EngineError("git_non_fast_forward", "Only fast-forward integration is supported")
        self._run(target_path, "merge", "--ff-only", "--no-edit", "--no-verify", "--no-stat", source["head"])
        result = self.status(target_path)
        if result["head"] != source["head"]:
            raise EngineError("git_stale_plan", "Target changed during integration; reconcile the outcome")
        return result

    def remove_worktree(self, repo, path, expected_head=None):
        state, owner = self.status(path), self.identity(repo)
        self._clean(state)
        if state["common_dir"] != owner["common_dir"]:
            raise EngineError("git_identity_mismatch", "Worktree belongs to a different repository")
        if state["git_dir"] == state["common_dir"]:
            raise EngineError("git_primary_worktree", "Primary worktree cannot be removed")
        if expected_head is not None and state["head"] != self._oid(expected_head):
            raise EngineError("git_stale_plan", "Worktree commit changed after planning")
        self._run(repo, "worktree", "remove", "--", state["path"])
        if Path(state["path"]).exists():
            raise EngineError("git_remove_incomplete", "Worktree removal did not finish")
        return {"removed": True, "path": state["path"], "head": state["head"], "branch_retained": True}
