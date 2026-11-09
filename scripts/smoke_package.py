#!/usr/bin/env python3
"""Smoke-test an installed wheel using synthetic settings outside the checkout.

Run with the source environment's Python after building a wheel::

    .venv/bin/python scripts/smoke_package.py --wheel dist/retulo-VERSION-py3-none-any.whl

The temporary runtime installs the wheel and its declared dependencies with pip.
It never imports Retulo from the checkout, invokes an AI client, or calls a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import venv

from retulo.cli import METHODS


PACKAGE_PROBE = r'''
import importlib.metadata
import importlib.resources
import json
from pathlib import Path
import sys

import jsonschema
import mcp
import retulo
import retulo.git_backend
import retulo.git_workspaces
import retulo.git_snapshot

runtime = Path(sys.prefix).resolve()
module = Path(retulo.__file__).resolve()
assert module.is_relative_to(runtime), f"Retulo was imported outside the runtime: {module}"
assert "site-packages" in module.parts, f"Retulo was not installed: {module}"
for dependency in (mcp, jsonschema):
    path = Path(dependency.__file__).resolve()
    assert path.is_relative_to(runtime), f"Dependency was imported outside the runtime: {path}"
resources = importlib.resources.files("retulo")
names = (
    "resources/codex/AGENTS.md", "resources/codex/SKILL.md",
    "resources/claude/CLAUDE.md", "schemas/task.schema.json",
    "schemas/task-v2.schema.json", "schemas/workflow.schema.json",
    "schemas/recipe.schema.json",
)
for name in names:
    item = resources.joinpath(name)
    assert item.read_bytes(), f"Packaged resource is empty or missing: {name}"
    assert Path(str(item)).resolve().is_relative_to(runtime), name
direct = importlib.metadata.distribution("retulo").read_text("direct_url.json")
assert direct and not json.loads(direct).get("dir_info", {}).get("editable"), direct
print(json.dumps({"module": str(module), "python": sys.executable,
                  "version": importlib.metadata.version("retulo"),
                  "resources": list(names), "editable": False}))
'''


MCP_VERIFY = r'''
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from retulo.cli import METHODS

async def main():
    payload = json.load(sys.stdin)
    results = {}
    assert {"git_status", "git_create_worktree", "git_record_snapshot", "git_integrate", "git_remove_worktree"} <= METHODS
    for agent, server in payload["servers"].items():
        params = StdioServerParameters(command=server["command"], args=server["args"],
                                      cwd=payload["project"], env={"PYTHONPATH": payload["poison_path"]})
        async with asyncio.timeout(20):
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    initialized = await client.initialize()
                    assert initialized.server_info.name == "retulo"
                    listed = await client.list_tools()
                    assert {tool.name for tool in listed.tools} == METHODS
                    verified = await client.call_tool("verify", {"task_id": payload["task_id"]})
                    assert not verified.is_error, verified
                    result = verified.structured_content
                    if result is None:
                        result = json.loads(next(item.text for item in verified.content
                                                 if getattr(item, "type", None) == "text"))
                    assert result["ok"], result
                    git_state = await client.call_tool("git_status", {"task_id": payload["task_id"]})
                    assert not git_state.is_error, git_state
                    assert git_state.structured_content["status"] == "unbound", git_state
                    results[agent] = {"server_name": initialized.server_info.name,
                                      "tool_count": len(listed.tools), "verify_ok": True}
    print(json.dumps(results))

asyncio.run(main())
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def tree(root: Path) -> dict[str, tuple]:
    """Include empty directories and modes when checking read-only operations."""
    result = {}
    for path in sorted(root.rglob("*")):
        mode = stat.S_IMODE(path.lstat().st_mode)
        key = str(path.relative_to(root))
        if path.is_symlink():
            result[key] = ("symlink", mode, os.readlink(path))
        elif path.is_dir():
            result[key] = ("directory", mode)
        else:
            result[key] = ("file", mode, hashlib.sha256(path.read_bytes()).hexdigest())
    return result


class Runtime:
    def __init__(self, root: Path):
        self.root = root
        self.cwd = root / "unrelated-cwd"
        self.path = root / "installed-runtime"
        self.cwd.mkdir()
        self.python = self.path / "bin/python"
        self.executable = self.path / "bin/retulo"
        self.environment = os.environ.copy()
        for name in ("PYTHONPATH", "PYTHONHOME", "RETULO_CONFIG",
                     "PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
            self.environment.pop(name, None)
        self.environment.update(VIRTUAL_ENV=str(self.path),
                                XDG_CONFIG_HOME=str(root / "xdg-config"),
                                XDG_CACHE_HOME=str(root / "xdg-cache"),
                                XDG_STATE_HOME=str(root / "xdg-state"),
                                PIP_CACHE_DIR=str(root / "pip-cache"),
                                PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
                                PATH=str(self.path / "bin") + os.pathsep + os.environ.get("PATH", ""))

    def run(self, arguments, payload=None, *, timeout=60, structured=True, cwd=None, environment=None):
        command = [str(item) for item in arguments]
        env = {**self.environment, **(environment or {})}
        result = subprocess.run(command, cwd=cwd or self.cwd, env=env,
                                input=json.dumps(payload) if payload is not None else None,
                                text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n"
                               f"{result.stdout}\n{result.stderr}")
        if not structured:
            return result.stdout
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Command did not return JSON: {' '.join(command)}\n"
                               f"{result.stdout}\n{result.stderr}") from exc

    def cli(self, arguments, payload=None):
        return self.run([self.executable, *arguments], payload)


def exercise(wheel: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="retulo-wheel-smoke-") as directory:
        root = Path(directory).resolve()
        runtime = Runtime(root)
        # Copy the only release input first; every subsequent command runs elsewhere.
        wheel_copy = root / wheel.name
        shutil.copy2(wheel, wheel_copy)
        wheel_sha256 = hashlib.sha256(wheel_copy.read_bytes()).hexdigest()
        venv.EnvBuilder(with_pip=True).create(runtime.path)
        runtime.run([runtime.python, "-I", "-m", "pip", "install", "--no-input",
                     "--no-cache-dir", wheel_copy], timeout=180, structured=False)
        require(runtime.executable.is_file(), "The wheel did not install the retulo executable")
        package = runtime.run([runtime.python, "-I", "-c", PACKAGE_PROBE])

        fixture = root / "fixture"
        project, codex = fixture / "project with spaces", fixture / "codex"
        skills, config = fixture / "skills", fixture / "config.json"
        workspaces = fixture / "workspaces"
        snapshots = {agent: fixture / "snapshots" / agent for agent in ("codex", "claude")}
        project.mkdir(parents=True)
        codex.mkdir()
        originals = {
            codex / "config.toml": b'model = "preserved-model"\n[mcp_servers.other]\ncommand = "unchanged"\n',
            codex / "AGENTS.md": b"Preserved synthetic user instructions.\n",
            codex / "hooks.json": b'{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"true"}]}]}}\n',
            project / ".mcp.json": b'{"mcpServers":{"other":{"command":"unchanged"}},"userSetting":true}\n',
            project / "CLAUDE.md": b"Preserved synthetic project instructions.\n",
            skills / "unrelated/SKILL.md": b"Preserved unrelated skill.\n",
        }
        for path, raw in originals.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

        def command(operation, agent, *extra):
            return [operation, agent, "--project", project, "--config", config,
                    "--workspace-home", workspaces, "--codex-home", codex,
                    "--skills-home", skills, "--snapshot-home", snapshots[agent], *extra]

        setups = {}
        for agent in ("codex", "claude"):
            before = tree(fixture)
            preview = runtime.cli(command("setup", agent))
            require(preview["ok"] and preview["status"] == "preview", f"{agent} setup preview failed")
            require(tree(fixture) == before, f"{agent} setup preview changed files or directories")
            applied = runtime.cli(command("setup", agent, "--apply"))
            require(applied["ok"] and applied["snapshot"], f"{agent} setup did not retain a backup")
            installed = tree(fixture)
            repeat = runtime.cli(command("setup", agent, "--apply"))
            require(repeat["snapshot"] is None and not any(row["changed"] for row in repeat["files"]),
                    f"{agent} repeated setup was not a no-op")
            require(tree(fixture) == installed, f"{agent} repeated setup changed files or directories")
            require(applied["host_adoption"] == "untested", f"{agent} setup claimed client adoption")
            setups[agent] = {"preview_read_only": True, "repeat_no_change": True}

        codex_settings = tomllib.loads((codex / "config.toml").read_text(encoding="utf-8"))
        claude_settings = json.loads((project / ".mcp.json").read_bytes())
        require(codex_settings["model"] == "preserved-model", "Codex model setting changed")
        require(codex_settings["mcp_servers"]["other"] == {"command": "unchanged"},
                "Codex unrelated MCP server changed")
        require(claude_settings["mcpServers"]["other"] == {"command": "unchanged"}
                and claude_settings["userSetting"] is True, "Claude unrelated settings changed")
        require((codex / "AGENTS.md").read_bytes().startswith(originals[codex / "AGENTS.md"]),
                "Codex user instructions changed")
        require((project / "CLAUDE.md").read_bytes().startswith(originals[project / "CLAUDE.md"]),
                "Claude project instructions changed")
        hooks = json.loads((codex / "hooks.json").read_bytes())
        require(hooks["hooks"]["Stop"] == json.loads(originals[codex / "hooks.json"])["hooks"]["Stop"],
                "Unrelated Codex hooks changed")
        require((skills / "unrelated/SKILL.md").read_bytes() == originals[skills / "unrelated/SKILL.md"],
                "An unrelated skill changed")
        servers = {"codex": codex_settings["mcp_servers"]["retulo"],
                   "claude": claude_settings["mcpServers"]["retulo"]}
        for agent, server in servers.items():
            require(server["command"] == str(runtime.python), f"{agent} saved the wrong Python runtime")
            require(server["args"] == ["-I", "-m", "retulo", "--config", str(config), "mcp"],
                    f"{agent} saved an unexpected MCP command")

        # A saved Python command must not import code supplied by the project or
        # by PYTHONPATH. Run the installed entry script in isolated mode for this
        # adversarial case; ordinary CLI invocations above/below use its shebang.
        poison_path = project / "poison-path"
        poison_path.mkdir()
        shadow_marker = fixture / "project-code-was-executed"
        poison = ("from pathlib import Path\n"
                  f"Path({str(shadow_marker)!r}).write_text('unexpected project import')\n"
                  "raise RuntimeError('Project code shadowed the installed Retulo package')\n")
        for directory in (project, poison_path):
            (directory / "retulo.py").write_text(poison, encoding="utf-8")
        doctors = {}
        for agent in ("codex", "claude"):
            diagnosed = runtime.run([runtime.python, "-I", runtime.executable,
                                    *command("doctor", agent, "--connect")],
                                   cwd=project, environment={"PYTHONPATH": str(poison_path)})
            transport = diagnosed["transport"]
            require(diagnosed["ok"] and diagnosed["configured"] and transport["status"] == "healthy"
                    and transport["tool_count"] == len(METHODS) and transport["server_name"] == "retulo",
                    f"{agent} installed MCP doctor failed: {diagnosed}")
            require(diagnosed["host_adoption"] == "untested", f"{agent} doctor claimed client adoption")
            doctors[agent] = {"status": transport["status"], "tool_count": transport["tool_count"],
                              "host_adoption": diagnosed["host_adoption"]}
        require(not shadow_marker.exists(), "Doctor executed project-supplied Python code")

        def call(name, arguments):
            return runtime.cli(["--config", config, "call", name, "--arguments", "-"], arguments)

        task = call("begin_task", {"start_request_id": "wheel-smoke-start", "title": "Visitor guide",
                    "goal": "Save and resume a deterministic guide from the installed wheel",
                    "completion_criteria": [{"id": "saved", "text": "Verified guide saved"}]})
        task_id, revision = task["task_id"], task["revision"]
        workspace = Path(task["workspace_path"])
        require(workspace.is_relative_to(workspaces), "Task workspace escaped the synthetic workspace home")
        preserved = {}
        for version in (1, 2):
            if version == 2:
                resumed = call("resume_task", {"task_id": task_id, "operation_id": "wheel-smoke-reopen",
                    "expected_revision": revision, "reopen": True, "reason": "Synthetic requested update",
                    "binding": {"provider": "smoke", "conversation_id": "second-cli-process"}})
                require(resumed["task_id"] == task_id, "Resume changed the task UUID")
                revision = resumed["revision"]
            reserved = call("reserve_output", {"task_id": task_id, "operation_id": f"reserve-{version}",
                "expected_revision": revision, "name": "guide.md"})
            output = Path(reserved["write_path"])
            require(output.is_relative_to(workspace), "Reserved output escaped the synthetic task workspace")
            text = "# Visitor guide\n\nSign in at reception.\n"
            if version == 2:
                text += "Ask reception for assistance.\n"
            output.write_text(text, encoding="utf-8")
            committed = call("commit_artifact", {"task_id": task_id,
                "operation_id": f"commit-{version}", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            artifact = committed["artifact"]
            artifact_path = workspace / artifact["path"]
            require(artifact_path.read_text(encoding="utf-8") == text, "Committed artifact bytes differ")
            require(call("verify", {"task_id": task_id})["ok"], "Committed artifact verification failed")
            for path, checksum in preserved.items():
                require(hashlib.sha256(path.read_bytes()).hexdigest() == checksum,
                        "An earlier artifact version was overwritten")
            preserved[artifact_path] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            checked = call("checkpoint", {"task_id": task_id, "operation_id": f"check-{version}",
                "expected_revision": committed["revision"], "changes": {"criteria_results": [
                    {"criterion": "saved", "status": "passed",
                     "evidence_ref": f"artifact:{artifact['artifact_id']}:{artifact['revision']}"}]}})
            done = call("complete_task", {"task_id": task_id, "operation_id": f"complete-{version}",
                "expected_revision": checked["revision"]})
            require(done["status"] == "completed", "Task did not complete")
            revision = done["revision"]
        require(len(preserved) == 2, "Updated output did not retain two immutable paths")
        wire = runtime.run([runtime.python, "-I", "-c", MCP_VERIFY],
                           {"servers": servers, "task_id": task_id, "project": str(project),
                            "poison_path": str(poison_path)})
        require(not shadow_marker.exists(), "MCP verification executed project-supplied Python code")

        core_before = config.read_bytes()
        workspace_before = tree(workspaces)
        disconnected = {}
        for agent in ("claude", "codex"):
            before = tree(fixture)
            preview = runtime.cli(command("disconnect", agent))
            require(preview["ok"], f"{agent} disconnect preview failed")
            require(tree(fixture) == before, f"{agent} disconnect preview changed files or directories")
            result = runtime.cli(command("disconnect", agent, "--apply"))
            require(result["ok"] and result["configuration_preserved"] and result["workspaces_preserved"],
                    f"{agent} disconnect failed")
            require(config.read_bytes() == core_before, f"{agent} disconnect changed core configuration")
            require(tree(workspaces) == workspace_before, f"{agent} disconnect changed task workspaces")
            after = tree(fixture)
            repeat = runtime.cli(command("disconnect", agent, "--apply"))
            require(repeat["status"] == "already_disconnected", f"{agent} repeated disconnect was not a no-op")
            require(tree(fixture) == after, f"{agent} repeated disconnect changed files or directories")
            disconnected[agent] = {"preview_read_only": True, "repeat_no_change": True}
        for path, raw in originals.items():
            require(path.read_bytes() == raw, f"Original settings were not restored: {path.name}")
        require(not (skills / "retulo/SKILL.md").exists(), "Managed Codex skill remains after disconnect")
        require(call("verify", {"task_id": task_id})["ok"], "Task integrity failed after disconnection")
        require(tree(workspaces) == workspace_before, "Post-disconnection verification changed task files")
        for path, checksum in preserved.items():
            require(hashlib.sha256(path.read_bytes()).hexdigest() == checksum,
                    "An artifact was lost after disconnect")
        return {"ok": True, "wheel": wheel.name, "wheel_sha256": wheel_sha256,
                "installed_package": package, "source_checkout_imported": False,
                "project_module_shadowing_ignored": True, "pythonpath_shadowing_ignored": True,
                "setup": setups, "doctor": doctors, "stdio_mcp": wire,
                "task_id": task_id, "completed_versions": len(preserved), "same_task_resumed": True,
                "disconnect": disconnected, "original_settings_restored": True,
                "core_configuration_preserved": True, "workspaces_preserved": True,
                "artifacts_verified_after_disconnect": True, "model_calls": 0,
                "host_settings_modified": False, "host_adoption": "untested"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path, help="Built Retulo wheel to install and exercise")
    args = parser.parse_args()
    wheel = args.wheel.expanduser().resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        parser.error("--wheel must name an existing .whl file")
    try:
        summary = exercise(wheel)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
