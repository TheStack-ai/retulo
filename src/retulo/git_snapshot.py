"""Explicit, bounded Git file snapshots stored as ordinary immutable artifacts.

This records observed bytes and Git identity, never test success or approval. The
intent/reservation/commit are recoverable steps, not an atomic Git transaction.
"""
from __future__ import annotations

import copy
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

from .fs import EngineError, atomic_bytes, canonical_json, process_lock, safe_path

MAX_FILES = 128
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def _paths(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_FILES:
        raise EngineError("invalid_input", "paths must select 1 to 128 individual files")
    result = []
    for value in values:
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096:
            raise EngineError("invalid_input", "Each selected path must be a bounded relative string")
        path = PurePosixPath(value)
        if (path.is_absolute() or "\\" in value or ":" in value or "\x00" in value
                or any(part in ("", ".", "..") for part in value.split("/"))
                or any(part.casefold() == ".git" for part in path.parts)):
            raise EngineError("unsafe_path", "Snapshot paths must be relative files outside Git metadata")
        result.append(value)
    if len(set(result)) != len(result) or len({p.casefold() for p in result}) != len(result):
        raise EngineError("invalid_input", "Snapshot paths must be unique, including case-insensitive names")
    if sum(len(p.encode()) for p in result) > 65536:
        raise EngineError("snapshot_limit", "Selected path names exceed the snapshot limit")
    return sorted(result)


def _read_file(root, relative):
    """Traverse every component using no-follow directory descriptors."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory = flags | getattr(os, "O_DIRECTORY", 0)
    descriptors = []
    try:
        fd = os.open(root, directory)
        descriptors.append(fd)
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            fd = os.open(part, directory, dir_fd=fd)
            descriptors.append(fd)
        leaf = os.open(parts[-1], flags | getattr(os, "O_NONBLOCK", 0), dir_fd=fd)
        descriptors.append(leaf)
        before = os.fstat(leaf)
        if not stat.S_ISREG(before.st_mode):
            raise EngineError("unsafe_path", "Only individual regular files can be snapshotted")
        if before.st_size > MAX_FILE_BYTES:
            raise EngineError("snapshot_limit", "A selected file exceeds the per-file snapshot limit")
        with os.fdopen(os.dup(leaf), "rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(leaf)
        if len(data) > MAX_FILE_BYTES:
            raise EngineError("snapshot_limit", "A selected file exceeds the per-file snapshot limit")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
            raise EngineError("snapshot_changed", "A selected file changed while being read")
        return data
    except OSError as exc:
        raise EngineError("snapshot_unreadable", "Selected file cannot be read safely",
                          {"path": relative, "errno": exc.errno}) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _files(root, paths):
    contents, total = {}, 0
    for relative in paths:
        data = _read_file(root, relative)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise EngineError("snapshot_limit", "Selected files exceed the total snapshot limit")
        contents[relative] = data
    metadata = [{"path": p, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for p, data in contents.items()]
    return contents, metadata


def _observed(engine, task_id):
    # Imported lazily so the normal non-Git engine does not depend on this path.
    from .git_workspaces import GitWorkspaces
    observed = GitWorkspaces(engine).status(task_id)
    binding = observed.get("binding")
    state = observed.get("observation")
    if observed.get("status") != "observed" or not isinstance(binding, dict) or not isinstance(state, dict):
        raise EngineError("git_unavailable", "A verified live Git workspace binding is required")
    if observed.get("pending") or observed.get("recovery_needed"):
        raise EngineError("git_recovery_required", "Resolve the pending Git operation before snapshotting")
    path = state.get("path")
    if not path or not state.get("head") or state.get("known") is False:
        raise EngineError("git_unavailable", "Git identity and HEAD could not be verified")
    return {"git_revision": observed.get("git_revision"), "binding": binding,
            "path": path, "common_dir": state.get("common_dir"), "git_dir": state.get("git_dir"),
            "head": state["head"], "branch": state.get("branch")}


def _zip(manifest, contents):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        entries = [("manifest.json", (canonical_json(manifest) + "\n").encode())]
        entries.extend(("files/" + path, data) for path, data in contents.items())
        for name, data in entries:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, data)
    return buffer.getvalue()


def record_git_snapshot(engine, task_id, operation_id, expected_revision, paths):
    """Commit selected bytes to a ZIP artifact; consumes three core revisions.

    Retry with the same operation ID and paths. An interrupted operation refuses
    changed source bytes/identity; its staging reservation is preserved. Completed
    retries return the original receipt, even when the worktree no longer exists.
    """
    from .engine import identifier, now
    operation_id = identifier(operation_id, "operation_id")
    selected = _paths(paths)
    payload = {"method": "git_record_snapshot", "paths": selected}
    prefix = "git-snapshot:" + hashlib.sha256(operation_id.encode()).hexdigest()
    root = engine._path(task_id)
    lock = safe_path(root / ".workspace" / "git-snapshot.lock", [root])
    with process_lock(lock):
        def intent_change(_root, state):
            if state["status"] != "active":
                raise EngineError("task_not_active", "Reactivate the task explicitly before snapshotting")
            observed = _observed(engine, task_id)
            _, metadata = _files(observed["path"], selected)
            if _observed(engine, task_id) != observed:
                raise EngineError("snapshot_changed", "Git binding or HEAD changed while observing files")
            return {"snapshot": {"schema_version": 1, "task_id": task_id,
                    "observed_at": now(), "git": observed, "files": metadata,
                    "claim": "observed_file_bytes; not tests, approval, or atomic Git snapshot"}}, []

        intent = engine._mutate(task_id, prefix + ":intent", expected_revision, payload, intent_change)
        manifest = intent["snapshot"]
        reserve = engine.reserve_output(task_id, prefix + ":reserve", max(expected_revision, intent["revision"]),
                                        "git-snapshot.zip", role="git_snapshot", format="zip")
        current = engine._load(root, task_id)
        prior = current["operations"].get(prefix + ":commit")
        if prior:
            return {**copy.deepcopy(prior["result"]), "replayed": True}
        observed = _observed(engine, task_id)
        if observed != manifest["git"]:
            raise EngineError("snapshot_changed", "Git binding or HEAD differs from the reserved snapshot")
        contents, metadata = _files(observed["path"], selected)
        if metadata != manifest["files"]:
            raise EngineError("snapshot_changed", "Selected bytes differ from the reserved snapshot")
        archive = _zip(manifest, contents)
        staging = safe_path(reserve["write_path"], [root], regular=True)
        atomic_bytes(staging, archive)
        if _files(observed["path"], selected)[1] != metadata or _observed(engine, task_id) != observed:
            raise EngineError("snapshot_changed", "Source changed before snapshot commit; staging is preserved")
        return engine.commit_artifact(task_id, prefix + ":commit", max(expected_revision, reserve["revision"]),
                                      reserve["reservation_id"], reserve["token"])
