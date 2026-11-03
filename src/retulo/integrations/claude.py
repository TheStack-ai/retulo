"""Guarded, project-local Claude Code MCP and guidance integration.

This module edits only an explicitly supplied project's .mcp.json and CLAUDE.md.
It never invokes Claude or changes its global settings, credentials or trust.
"""
from __future__ import annotations

from datetime import datetime, timezone
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from .codex import InstallConflict, atomic, digest, installation_locks, reject_symlink_path


START = b"<!-- retulo:claude:start -->"
END = b"<!-- retulo:claude:end -->"
MAX_BYTES = 8 * 1024 * 1024
MAX_RECEIPT = 1024 * 1024
PYTHON_MODULE_ARGS = ("-I", "-m", "retulo")


def _path(value, label):
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        raise ValueError(f"{label} must be an absolute path without parent traversal")
    reject_symlink_path(path)
    return Path(os.path.abspath(path))


def _read(path, limit=MAX_BYTES):
    """Bounded reads reject FIFOs, devices, links and special permission bits."""
    reject_symlink_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit or mode & ~0o777:
            raise ValueError(f"Expected a regular file of at most {limit} bytes: {path.name}")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError(f"File grew beyond read limit: {path.name}")
        return raw, mode


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _json(raw, label):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must contain valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _inputs(project, engine_config, workspace_home, snapshot_home):
    project, config, workspace, snapshots = [_path(value, name) for value, name in (
        (project, "project"), (engine_config, "engine_config"),
        (workspace_home, "workspace_home"), (snapshot_home, "snapshot_home"))]
    if not project.is_dir():
        raise ValueError("project must be an existing directory")
    for path in (workspace, snapshots):
        if path.exists() and not path.is_dir():
            raise ValueError(f"Expected a directory: {path.name}")
    identity = digest(str(project).encode())[:24]
    paths = {"mcp": project / ".mcp.json", "guidance": project / "CLAUDE.md",
             "config": config, "managed": snapshots / f"managed-claude-{identity}.json"}
    if len(set(paths.values())) != len(paths):
        raise ValueError("Claude setup targets must be distinct")
    # Runtime state and snapshot metadata must never become a source target.
    for path in paths.values():
        reject_symlink_path(path)
    if any(path == snapshots or snapshots.is_relative_to(path) for path in paths.values()):
        raise ValueError("Snapshot directory overlaps an installation target")
    return project, config, workspace, snapshots, identity, paths


def _block(raw):
    raw = raw or b""
    if raw.count(START) != raw.count(END) or raw.count(START) > 1:
        raise ValueError("Duplicate or incomplete Retulo Claude guidance markers")
    if not raw.count(START):
        return None, None
    begin = raw.index(START)
    end = raw.index(END) + len(END)
    if begin >= end - len(END):
        raise ValueError("Retulo Claude guidance markers are out of order")
    return raw[begin:end], (begin, end)


def _template():
    raw = files("retulo").joinpath("resources/claude/CLAUDE.md").read_bytes().rstrip(b"\r\n")
    if len(raw) > MAX_RECEIPT or _block(raw) != (raw, (0, len(raw))):
        raise ValueError("Invalid packaged Claude guidance template")
    return raw


def _mcp(raw):
    value = _json(raw, ".mcp.json") if raw is not None else {}
    servers = value.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(".mcp.json mcpServers must be an object")
    if "retulo" in servers and not isinstance(servers["retulo"], dict):
        raise ValueError("Existing Retulo MCP entry must be an object")
    return value, servers.get("retulo")


def _runtime(value):
    # Preserve the venv executable path: resolving its symlink loses its packages.
    path = Path(value or sys.executable).expanduser()
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        raise ValueError("runtime_python must be an absolute executable path")
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("runtime_python must be an existing executable file")
    return str(path)


def _settings(config, workspace, project):
    from retulo.cli import validate_config
    old, mode = _read(config, 65536)
    if old is None:
        value = {"schema_version": 1, "workspace_home": str(workspace),
                 "state_home": str(workspace / ".retulo"),
                 "allowed_workspace_roots": [str(workspace)], "allowed_source_roots": [str(project)]}
        validate_config(value)
        return old, _dump(value), mode
    value = _json(old, "Engine configuration")
    validated = validate_config(value)
    if validated["workspace_home"] != str(workspace):
        raise ValueError("Existing engine configuration differs for workspace_home; refusing to overwrite")
    for field in ("workspace_home", "state_home"):
        if validated.get(field):
            _path(validated[field], field)
    for field in ("allowed_source_roots", "allowed_workspace_roots"):
        for item in validated[field]:
            _path(item, field)
    if str(project) in validated["allowed_source_roots"]:
        return old, old, mode
    value["allowed_source_roots"] = [*value.get("allowed_source_roots", []), str(project)]
    validate_config(value)
    return old, _dump(value), mode


def _snapshot_path(value, snapshots, identity):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError("Claude snapshot must be an absolute project-specific path")
    path = _path(value, "snapshot")
    if (path.parent != snapshots or not path.name.startswith(f"claude-{identity}-") or
            not path.is_dir()):
        raise ValueError("Claude snapshot must be an existing project-specific child of snapshot_home")
    return path


def _setup_receipt(snapshot, paths, project, config, workspace, identity):
    raw, _ = _read(snapshot / "manifest.json", MAX_RECEIPT)
    if raw is None:
        raise ValueError("Managed Claude setup manifest is missing")
    record = _json(raw, "Managed Claude setup manifest")
    expected = {"schema_version": 1, "integration": "claude", "operation": "setup", "status": "complete",
                "project": str(project), "engine_config": str(config), "workspace_home": str(workspace),
                "identity": identity}
    if any(record.get(key) != item or type(record.get(key)) is not type(item) for key, item in expected.items()):
        raise ValueError("Managed Claude setup snapshot is incomplete or has a different identity")
    rows = record.get("files")
    order = [paths["mcp"], paths["guidance"], config, paths["managed"]]
    if (not isinstance(rows, list) or len(rows) != len(order) or
            any(not isinstance(row, dict) or row.get("path") != str(path) for row, path in zip(rows, order))):
        raise ValueError("Managed Claude setup snapshot target set differs")
    return record


def _managed(paths, project, config, workspace, snapshots, identity):
    raw, mode = _read(paths["managed"], MAX_RECEIPT)
    if raw is None:
        return None, raw, mode
    value = _json(raw, "Managed Claude receipt")
    expected = {"schema_version": 1, "integration": "claude", "project": str(project),
                "engine_config": str(config), "workspace_home": str(workspace)}
    for key, item in expected.items():
        if value.get(key) != item or type(value.get(key)) is not type(item):
            raise ValueError(f"Managed Claude receipt identity differs for {key}")
    if value.get("active") is not True:
        raise ValueError("Invalid managed Claude receipt state")
    snapshot = _snapshot_path(value.get("snapshot", ""), snapshots, identity)
    receipt = _setup_receipt(snapshot, paths, project, config, workspace, identity)
    if receipt["files"][3].get("after_sha256") != digest(raw):
        raise ValueError("Managed Claude receipt does not match its completed setup snapshot")
    components = value.get("components")
    if not isinstance(components, dict) or set(components) != {"mcp", "guidance"}:
        raise ValueError("Invalid managed Claude components")
    for name, filename in (("mcp", "mcp-default.json"), ("guidance", "guidance-default.md")):
        item = components[name]
        if (not isinstance(item, dict) or item.get("path") != str(paths[name]) or
                not _sha(item.get("sha256")) or item.get("base_copy") != str(snapshot / filename)):
            raise ValueError("Invalid managed Claude component identity")
        base, _ = _read(snapshot / filename, MAX_RECEIPT)
        if base is None or digest(base) != item["sha256"]:
            raise ValueError("Managed Claude component baseline does not match receipt")
    initial = _snapshot_path(value.get("initial_snapshot", ""), snapshots, identity)
    if initial != snapshot:
        _setup_receipt(initial, paths, project, config, workspace, identity)
    originals = value.get("originals")
    if not isinstance(originals, dict) or set(originals) != {"mcp", "guidance"}:
        raise ValueError("Invalid original Claude target records")
    for name in ("mcp", "guidance"):
        item = originals[name]
        if not isinstance(item, dict) or "sha256" not in item:
            raise ValueError("Invalid original Claude target record")
        sha = item["sha256"]
        expected_copy = str(initial / f"{name}-original.before") if sha is not None else None
        if (sha is not None and not _sha(sha)) or item.get("backup") != expected_copy:
            raise ValueError("Invalid original Claude backup identity")
        if sha is not None:
            data, _ = _read(initial / f"{name}-original.before")
            if data is None or digest(data) != sha:
                raise ValueError("Original Claude backup does not match receipt")
    return value, raw, mode


def _component(name, current, candidate, managed, path):
    base = managed["components"][name]["sha256"] if managed else None
    sha = digest(current) if current is not None else None
    if current is not None and managed is None:
        raise InstallConflict(f"Retulo Claude {name} already exists without verified ownership; refusing to adopt it",
                              {"target": name, "path": str(path), "status": "conflict",
                               "reason": "baseline_missing"})
    if current == candidate:
        state = "unchanged"
    elif current is None and not managed:
        state = "new"
    elif base and sha == base:
        state = "managed_upgrade"
    else:
        raise InstallConflict(f"Retulo Claude {name} differs; refusing to overwrite unverified or user-edited content",
                              {"target": name, "path": str(path), "status": "conflict",
                               "reason": "user_modified" if managed else "baseline_missing"})
    return {"status": state, "base_sha256": base, "current_sha256": sha,
            "candidate_sha256": digest(candidate)}


def _same(path, raw, mode):
    return _read(path) == (raw, mode)


def _replace(path, raw, mode, expected, expected_mode):
    if not _same(path, expected, expected_mode):
        raise ValueError("Concurrent settings or mode change; Claude setup cancelled")
    if raw is None:
        path.unlink()
    else:
        atomic(path, raw, mode if mode is not None else 0o600, expected=expected)


def _transaction(outputs, snapshot, project, config, workspace, identity, operation, copies=()):
    """Write a guarded snapshot then apply, rolling back only unchanged own writes."""
    for path, old, _, mode, _ in outputs:
        if not _same(path, old, mode):
            raise ValueError("Concurrent settings change; Claude setup cancelled")
    reject_symlink_path(snapshot)
    snapshot.mkdir(parents=True, mode=0o700)
    rows = []
    for index, (path, old, fresh, mode, new_mode) in enumerate(outputs):
        backup = snapshot / f"{index:02d}.before"
        if old is not None:
            atomic(backup, old)
        rows.append({"path": str(path), "before_sha256": digest(old) if old is not None else None,
                     "after_sha256": digest(fresh) if fresh is not None else None,
                     "before_mode": mode, "after_mode": new_mode,
                     "backup": str(backup) if old is not None else None})
    for path, raw in copies:
        atomic(path, raw)
    receipt = {"schema_version": 1, "integration": "claude", "operation": operation,
               "project": str(project), "engine_config": str(config), "workspace_home": str(workspace),
               "identity": identity, "status": "prepared", "files": rows}
    manifest = snapshot / "manifest.json"
    atomic(manifest, _dump(receipt))
    written = []
    try:
        for row in outputs:
            path, old, fresh, mode, new_mode = row
            if old != fresh or mode != new_mode:
                _replace(path, fresh, new_mode, old, mode)
                written.append(row)
            elif not _same(path, old, mode):
                raise ValueError("Concurrent settings change; Claude setup cancelled")
        receipt["status"] = "complete"
        atomic(manifest, _dump(receipt))
    except Exception as exc:
        for path, old, fresh, mode, new_mode in reversed(written):
            try:
                _replace(path, old, mode, fresh, new_mode)
            except (ValueError, OSError) as error:
                exc.add_note(f"Rollback preserved {path} for review: {error}")
        try:
            checks = [{"path": str(path), "sha256": digest(raw) if raw is not None else None,
                       "mode": mode} for path, _, _, _, _ in outputs for raw, mode in [_read(path)]]
            restored = all(_same(path, old, mode) for path, old, _, mode, _ in outputs)
            receipt.update(status="rolled_back" if restored else "rollback_incomplete",
                           rollback_verified=restored, rollback_checks=checks)
            atomic(manifest, _dump(receipt))
        except (ValueError, OSError) as error:
            exc.add_note(f"Rollback verification remains incomplete: {error}")
        raise


def _new_snapshot(snapshots, identity):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return snapshots / f"claude-{identity}-{stamp}-{uuid.uuid4().hex[:8]}"


def _install(project, engine_config, workspace_home, snapshot_home, apply=False, runtime_python=None):
    project, config, workspace, snapshots, identity, paths = _inputs(
        project, engine_config, workspace_home, snapshot_home)
    managed, managed_old, managed_mode = _managed(paths, project, config, workspace, snapshots, identity)
    mcp_old, mcp_mode = _read(paths["mcp"])
    guidance_old, guidance_mode = _read(paths["guidance"])
    settings_old, settings_new, settings_mode = _settings(config, workspace, project)
    parsed, server = _mcp(mcp_old)
    candidate = {"type": "stdio", "command": _runtime(runtime_python),
                 "args": [*PYTHON_MODULE_ARGS, "--config", str(config), "mcp"]}
    template = _template()
    current_block, bounds = _block(guidance_old)
    components = {
        "mcp": _component("mcp", _canonical(server) if server is not None else None,
                          _canonical(candidate), managed, paths["mcp"]),
        "guidance": _component("guidance", current_block, template, managed, paths["guidance"])}
    parsed.setdefault("mcpServers", {})["retulo"] = candidate
    mcp_new = mcp_old if server == candidate else _dump(parsed)
    if bounds:
        guidance_new = guidance_old[:bounds[0]] + template + guidance_old[bounds[1]:]
    else:
        prefix = guidance_old or b""
        guidance_new = prefix + (b"" if not prefix or prefix.endswith(b"\n") else b"\n") + template + b"\n"
    outputs = [(paths["mcp"], mcp_old, mcp_new, mcp_mode, mcp_mode if mcp_mode is not None else 0o600),
               (paths["guidance"], guidance_old, guidance_new, guidance_mode,
                guidance_mode if guidance_mode is not None else 0o600),
               (config, settings_old, settings_new, settings_mode,
                settings_mode if settings_mode is not None else 0o600)]
    result = {"integration": "claude", "apply": apply, "project": str(project),
              "engine_config": str(config), "workspace_home": str(workspace),
              "files": [{"path": str(path), "changed": old != fresh} for path, old, fresh, _, _ in outputs],
              "components": components, "snapshot": None, "managed_baseline": str(paths["managed"]),
              "native_trust": "review_in_claude_code", "unrelated_settings_preserved": True,
              "configuration_scope": "project", "github_publication": False}
    result["files"].append({"path": str(paths["managed"]),
                            "changed": managed is None or any(old != fresh for _, old, fresh, _, _ in outputs)})
    if not apply or (managed and all(old == fresh for _, old, fresh, _, _ in outputs)):
        return result
    snapshot = _new_snapshot(snapshots, identity)
    originals = managed["originals"] if managed else {
        name: {"sha256": digest(raw) if raw is not None else None,
               "backup": str(snapshot / f"{name}-original.before") if raw is not None else None}
        for name, raw in (("mcp", mcp_old), ("guidance", guidance_old))}
    record = {"schema_version": 1, "integration": "claude", "active": True,
              "project": str(project), "engine_config": str(config), "workspace_home": str(workspace),
              "snapshot": str(snapshot), "initial_snapshot": managed["initial_snapshot"] if managed else str(snapshot),
              "originals": originals, "components": {
                  name: {"path": str(paths[name]), "sha256": digest(raw), "base_copy": str(snapshot / filename)}
                  for name, raw, filename in (("mcp", _canonical(candidate), "mcp-default.json"),
                                              ("guidance", template, "guidance-default.md"))}}
    copies = [(snapshot / "mcp-default.json", _canonical(candidate)),
              (snapshot / "guidance-default.md", template),
              (snapshot / "mcp-installed.json", mcp_new), (snapshot / "guidance-installed.md", guidance_new)]
    if not managed:
        copies += [(snapshot / f"{name}-original.before", raw)
                   for name, raw in (("mcp", mcp_old), ("guidance", guidance_old)) if raw is not None]
    outputs.append((paths["managed"], managed_old, _dump(record), managed_mode,
                    managed_mode if managed_mode is not None else 0o600))
    _transaction(outputs, snapshot, project, config, workspace, identity, "setup", copies)
    result["snapshot"] = str(snapshot)
    return result


def install(project, engine_config, workspace_home, snapshot_home, apply=False, runtime_python=None):
    """Preview or install project-local native Claude configuration and guidance."""
    options = dict(project=project, engine_config=engine_config, workspace_home=workspace_home,
                   snapshot_home=snapshot_home, runtime_python=runtime_python)
    *_, paths = _inputs(project, engine_config, workspace_home, snapshot_home)
    with installation_locks(paths.values(), create=False):
        preview = _install(**options)
    if not apply:
        return preview
    with installation_locks(paths.values()):
        return _install(**options, apply=True)


def status(project, engine_config, workspace_home, snapshot_home):
    """Read component ownership and config status without creating any files."""
    project, config, workspace, snapshots, identity, paths = _inputs(
        project, engine_config, workspace_home, snapshot_home)
    result = {"integration": "claude", "project": str(project), "engine_config": str(config),
              "workspace_home": str(workspace), "connected": False, "managed": False, "ready": False,
              "components": {}, "conflicts": [], "native_trust": "review_in_claude_code",
              "files": [{"path": str(path), "exists": path.exists()} for path in paths.values()]}
    with installation_locks(paths.values(), create=False):
        try:
            managed, _, _ = _managed(paths, project, config, workspace, snapshots, identity)
            mcp, _ = _read(paths["mcp"])
            guidance, _ = _read(paths["guidance"])
            _, entry = _mcp(mcp)
            block, _ = _block(guidance)
            result["connected"] = entry is not None
            result["managed"] = managed is not None
            for name, raw in (("mcp", _canonical(entry) if entry is not None else None), ("guidance", block)):
                matches = bool(managed and raw is not None and digest(raw) == managed["components"][name]["sha256"])
                state = "managed" if matches else "missing" if raw is None else "unmanaged"
                result["components"][name] = {"status": state}
                if managed and not matches:
                    result["conflicts"].append(f"Managed {name} component was changed or removed")
            settings_raw, _ = _read(config, 65536)
            if settings_raw is None:
                result["conflicts"].append("Engine configuration is missing")
            else:
                from retulo.cli import validate_config
                settings = validate_config(_json(settings_raw, "Engine configuration"))
                _settings(config, workspace, project)
                if str(project) not in settings["allowed_source_roots"]:
                    result["conflicts"].append("Explicit project root is absent from engine configuration")
            if entry is not None:
                command = entry.get("command")
                expected_args = [*PYTHON_MODULE_ARGS, "--config", str(config), "mcp"]
                if (entry.get("type", "stdio") != "stdio" or entry.get("args") != expected_args or
                        not isinstance(command, str)):
                    result["conflicts"].append("Retulo MCP entry differs from the expected native stdio command")
                else:
                    try:
                        _runtime(command)
                    except ValueError as exc:
                        result["conflicts"].append(str(exc))
            result["ready"] = bool(managed and entry is not None and block is not None and not result["conflicts"])
        except (ValueError, OSError) as exc:
            result["conflicts"].append(str(exc))
    return result


def _original(managed, name):
    item = managed["originals"][name]
    return _read(Path(item["backup"]))[0] if item["backup"] is not None else None


def _disconnect(project, engine_config, workspace_home, snapshot_home, apply=False, snapshot=None):
    project, config, workspace, snapshots, identity, paths = _inputs(
        project, engine_config, workspace_home, snapshot_home)
    if snapshot is not None:
        return _undo(snapshot, paths, project, config, workspace, snapshots, identity, apply)
    managed, managed_old, managed_mode = _managed(paths, project, config, workspace, snapshots, identity)
    if managed is None:
        mcp, _ = _read(paths["mcp"])
        guidance, _ = _read(paths["guidance"])
        _, entry = _mcp(mcp)
        block, _ = _block(guidance)
        if entry is not None or block is not None:
            raise ValueError("Retulo Claude components have no managed baseline; refusing to remove them")
        return {"integration": "claude", "apply": apply, "status": "already_disconnected", "files": [],
                "snapshot": None, "workspaces_preserved": True, "configuration_preserved": True}
    mcp_old, mcp_mode = _read(paths["mcp"])
    guidance_old, guidance_mode = _read(paths["guidance"])
    parsed, entry = _mcp(mcp_old)
    block, bounds = _block(guidance_old)
    for name, raw in (("mcp", _canonical(entry) if entry is not None else None), ("guidance", block)):
        if raw is None or digest(raw) != managed["components"][name]["sha256"]:
            raise InstallConflict(f"Retulo Claude {name} changed; refusing disconnect",
                                  {"target": name, "status": "conflict", "reason": "user_modified"})
    originals = {name: _original(managed, name) for name in ("mcp", "guidance")}
    parsed["mcpServers"].pop("retulo")
    initial_value, initial_entry = _mcp(originals["mcp"])
    if initial_entry is not None:
        parsed["mcpServers"]["retulo"] = initial_entry
    if not parsed["mcpServers"] and "mcpServers" not in initial_value:
        parsed.pop("mcpServers")
    if parsed == initial_value:
        mcp_new = originals["mcp"]
    else:
        mcp_new = None if not parsed and originals["mcp"] is None else _dump(parsed)
    initial_block, initial_bounds = _block(originals["guidance"])
    initial_guidance = originals["guidance"] or b""
    if initial_bounds:
        expected_guidance = initial_guidance[:initial_bounds[0]] + block + initial_guidance[initial_bounds[1]:]
    else:
        separator = b"" if not initial_guidance or initial_guidance.endswith(b"\n") else b"\n"
        expected_guidance = initial_guidance + separator + block + b"\n"
    if guidance_old == expected_guidance:
        guidance_new = originals["guidance"]
    else:
        prefix, suffix = guidance_old[:bounds[0]], guidance_old[bounds[1]:]
        if initial_block is None and suffix.startswith(b"\n"):
            suffix = suffix[1:]
        guidance_new = prefix + (initial_block or b"") + suffix
    outputs = [(paths["mcp"], mcp_old, mcp_new, mcp_mode, mcp_mode if mcp_new is not None else None),
               (paths["guidance"], guidance_old, guidance_new, guidance_mode, guidance_mode if guidance_new is not None else None),
               (paths["managed"], managed_old, None, managed_mode, None)]
    return _finish_disconnect(outputs, project, config, workspace, snapshots, identity, apply)


def _finish_disconnect(outputs, project, config, workspace, snapshots, identity, apply):
    result = {"integration": "claude", "apply": apply, "status": "disconnected" if apply else "preview",
              "files": [{"path": str(path), "changed": old != fresh} for path, old, fresh, _, _ in outputs],
              "snapshot": None, "workspaces_preserved": True, "configuration_preserved": True}
    if apply and any(old != fresh or mode != new_mode for _, old, fresh, mode, new_mode in outputs):
        selected = _new_snapshot(snapshots, identity)
        _transaction(outputs, selected, project, config, workspace, identity, "disconnect")
        result["snapshot"] = str(selected)
    return result


def _undo(selected, paths, project, config, workspace, snapshots, identity, apply):
    selected = _snapshot_path(selected, snapshots, identity)
    raw, _ = _read(selected / "manifest.json", MAX_RECEIPT)
    if raw is None:
        raise ValueError("Claude snapshot manifest is missing")
    record = _json(raw, "Claude snapshot manifest")
    expected = {"schema_version": 1, "integration": "claude", "project": str(project),
                "engine_config": str(config), "workspace_home": str(workspace), "identity": identity,
                "status": "complete", "operation": "setup"}
    if any(record.get(key) != value or type(record.get(key)) is not type(value) for key, value in expected.items()):
        raise ValueError("Only a completed matching Claude setup snapshot can be undone")
    rows = record.get("files")
    if not isinstance(rows, list) or len(rows) != 4:
        raise ValueError("Claude snapshot target set differs")
    seen, plans, positions = set(), [], [paths["mcp"], paths["guidance"], config, paths["managed"]]
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("path") != str(positions[index]) or row["path"] in seen:
            raise ValueError("Claude snapshot target is duplicated or outside explicit target set")
        seen.add(row["path"])
        before_sha, after_sha = row.get("before_sha256"), row.get("after_sha256")
        if ("before_sha256" not in row or (before_sha is not None and not _sha(before_sha)) or
                not _sha(after_sha)):
            raise ValueError("Invalid Claude snapshot hash")
        for field in ("before_mode", "after_mode"):
            mode = row.get(field)
            if mode is not None and (type(mode) is not int or not 0 <= mode <= 0o777):
                raise ValueError("Invalid Claude snapshot file mode")
        if row.get("after_mode") is None or (before_sha is not None and row.get("before_mode") is None):
            raise ValueError("Present Claude snapshot targets must have a file mode")
        before = None
        if before_sha is None:
            if row.get("backup") is not None or row.get("before_mode") is not None:
                raise ValueError("Invalid absent Claude snapshot target")
        else:
            backup = selected / f"{index:02d}.before"
            if row.get("backup") != str(backup):
                raise ValueError("Claude snapshot backup must be the indexed file inside the selected snapshot")
            before, _ = _read(backup)
            if before is None or digest(before) != before_sha:
                raise ValueError("Claude snapshot backup hash differs")
        if index == 2:  # Engine configuration is deliberately retained on disconnect/undo.
            continue
        current, mode = _read(positions[index])
        sha = digest(current) if current is not None else None
        matches_before = sha == before_sha and mode == row.get("before_mode")
        matches_after = sha == after_sha and mode == row.get("after_mode")
        plans.append((positions[index], current, before, mode, row.get("before_mode"), matches_before, matches_after))
    if not all(row[5] for row in plans) and not all(row[6] for row in plans):
        raise ValueError("Claude files changed or snapshot is not the latest applicable step; refusing undo")
    if all(row[5] for row in plans):
        return {"integration": "claude", "apply": apply, "status": "already_disconnected", "snapshot": None,
                "files": [], "workspaces_preserved": True, "configuration_preserved": True}
    if _read(selected / "manifest.json", MAX_RECEIPT)[0] != raw:
        raise ValueError("Concurrent Claude snapshot change; refusing undo")
    result = _finish_disconnect([row[:5] for row in plans], project, config, workspace, snapshots, identity, apply)
    if apply:
        result["status"] = "restored"
    return result


def disconnect(project, engine_config, workspace_home, snapshot_home, apply=False, snapshot=None):
    """Remove verified owned components, or undo one explicit completed setup snapshot."""
    options = dict(project=project, engine_config=engine_config, workspace_home=workspace_home,
                   snapshot_home=snapshot_home, snapshot=snapshot)
    *_, paths = _inputs(project, engine_config, workspace_home, snapshot_home)
    with installation_locks(paths.values(), create=False):
        preview = _disconnect(**options)
    if not apply:
        return preview
    with installation_locks(paths.values()):
        return _disconnect(**options, apply=True)
