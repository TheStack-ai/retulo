"""Explicit local CLI/configuration entry point; reading never initializes state."""
from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from . import __version__
from .projection import SECTIONS as RECORD_SECTIONS


METHODS = frozenset({
    "capabilities", "begin_task", "get_context", "list_tasks", "add_source",
    "reserve_output", "commit_artifact", "checkpoint", "record_approval",
    "resume_task", "verify", "complete_task", "record_external_artifact", "attach_workspace",
    "abandon_output", "read_records", "harness_context",
    "define_workflow", "bind_input", "plan_rebuild", "begin_step_run", "finish_step_run",
    "check_recipe", "begin_from_recipe",
    "git_status", "git_bind", "git_create_worktree", "git_prepare_integration",
    "git_integrate", "git_prepare_remove", "git_remove_worktree",
    "git_record_snapshot", "git_abandon_pending",
})


class ConfigError(ValueError):
    """A safe, actionable configuration error."""

    code = "configuration_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = {}


def default_config_path() -> Path:
    override = os.environ.get("RETULO_CONFIG")
    return Path(override).expanduser() if override else Path.home() / ".config/retulo/config.json"


def default_workspace_home() -> Path:
    """A proposal used only by explicit init-config; never an implicit fallback."""
    return Path.home() / "Documents/RetuloWorkspaces"


def _no_symlink(path: Path) -> None:
    for node in (path, *path.parents):
        if node.is_symlink():
            raise ConfigError("Configuration paths must not contain symbolic links")


def _absolute_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigError(f"{field} must be a nonempty absolute path")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute() or ".." in expanded.parts:
        raise ConfigError(f"{field} must be absolute and must not contain '..'")
    return str(expanded)


def validate_config(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("Configuration must be a JSON object")
    allowed = {"schema_version", "workspace_home", "state_home", "allowed_source_roots",
               "allowed_workspace_roots", "allowed_git_worktree_roots"}
    unsupported = set(data) - allowed
    if unsupported:
        raise ConfigError("Configuration contains unsupported keys: " + ", ".join(sorted(unsupported)))
    if type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) != 1:
        raise ConfigError("Unsupported configuration schema_version")
    normalized = dict(data)
    normalized["schema_version"] = 1
    normalized["workspace_home"] = _absolute_path(data.get("workspace_home"), "workspace_home")
    if data.get("state_home") is not None:
        normalized["state_home"] = _absolute_path(data["state_home"], "state_home")
    for field in ("allowed_source_roots", "allowed_workspace_roots", "allowed_git_worktree_roots"):
        values = data.get(field, [])
        if not isinstance(values, list) or len(values) > 128:
            raise ConfigError(f"{field} must be a bounded array of paths")
        normalized[field] = [_absolute_path(item, field) for item in values]
    return normalized


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    location = Path(path).expanduser() if path else default_config_path()
    _no_symlink(location.absolute())
    if not location.is_file():
        raise ConfigError("Configuration is missing; run init-config explicitly before using the engine")
    if location.stat().st_size > 65536:
        raise ConfigError("Configuration exceeds 65536 bytes")
    try:
        return validate_config(json.loads(location.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError("Configuration must contain valid UTF-8 JSON") from exc


def init_config(path: str | Path | None = None, *, workspace_home: str | None = None,
                state_home: str | None = None, allowed_source_roots: list[str] | None = None,
                allowed_workspace_roots: list[str] | None = None,
                allowed_git_worktree_roots: list[str] | None = None) -> dict[str, Any]:
    """Create configuration only, exclusively; never replace user configuration."""
    location = (Path(path).expanduser() if path else default_config_path()).absolute()
    _no_symlink(location)
    config: dict[str, Any] = {
        "schema_version": 1,
        "workspace_home": workspace_home or str(default_workspace_home()),
        "allowed_source_roots": allowed_source_roots or [],
        "allowed_workspace_roots": allowed_workspace_roots or [],
        "allowed_git_worktree_roots": allowed_git_worktree_roots or [],
    }
    if state_home is not None:
        config["state_home"] = state_home
    config = validate_config(config)
    location.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _no_symlink(location)
    fd, temporary = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=location.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            # A completed file becomes visible in one step; link never replaces an existing path.
            os.link(temporary, location, follow_symlinks=False)
        except FileExistsError as exc:
            raise ConfigError("Configuration already exists; it was not overwritten") from exc
    finally:
        os.unlink(temporary)
    return {"ok": True, "config_path": str(location), "workspace_home": config["workspace_home"],
            "workspace_created": False, "existing_configuration_overwritten": False}


def build_engine(config: dict[str, Any]):
    from .engine import Engine
    return Engine(**{key: config[key] for key in ("workspace_home", "state_home", "allowed_source_roots",
                                                 "allowed_workspace_roots", "allowed_git_worktree_roots") if key in config})


def dispatch(engine: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Public core allowlist shared by CLI and MCP; no arbitrary method access."""
    if name not in METHODS:
        raise ConfigError("Unknown engine method")
    if not isinstance(arguments, dict):
        raise ConfigError("Arguments must be a JSON object")
    if name == "harness_context":
        from functools import partial
        from .harness import harness_context
        function = partial(harness_context, engine)
    else:
        function = getattr(engine, name)
    try:
        inspect.signature(function).bind(**arguments)
    except TypeError as exc:
        raise ConfigError(f"Invalid arguments for {name}: {exc}") from exc
    return function(**arguments)


def error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ConfigError) or hasattr(exc, "code"):
        error = {"code": str(exc.code), "message": str(getattr(exc, "message", str(exc)))}
        details = getattr(exc, "details", None)
        if details:
            error["details"] = details
    elif isinstance(exc, (ValueError, TypeError)):
        error = {"code": "invalid_input", "message": str(exc)}
    elif isinstance(exc, OSError):
        error = {"code": "filesystem_error", "message": "Filesystem operation failed", "errno": exc.errno}
    else:
        error = {"code": "internal_error", "message": "Engine operation failed; no success was recorded"}
    return {"ok": False, "error": error}


def parse_arguments(raw: str) -> dict[str, Any]:
    if raw == "-":
        raw = sys.stdin.read(131073)
    if len(raw.encode("utf-8")) > 131072:
        raise ConfigError("Arguments exceed 131072 bytes")
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError("Arguments must contain valid JSON") from exc
    if not isinstance(arguments, dict):
        raise ConfigError("Arguments must be a JSON object")
    return arguments


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retulo", description=__doc__)
    parser.add_argument("--version", action="version", version=f"retulo {__version__}")
    parser.add_argument("--config", help="Configuration path (default: RETULO_CONFIG or ~/.config/retulo/config.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-config", help="Explicitly create configuration; never overwrite an existing file")
    init.add_argument("--workspace-home")
    init.add_argument("--state-home")
    init.add_argument("--allow-source-root", action="append", default=[])
    init.add_argument("--allow-workspace-root", action="append", default=[])
    init.add_argument("--allow-git-worktree-root", action="append", default=[],
                      help="Explicit parent directory for Retulo-managed Git worktrees (disabled by default)")
    init.add_argument("--json", action="store_true")

    call = sub.add_parser("call", help="Call one engine method with structured JSON arguments")
    call.add_argument("name", choices=sorted(METHODS))
    call.add_argument("--arguments", default="{}", help="JSON object, or '-' to read JSON from stdin")
    call.add_argument("--json", action="store_true")

    for name, help_text in (("verify", "Verify registered files without declaring content approval"),
                            ("get-context", "Read current state and generated guidance"),
                            ("resume", "Inspect an existing task; this shortcut does not mutate bindings")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--task-id", required=True)
        command.add_argument("--json", action="store_true")
        if name in ("get-context", "resume"):
            command.add_argument("--detail", choices=("compact", "full"), default="compact",
                                 help="Read the compact summary by default; request full state explicitly")
    records = sub.add_parser("read-records", help="Read one bounded task record section at a pinned revision")
    records.add_argument("--task-id", required=True)
    records.add_argument("--section", choices=RECORD_SECTIONS, required=True)
    records.add_argument("--offset", type=int, default=0)
    records.add_argument("--limit", type=int, default=20)
    records.add_argument("--expected-revision", type=int)
    records.add_argument("--json", action="store_true")
    for name in ("list", "capabilities"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true")
    for name, help_text in (("setup", "Connect Retulo to an agent; preview changes before --apply"),
                            ("doctor", "Check configuration and optionally test the local MCP connection"),
                            ("disconnect", "Remove the agent connection while keeping Retulo workspaces")):
        command = sub.add_parser(name, help=help_text,
            epilog=f"Example: retulo {name} codex --project /path/to/project" +
                   (" --connect" if name == "doctor" else " --apply"))
        command.add_argument("agent", choices=("codex", "claude"), help="Agent client")
        command.add_argument("--project", help="Project directory (default: current directory; home/root rejected)")
        command.add_argument("--config", default=argparse.SUPPRESS,
                             help="Retulo configuration path; also accepted before the command")
        command.add_argument("--workspace-home", help="Workspace storage (default: existing configuration or ~/Documents/RetuloWorkspaces)")
        command.add_argument("--codex-home", help="Codex settings directory (default: ~/.codex)")
        command.add_argument("--skills-home", help="Codex skill directory (default: ~/.agents/skills)")
        command.add_argument("--snapshot-home", help="Installation backup directory")
        command.add_argument("--json", action="store_true", help="Emit the structured JSON result (default)")
        if name == "doctor":
            command.add_argument("--connect", action="store_true", help="Test Retulo MCP locally without calling a model")
        else:
            command.add_argument("--apply", action="store_true", help="Apply the previewed changes")
        if name == "disconnect":
            command.add_argument("--snapshot", help="Use a specific installation backup")
    sub.add_parser("mcp", help="Serve the official MCP SDK stdio endpoint")
    sub.add_parser("codex-hook", help="Emit bounded read-only context for supported Codex hook events")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.command == "codex-hook":
        from .harness import run_codex_hook
        return run_codex_hook(args.config)
    try:
        if args.command in ("setup", "doctor", "disconnect"):
            from . import installation
            options = {name: getattr(args, name) for name in
                       ("project", "config", "workspace_home", "codex_home", "skills_home", "snapshot_home")}
            if args.command == "doctor":
                result = installation.doctor(args.agent, connect=args.connect, **options)
            elif args.command == "disconnect":
                result = installation.disconnect(args.agent, apply=args.apply, snapshot=args.snapshot, **options)
            else:
                result = installation.setup(args.agent, apply=args.apply, **options)
        elif args.command == "init-config":
            result = init_config(args.config, workspace_home=args.workspace_home, state_home=args.state_home,
                                 allowed_source_roots=args.allow_source_root,
                                 allowed_workspace_roots=args.allow_workspace_root,
                                 allowed_git_worktree_roots=args.allow_git_worktree_root)
        else:
            config = load_config(args.config)
            engine = build_engine(config)
            if args.command == "mcp":
                from .mcp import build_server
                build_server(engine).run(transport="stdio")
                return 0
            if args.command == "call":
                result = dispatch(engine, args.name, parse_arguments(args.arguments))
            else:
                method = {"verify": "verify", "get-context": "get_context", "resume": "resume_task",
                          "list": "list_tasks", "capabilities": "capabilities",
                          "read-records": "read_records"}[args.command]
                parameters = {"task_id": args.task_id} if hasattr(args, "task_id") else {}
                if args.command in ("get-context", "resume"):
                    parameters["detail"] = args.detail
                elif args.command == "read-records":
                    parameters.update(section=args.section, offset=args.offset, limit=args.limit,
                                      expected_revision=args.expected_revision)
                result = dispatch(engine, method, parameters)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        return 0 if not isinstance(result, dict) or result.get("ok", True) else 1
    except Exception as exc:
        payload = json.dumps(error_payload(exc), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        print(payload, file=sys.stderr if args.command == "mcp" else sys.stdout)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
