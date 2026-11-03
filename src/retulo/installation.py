"""Local setup, diagnosis and disconnection for supported agent clients."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
import importlib.resources
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tomllib
from typing import Any

from . import __version__


class InstallationError(ValueError):
    code = "installation_error"

    def __init__(self, message: str, **details: Any):
        super().__init__(message)
        self.message, self.details = message, details


def _path(value: str | Path, label: str) -> Path:
    """Normalize spelling only after inspecting the supplied path for symlinks."""
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        raise InstallationError(f"{label} must be a nonempty local path")
    expanded = Path(value).expanduser()
    if ".." in expanded.parts:
        raise InstallationError(f"{label} must not contain '..'; use its direct path")
    candidate = expanded if expanded.is_absolute() else Path.cwd() / expanded
    for node in (candidate, *candidate.parents):
        if node.is_symlink():
            raise InstallationError(f"{label} must not contain symbolic links", path=str(candidate))
    return Path(os.path.abspath(candidate))


def _project(value: str | Path | None) -> Path:
    path = _path(value if value is not None else Path.cwd(), "project")
    home = Path(os.path.abspath(Path.home()))
    if path == Path(path.anchor) or path == home or path in home.parents:
        raise InstallationError("Choose a project directory below your home or another specific working directory",
                                path=str(path), action="Pass --project /path/to/your/project")
    if not path.is_dir():
        raise InstallationError("Project must be an existing directory", path=str(path))
    if not os.access(path, os.R_OK | os.X_OK):
        raise InstallationError("Project directory is not readable", path=str(path))
    return path


def _installation_action() -> str:
    return (f"Install with https://github.com/TheStack-ai/retulo/releases/download/v{__version__}/install.sh, "
            "then run retulo setup again")


@lru_cache(maxsize=8)
def _installed_runtime(runtime: str, version: str, module: str) -> None:
    """Verify this interpreter can load this installed package without cwd/PYTHONPATH."""
    program = ("import json,retulo,retulo.mcp; "
               "print(json.dumps({'version':retulo.__version__,'module':retulo.__file__}))")
    try:
        completed = subprocess.run([runtime, "-I", "-B", "-c", program], capture_output=True,
                                   text=True, timeout=5, check=False)
        if completed.returncode != 0 or len(completed.stdout) > 8192:
            raise ValueError("Isolated package import failed")
        observed = json.loads(completed.stdout)
        if (not isinstance(observed, dict) or observed.get("version") != version or
                not isinstance(observed.get("module"), str) or
                Path(observed["module"]).resolve() != Path(module)):
            raise ValueError("Isolated package identity differs")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise InstallationError("This Python command cannot load the installed Retulo package in isolation",
                                interpreter=runtime, action=_installation_action()) from exc


def _runtime() -> str:
    # Keep the invoked venv/tool path: resolving its Python symlink loses persistence identity.
    value = os.path.abspath(sys.executable)
    folded = value.replace("\\", "/").lower()
    if (any(part in folded for part in ("/.cache/uv/archive-", "/caches/uv/archive-", "/uv/cache/archive-")) or
            any(re.fullmatch(r"archive-v[0-9]+", part) for part in Path(value).parts)):
        raise InstallationError("This temporary uvx runtime cannot be saved as a durable agent command",
                                action=_installation_action(), interpreter=value)
    if not Path(value).is_file() or not os.access(value, os.X_OK):
        raise InstallationError("The current Python interpreter is unavailable", interpreter=value)
    _installed_runtime(value, __version__, str(Path(__file__).with_name("__init__.py").resolve()))
    return value


@dataclass(frozen=True)
class Locations:
    project: Path
    engine_config: Path
    workspace_home: Path
    codex_home: Path
    skills_home: Path
    snapshot_home: Path


def locations(agent: str, *, project: str | Path | None = None,
              config: str | Path | None = None, workspace_home: str | Path | None = None,
              codex_home: str | Path | None = None, skills_home: str | Path | None = None,
              snapshot_home: str | Path | None = None) -> Locations:
    from .cli import default_config_path, default_workspace_home, load_config
    if agent not in ("codex", "claude"):
        raise InstallationError("Choose codex or claude")
    selected_project = _project(project)
    config_path = _path(config if config is not None else default_config_path(), "config")
    existing = load_config(config_path) if config_path.exists() else None
    chosen_workspace = workspace_home if workspace_home is not None else (
        existing["workspace_home"] if existing else default_workspace_home())
    workspace = _path(chosen_workspace, "workspace-home")
    if workspace == Path(workspace.anchor) or workspace == Path.home():
        raise InstallationError("workspace-home must be a specific workspace directory", path=str(workspace))
    codex = _path(codex_home if codex_home is not None else Path.home() / ".codex", "codex-home")
    skills = _path(skills_home if skills_home is not None else Path.home() / ".agents/skills", "skills-home")
    snapshots_default = Path.home() / ".local/state/retulo/install"
    if agent == "claude":
        snapshots_default /= "claude"
    snapshots = _path(snapshot_home if snapshot_home is not None else snapshots_default, "snapshot-home")
    for label, path in (("workspace-home", workspace), ("codex-home", codex),
                        ("skills-home", skills), ("snapshot-home", snapshots)):
        if path.exists() and not path.is_dir():
            raise InstallationError(f"{label} must be a directory", path=str(path))
    if existing:
        for field in ("workspace_home", "state_home"):
            if field in existing:
                _path(existing[field], field)
        for field in ("allowed_source_roots", "allowed_workspace_roots"):
            for root in existing[field]:
                _path(root, field)
    return Locations(selected_project, config_path, workspace, codex, skills, snapshots)


def _backend_error(exc: Exception) -> InstallationError:
    details = getattr(exc, "details", {})
    return InstallationError(str(exc), **(details if isinstance(details, dict) else {}))


def setup(agent: str, *, apply: bool = False, **options: Any) -> dict[str, Any]:
    target = locations(agent, **options)
    runtime = _runtime()
    try:
        if agent == "codex":
            from .integrations import codex
            _codex_document(_read(target.codex_home / "config.toml"))
            result = codex.install(repo=None, codex_home=target.codex_home,
                                   engine_config=target.engine_config, skills_home=target.skills_home,
                                   workspace_home=target.workspace_home, source_roots=[target.project],
                                   snapshot_home=target.snapshot_home, apply=apply,
                                   runtime_python=runtime, extend_source_roots=True)
        else:
            from .integrations import claude
            result = claude.install(project=target.project, engine_config=target.engine_config,
                                    workspace_home=target.workspace_home, snapshot_home=target.snapshot_home,
                                    apply=apply, runtime_python=runtime)
    except (OSError, ValueError) as exc:
        raise _backend_error(exc) from exc
    return {**result, "ok": True, "integration": agent, "apply": apply,
            "status": "configured" if apply else "preview", "project": str(target.project),
            "config_path": str(target.engine_config), "workspace_home": str(target.workspace_home),
            "next_action": ("Restart the agent client and review its Retulo connection approval" if apply else
                            "Review the listed files, then repeat with --apply"),
            "host_adoption": "untested"}


def _read(path: Path, maximum: int = 1048576) -> bytes | None:
    _path(path, "settings file")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise InstallationError("Settings must be a bounded regular file", path=str(path))
        raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise InstallationError("Settings file is too large", path=str(path))
        return raw


def _unique_json(raw: bytes) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise InstallationError("Settings contain duplicate JSON keys")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def _resource(agent: str, filename: str) -> bytes:
    return importlib.resources.files("retulo").joinpath("resources", agent, filename).read_bytes()


def _expected_server(agent: str, config: Path, runtime: str) -> dict[str, Any]:
    value = {"command": runtime, "args": ["-I", "-m", "retulo", "--config", str(config), "mcp"]}
    if agent == "claude":
        value["type"] = "stdio"
    else:
        value.update(startup_timeout_sec=20, tool_timeout_sec=120)
    return value


def _codex_document(raw: bytes | None) -> dict[str, Any]:
    value = tomllib.loads(raw.decode("utf-8")) if raw is not None else {}
    for field in ("mcp_servers", "features"):
        if not isinstance(value.get(field, {}), dict):
            raise InstallationError(f"Codex {field} must be a table; existing settings were kept")
    return value


def _codex_components(target: Locations, runtime: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from .integrations import codex
    raw = _read(target.codex_home / "config.toml")
    parsed = _codex_document(raw)
    server = parsed.get("mcp_servers", {}).get("retulo")
    expected = _expected_server("codex", target.engine_config, runtime)
    agents = _read(target.codex_home / "AGENTS.md")
    guidance, _ = codex.agents_component(agents)
    template = _resource("codex", "AGENTS.md").rstrip(b"\r\n")
    skill = _read(target.skills_home / "retulo/SKILL.md")
    hooks_raw = _read(target.codex_home / "hooks.json")
    owned, candidate, _, _ = codex.hook_components(hooks_raw, runtime, target.engine_config)
    components = {"mcp": server == expected,
                  "guidance": guidance == template,
                  "skill": skill == _resource("codex", "SKILL.md"),
                  "hooks": owned == candidate and parsed.get("features", {}).get("hooks") is not False}
    return components, server


def _claude_components(target: Locations, runtime: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from .integrations import claude
    status = claude.status(project=target.project, engine_config=target.engine_config,
                           workspace_home=target.workspace_home, snapshot_home=target.snapshot_home)
    raw = _read(target.project / ".mcp.json")
    parsed = _unique_json(raw) if raw is not None else {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("mcpServers", {}), dict):
        raise InstallationError(".mcp.json must contain an mcpServers object")
    server = parsed.get("mcpServers", {}).get("retulo")
    guidance = _read(target.project / "CLAUDE.md") or b""
    template = _resource("claude", "CLAUDE.md").rstrip(b"\r\n")
    return {"mcp": server == _expected_server("claude", target.engine_config, runtime),
            "guidance": guidance.count(b"<!-- retulo:claude:start -->") == 1 and
                        guidance.count(b"<!-- retulo:claude:end -->") == 1 and template in guidance,
            "managed": status.get("managed") is True,
            "ready": status.get("ready") is True,
            "conflicts_clear": not status.get("conflicts")}, server


def _accessible_directory(path: Path) -> bool:
    if path.exists():
        return path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK)
    parent = next((node for node in path.parents if node.exists()), None)
    return parent is not None and parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)


async def _probe_mcp(server: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from .cli import METHODS
    # This private helper only receives the exact expected command from doctor.
    async with asyncio.timeout(timeout):
        parameters = StdioServerParameters(command=server["command"], args=server["args"])
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                if names != METHODS:
                    raise InstallationError("The MCP tool list differs from this Retulo version")
                result = await session.call_tool("capabilities", {})
                if result.is_error:
                    raise InstallationError("Retulo MCP capabilities failed")
                structured = result.structured_content
                if structured is None:
                    structured = _unique_json(next(item.text.encode() for item in result.content
                                                   if getattr(item, "type", None) == "text"))
                if not isinstance(structured, dict) or structured.get("protocol_version") != 1:
                    raise InstallationError("Retulo MCP returned invalid capabilities")
                return {"status": "healthy", "tool_count": len(names),
                        "server_name": initialized.server_info.name, "capabilities": structured}


def doctor(agent: str, *, connect: bool = False, **options: Any) -> dict[str, Any]:
    target = locations(agent, **options)
    checks: list[dict[str, Any]] = []
    def check(name: str, passed: bool, message: str, **extra: Any) -> None:
        checks.append({"name": name, "ok": bool(passed), "message": message, **extra})
    runtime = None
    try:
        runtime = _runtime()
        check("runtime", True, "Python command is available", path=runtime)
    except InstallationError as exc:
        check("runtime", False, str(exc), **exc.details)
    from .cli import load_config
    try:
        config = load_config(target.engine_config)
        if Path(config["workspace_home"]) != target.workspace_home:
            raise InstallationError("The configured workspace differs from --workspace-home")
        roots = [_path(root, "allowed_source_roots") for root in config["allowed_source_roots"]]
        authorized = any(target.project == root or root in target.project.parents for root in roots)
        check("config", True, "Retulo configuration is valid", path=str(target.engine_config))
        check("project_scope", authorized, "Project is allowed" if authorized else
              "Run setup for this project to add its directory", path=str(target.project))
        check("workspace_access", _accessible_directory(target.workspace_home),
              "Workspace directory or its existing parent must be writable", path=str(target.workspace_home))
        if config.get("state_home"):
            check("state_access", _accessible_directory(_path(config["state_home"], "state_home")),
                  "State directory or its existing parent must be writable", path=config["state_home"])
    except (OSError, ValueError) as exc:
        check("config", False, str(exc), path=str(target.engine_config))
    check("project_access", os.access(target.project, os.R_OK | os.X_OK),
          "Project must be readable", path=str(target.project))
    try:
        for filename in (("AGENTS.md", "SKILL.md") if agent == "codex" else ("CLAUDE.md",)):
            if not _resource(agent, filename):
                raise InstallationError("A packaged instruction file is empty")
        if importlib.util.find_spec("retulo.mcp") is None or importlib.util.find_spec("mcp") is None:
            raise InstallationError("The installed Retulo MCP dependencies are missing")
        check("package", True, "Retulo module and agent instructions are installed")
    except (OSError, ValueError, ModuleNotFoundError) as exc:
        check("package", False, str(exc))
    server, configured = None, False
    if runtime:
        try:
            components, server = (_codex_components(target, runtime) if agent == "codex" else
                                  _claude_components(target, runtime))
            for name, valid in components.items():
                check(name, valid, "Installed entry matches Retulo" if valid else
                      "Entry is missing or changed; review setup preview")
            configured = all(components.values())
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            check("agent_settings", False, str(exc))
    transport: dict[str, Any] = {"status": "not_tested", "action": "Add --connect to test the local MCP connection"}
    if connect:
        if not configured or server != _expected_server(agent, target.engine_config, runtime or ""):
            transport = {"status": "not_started", "message": "Only the exact configured Retulo command can be tested"}
        elif not all(item["ok"] for item in checks):
            transport = {"status": "not_started", "message": "Fix the failed checks before testing MCP"}
        else:
            try:
                transport = asyncio.run(_probe_mcp(server))
            except Exception as exc:
                transport = {"status": "failed", "message": ("MCP connection timed out after 15 seconds" if
                             isinstance(exc, TimeoutError) else "MCP connection failed; review the Retulo configuration")}
            check("transport", transport["status"] == "healthy", transport.get("message", "Local MCP connection is healthy"))
    ok = all(item["ok"] for item in checks) and (not connect or transport["status"] == "healthy")
    return {"ok": ok, "integration": agent, "project": str(target.project),
            "config_path": str(target.engine_config), "workspace_home": str(target.workspace_home),
            "configured": configured, "transport": transport, "host_adoption": "untested",
            "host_trust": "Review approval in the agent client", "checks": checks,
            "next_action": "Restart the agent client and review its connection approval" if ok else
                           "Fix the failed checks, then repeat doctor"}


def _codex_owned(config_raw: bytes | None, agents_raw: bytes | None, hooks_raw: bytes | None) -> bool:
    from .integrations import codex
    parsed = _codex_document(config_raw)
    if "retulo" in parsed.get("mcp_servers", {}):
        return True
    guidance, _ = codex.agents_component(agents_raw)
    if guidance is not None:
        return True
    value = _unique_json(hooks_raw) if hooks_raw is not None else {}
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise InstallationError("Hooks configuration must contain an object")
    for groups in value.get("hooks", {}).values():
        if not isinstance(groups, list):
            raise InstallationError("Hook event handlers must be a list")
        if any(codex._managed_hook_group(group) for group in groups):
            return True
    return False


def _codex_present(target: Locations) -> bool:
    return _codex_owned(_read(target.codex_home / "config.toml"),
                        _read(target.codex_home / "AGENTS.md"), _read(target.codex_home / "hooks.json"))


def _disconnect_chain(target: Locations, snapshot: str | Path | None) -> list[Path]:
    """Stop at the first validated prior state with no owned agent entries."""
    from .integrations import codex_restore
    expected = {str(target.codex_home / "config.toml"), str(target.engine_config),
                str(target.skills_home / "retulo/SKILL.md"), str(target.codex_home / "AGENTS.md"),
                str(target.codex_home / "hooks.json"), str(target.snapshot_home / "managed-skill.json"),
                str(target.snapshot_home / "managed-harness.json")}
    candidates: list[Path] = []
    if snapshot is not None:
        candidates = [_path(snapshot, "snapshot")]
    elif target.snapshot_home.is_dir():
        children = list(target.snapshot_home.iterdir())
        if len(children) > 1024:
            raise InstallationError("Too many installation records to select safely; pass --snapshot")
        for directory in sorted(children, key=lambda path: path.name, reverse=True):
            _path(directory, "installation record")
            if not directory.is_dir():
                continue
            raw = _read(directory / "manifest.json")
            if raw is None:
                continue
            record = _unique_json(raw)
            if not isinstance(record, dict) or record.get("status") != "complete":
                continue
            rows = record.get("files")
            if isinstance(rows, list) and {row.get("path") for row in rows if isinstance(row, dict)} == expected:
                candidates.append(directory)
    if not candidates:
        raise InstallationError("No matching completed installation backup was found; existing settings were kept",
                                snapshot_home=str(target.snapshot_home), action="Locate the original --snapshot-home or review the agent settings manually")
    chain: list[Path] = []
    for candidate in candidates[:64]:
        chain.append(candidate)
        # Validate hashes, complete target sets and every intermediate state before reading the endpoint.
        codex_restore.restore_chain(chain, codex_home=target.codex_home, engine_config=target.engine_config,
            skills_home=target.skills_home, snapshot_home=target.snapshot_home, preserve_engine_config=True)
        record = _unique_json(_read(candidate / "manifest.json") or b"")
        rows = {row["path"]: row for row in record["files"]}
        def before(path: Path) -> bytes | None:
            row = rows[str(path)]
            return None if row["before_sha256"] is None else _read(_path(row["backup"], "backup"), 8 * 1024 * 1024)
        if not _codex_owned(before(target.codex_home / "config.toml"), before(target.codex_home / "AGENTS.md"),
                            before(target.codex_home / "hooks.json")):
            return chain
    raise InstallationError("The retained backups do not reach a fully disconnected state; existing settings were kept",
                            action="Review the original installation backup; no foreign edits were removed")


def disconnect(agent: str, *, apply: bool = False, snapshot: str | Path | None = None,
               **options: Any) -> dict[str, Any]:
    target = locations(agent, **options)
    try:
        if agent == "claude":
            from .integrations import claude
            result = claude.disconnect(project=target.project, engine_config=target.engine_config,
                                       workspace_home=target.workspace_home, snapshot_home=target.snapshot_home,
                                       apply=apply, snapshot=_path(snapshot, "snapshot") if snapshot else None)
        else:
            from .integrations import codex_restore
            if not _codex_present(target):
                result = {"apply": apply, "status": "already_disconnected", "files": []}
            else:
                selected = _disconnect_chain(target, snapshot)
                result = codex_restore.restore_chain(snapshots=selected, codex_home=target.codex_home,
                             engine_config=target.engine_config, skills_home=target.skills_home,
                             snapshot_home=target.snapshot_home, apply=apply, preserve_engine_config=True)
                if apply and _codex_present(target):
                    raise InstallationError("Some Retulo entries remain; review the retained snapshot chain")
    except (OSError, ValueError) as exc:
        raise _backend_error(exc) from exc
    return {**result, "ok": True, "integration": agent, "project": str(target.project),
            "config_path": str(target.engine_config), "apply": apply,
            "workspaces_preserved": True, "configuration_preserved": True,
            "next_action": "Restart the agent client" if apply else "Review the listed files, then repeat with --apply"}
