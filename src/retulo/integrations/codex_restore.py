#!/usr/bin/env python3
"""Preview or restore one unchanged Retulo installation snapshot."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys


from retulo.integrations import codex as installer

MAX_FILE_BYTES = 8 * 1024 * 1024


def _path(value, label):
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute path without parent traversal")
    installer.reject_symlink_path(path)
    return path


def _read(path):
    """Read only an ordinary file; absent files are a distinct state."""
    installer.reject_symlink_path(path)
    try:
        # O_NONBLOCK lets us reject a FIFO without waiting for a writer.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError(f"Expected a regular file of at most {MAX_FILE_BYTES} bytes: {path.name}")
        if stat.S_IMODE(info.st_mode) & ~0o777:
            raise ValueError(f"Unsupported special permission bits: {path.name}")
        raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(f"File grew beyond restore limit: {path.name}")
        return raw, stat.S_IMODE(info.st_mode)


def _mode(row, name):
    value = row.get(name)
    if value is not None and (type(value) is not int or not 0 <= value <= 0o777):
        raise ValueError(f"Invalid {name} in snapshot")
    return value


def _targets(codex_home, engine_config, skills_home, snapshot_home):
    codex_home = _path(codex_home, "codex_home")
    engine_config = _path(engine_config, "engine_config")
    skills_home = _path(skills_home, "skills_home")
    snapshot_home = _path(snapshot_home, "snapshot_home")
    paths = [codex_home / "config.toml", engine_config,
             skills_home / "retulo/SKILL.md", codex_home / "AGENTS.md",
             codex_home / "hooks.json", snapshot_home / "managed-skill.json",
             snapshot_home / "managed-harness.json"]
    if len(set(paths)) != len(paths):
        raise ValueError("Restore targets must be distinct")
    for path in paths:
        installer.reject_symlink_path(path)
    return paths, snapshot_home


def _plan(snapshot, targets, snapshot_home, preserve_engine_config=False, current_overrides=None,
          check_current=True):
    snapshot = _path(snapshot, "snapshot")
    if snapshot.parent != snapshot_home or not snapshot.is_dir():
        raise ValueError("Snapshot must be an existing direct child of snapshot_home")
    if any(path.is_relative_to(snapshot) for path in targets):
        raise ValueError("Restore targets must not overlap the selected snapshot")
    manifest_path = snapshot / "manifest.json"
    raw, _ = _read(manifest_path)
    if raw is None or len(raw) > 1048576:
        raise ValueError("Missing or oversized snapshot manifest")
    record = json.loads(raw)
    if (not isinstance(record, dict) or type(record.get("schema_version")) is not int or
            record["schema_version"] != 1 or record.get("status") != "complete"):
        raise ValueError("Only a completed schema-version-1 installation snapshot can be restored")
    rows = record.get("files")
    if not isinstance(rows, list) or len(rows) != len(targets):
        raise ValueError("Snapshot must contain the complete explicit target set")
    allowed, seen, plans = set(targets), set(), []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Invalid snapshot file record")
        path = _path(row["path"], "snapshot target")
        if path not in allowed or path in seen:
            raise ValueError("Snapshot target is duplicated or outside the explicit target set")
        seen.add(path)
        before_sha, after_sha = row.get("before_sha256"), row.get("after_sha256")
        if "before_sha256" not in row or (before_sha is not None and not installer._sha(before_sha)):
            raise ValueError("Invalid before hash in snapshot")
        if not installer._sha(after_sha):
            raise ValueError("Invalid after hash in snapshot")
        before = None
        if before_sha is None:
            if row.get("backup") is not None:
                raise ValueError("An initially absent target must not have a backup")
        else:
            if not isinstance(row.get("backup"), str):
                raise ValueError("Existing target backup is missing")
            backup = _path(row["backup"], "backup")
            if backup != snapshot / f"{index:02d}.before":
                raise ValueError("Backup must be the indexed file inside the selected snapshot")
            before, _ = _read(backup)
            if before is None or installer.digest(before) != before_sha:
                raise ValueError("Snapshot backup hash does not match")
        before_mode, after_mode = _mode(row, "before_mode"), _mode(row, "after_mode")
        if before is None and before_mode is not None:
            raise ValueError("Initially absent target cannot have a before mode")
        current, current_mode = (current_overrides[path] if current_overrides is not None and path in current_overrides
                                 else _read(path))
        current_sha = installer.digest(current) if current is not None else None
        preserved = preserve_engine_config and path == targets[1]
        plans.append({"path": path, "before": before, "current": current,
                      "before_sha256": before_sha, "after_sha256": after_sha,
                      "current_sha256": current_sha, "current_mode": current_mode,
                      "before_mode": before_mode, "after_mode": after_mode,
                      "preserved": preserved,
                      "restore_mode": before_mode if before_mode is not None else current_mode,
                      "matches_after": preserved or (current_sha == after_sha and
                          (after_mode is None or current_mode == after_mode)),
                      "matches_before": preserved or (current_sha == before_sha and
                          (before_mode is None or current_mode == before_mode))})
    if seen != allowed:
        raise ValueError("Snapshot does not match the complete explicit target set")
    already_before = all(row["matches_before"] for row in plans)
    if check_current and not already_before and not all(row["matches_after"] for row in plans):
        raise ValueError("Installed files changed or snapshot is not the latest applicable step; refusing restore")
    return snapshot, installer.digest(raw), plans, already_before


def _same(path, expected, mode):
    current, current_mode = _read(path)
    return current == expected and current_mode == mode


def _replace(path, replacement, mode, expected, expected_mode):
    if not _same(path, expected, expected_mode):
        raise ValueError("Concurrent settings or mode change; restore cancelled")
    if replacement is None:
        # Existing installer locks are shared with install/update. As with the
        # installer CAS, unrelated editors must remain closed during apply.
        path.unlink()
    else:
        installer.atomic(path, replacement, mode, expected=expected)


def _rollback(attempted, error):
    """Return to the installed bytes after a failed apply, preserving late edits."""
    for row in reversed(attempted):
        path = row["path"]
        try:
            if _same(path, row["current"], row["current_mode"]):
                continue
            goal_mode = row["restore_mode"] if row["before"] is not None else None
            if not _same(path, row["before"], goal_mode):
                raise ValueError("target changed after restore began")
            _replace(path, row["current"], row["current_mode"], row["before"], goal_mode)
        except (OSError, ValueError) as rollback_error:
            error.add_note(f"Rollback preserved {path} for review: {rollback_error}")


def restore(snapshot, codex_home, engine_config, skills_home, snapshot_home, apply=False,
            preserve_engine_config=False):
    targets, snapshot_home = _targets(codex_home, engine_config, skills_home, snapshot_home)
    with installer.installation_locks(targets, create=False):
        planned = _plan(snapshot, targets, snapshot_home, preserve_engine_config)
    snapshot, manifest_sha, plans, already_before = planned

    def result(status):
        return {"apply": apply, "status": status, "snapshot": str(snapshot),
                "manifest_sha256": manifest_sha, "workspaces_preserved": True,
                "snapshots_preserved": True,
                "engine_config_preserved": preserve_engine_config,
                "files": [{"path": str(row["path"]), "before_sha256": row["before_sha256"],
                           "installed_sha256": row["after_sha256"],
                           "action": "preserve" if row["preserved"] else
                               "restore" if row["before"] is not None else "remove_created_file",
                           "mode_source": "snapshot" if row["before_mode"] is not None else
                               "current_file; legacy snapshot has no mode metadata"}
                          for row in plans]}

    if not apply or already_before:
        return result("already_at_before_state" if already_before else "ready")
    with installer.installation_locks(targets):
        snapshot, manifest_sha, plans, already_before = _plan(snapshot, targets, snapshot_home, preserve_engine_config)
        if already_before:
            return result("already_at_before_state")
        attempted = []
        try:
            for row in plans:
                if row["preserved"]:
                    continue
                mode = row["restore_mode"] if row["before"] is not None else None
                if row["before"] == row["current"] and mode == row["current_mode"]:
                    continue
                attempted.append(row)
                _replace(row["path"], row["before"], mode, row["current"], row["current_mode"])
            for row in plans:
                if row["preserved"]:
                    continue
                mode = row["restore_mode"] if row["before"] is not None else None
                if not _same(row["path"], row["before"], mode):
                    raise ValueError("Restore verification found a concurrent change")
        except BaseException as error:
            _rollback(attempted, error)
            raise
    return result("restored")


def restore_chain(snapshots, codex_home, engine_config, skills_home, snapshot_home, apply=False,
                  preserve_engine_config=False):
    """Validate and undo a bounded newest-to-oldest chain as one guarded transaction.

    Preview follows the restored bytes in memory, without creating temporary
    client settings. Snapshots must be selected explicitly by the caller.
    """
    snapshots = list(snapshots)
    if not 1 <= len(snapshots) <= 64 or len({str(Path(p)) for p in snapshots}) != len(snapshots):
        raise ValueError('Restore chain must contain 1 to 64 distinct snapshots')
    targets, snapshot_home = _targets(codex_home, engine_config, skills_home, snapshot_home)

    def plan_chain():
        observed, initial, steps = {}, {}, []
        for index, selected in enumerate(snapshots):
            snapshot, manifest_sha, plans, already_before = _plan(
                selected, targets, snapshot_home, preserve_engine_config, observed, check_current=index != 0)
            if index == 0:
                head_valid = already_before or all(row['matches_after'] for row in plans)
            for row in plans:
                initial.setdefault(row['path'], row)
                if not row['preserved']:
                    observed[row['path']] = (row['before'], row['restore_mode'] if row['before'] is not None else None)
            steps.append({'snapshot':str(snapshot), 'manifest_sha256':manifest_sha,
                          'status':'already_at_before_state' if already_before else 'ready'})
        combined = []
        for path in targets:
            row = dict(initial[path])
            if not row['preserved']:
                row['before'], row['restore_mode'] = observed[path]
            combined.append(row)
        # A repeated complete disconnect may already be at the terminal state,
        # even though it no longer matches an intermediate upgrade's before state.
        if not head_valid and any(not row['preserved'] and
                (row['current'] != row['before'] or row['current_mode'] != row['restore_mode']) for row in combined):
            raise ValueError('Installed files changed or snapshot is not the latest applicable step; refusing restore')
        return steps, combined

    def result(status, steps, plans):
        return {'apply':apply, 'status':status, 'snapshots':steps,
                'workspaces_preserved':True, 'snapshots_preserved':True,
                'engine_config_preserved':preserve_engine_config,
                'files':[{'path':str(row['path']), 'action':'preserve' if row['preserved'] else
                          'restore' if row['before'] is not None else 'remove_created_file',
                          'changed':not row['preserved'] and (row['current'] != row['before'] or
                                     row['current_mode'] != row['restore_mode'])} for row in plans]}

    with installer.installation_locks(targets, create=False):
        steps, plans = plan_chain()
    changed = any(not row['preserved'] and (row['current'] != row['before'] or
                  row['current_mode'] != row['restore_mode']) for row in plans)
    if not apply or not changed:
        return result('ready' if changed else 'already_at_before_state', steps, plans)
    with installer.installation_locks(targets):
        steps, plans = plan_chain()
        attempted = []
        try:
            for row in plans:
                if row['preserved'] or (row['current'] == row['before'] and row['current_mode'] == row['restore_mode']):
                    continue
                attempted.append(row)
                _replace(row['path'], row['before'], row['restore_mode'], row['current'], row['current_mode'])
            for row in plans:
                if not row['preserved'] and not _same(row['path'], row['before'], row['restore_mode']):
                    raise ValueError('Restore chain verification found a concurrent change')
        except BaseException as error:
            _rollback(attempted, error)
            raise
    return result('restored', steps, plans)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "codex-home", "engine-config", "skills-home", "snapshot-home"):
        parser.add_argument("--" + name, required=True, help="Explicit absolute path")
    parser.add_argument("--apply", action="store_true", help="Apply the previewed snapshot restoration")
    parser.add_argument("--preserve-engine-config", action="store_true", help="Keep shared core configuration")
    args = vars(parser.parse_args(argv))
    try:
        print(json.dumps(restore(**args), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": {"code": "restore_conflict",
                         "message": str(error), "notes": getattr(error, "__notes__", [])}},
                         ensure_ascii=False, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
