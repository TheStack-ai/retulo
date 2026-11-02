#!/usr/bin/env python3
"""Exercise a source install in temporary settings, without models or host changes."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / ".venv/bin/python"


def run(arguments, payload=None):
    result = subprocess.run([str(PYTHON), *map(str, arguments)], cwd=REPO,
                            input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


async def wire(server, task_id):
    params = StdioServerParameters(command=server["command"], args=server["args"], cwd=str(REPO))
    with open(os.devnull, "w") as errors:
        async with stdio_client(params, errlog=errors) as streams:
            async with ClientSession(*streams, read_timeout_seconds=15) as client:
                initialized = await client.initialize()
                assert initialized.server_info.name == "retulo"
                listed = await client.list_tools()
                assert len(listed.tools) == 24
                checked = await client.call_tool("verify", {"task_id": task_id})
                assert not checked.is_error and checked.structured_content["ok"]
    return len(listed.tools)


def main():
    if not PYTHON.is_file():
        raise RuntimeError("Create and install this source checkout's .venv first")
    with tempfile.TemporaryDirectory(prefix="retulo-smoke-") as directory:
        root = Path(directory).resolve()
        codex, config, skills = root / "codex", root / "engine.json", root / "skills"
        workspaces, snapshots = root / "workspaces", root / "snapshots"
        codex.mkdir()
        originals = {"config.toml": b'model = "preserved-model"\n[mcp_servers.other]\ncommand = "unchanged"\n',
                     "AGENTS.md": b"Preserved synthetic policy.\n",
                     "hooks.json": b'{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"true"}]}]}}\n'}
        for name, raw in originals.items():
            (codex / name).write_bytes(raw)
        flags = ["--codex-home", codex, "--engine-config", config, "--skills-home", skills,
                 "--workspace-home", workspaces, "--snapshot-home", snapshots]
        before = hashes(root)
        run(["scripts/install_codex.py", *flags])
        assert hashes(root) == before
        installed = run(["scripts/install_codex.py", *flags, "--apply"])
        after_install = hashes(root)
        repeat = run(["scripts/install_codex.py", *flags, "--apply"])
        assert repeat["snapshot"] is None and hashes(root) == after_install
        server = tomllib.loads((codex / "config.toml").read_text())["mcp_servers"]["retulo"]

        def call(name, args):
            return run(["-m", "retulo", "--config", config, "call", name, "--arguments", "-"], args)

        read_before = hashes(workspaces)
        for event in ("SessionStart", "UserPromptSubmit", "SubagentStart"):
            output = run(["-m", "retulo", "--config", config, "codex-hook"],
                         {"hook_event_name": event, "session_id": "synthetic-session",
                          "cwd": str(root), "prompt": "Explain version preservation.",
                          "agent_id": "synthetic-worker", "agent_type": "default"})
            assert "additionalContext" in output["hookSpecificOutput"]
        assert hashes(workspaces) == read_before
        task = call("begin_task", {"start_request_id": "synthetic-start", "title": "Visitor guide",
                    "goal": "Save and resume a synthetic guide",
                    "completion_criteria": [{"id": "saved", "text": "Verified guide saved"}]})
        task_id, revision = task["task_id"], task["revision"]
        workspace = Path(task["workspace_path"])
        preserved = {}
        for version in (1, 2):
            if version == 2:
                resumed = call("resume_task", {"task_id": task_id, "operation_id": "reopen",
                    "expected_revision": revision, "reopen": True, "reason": "Synthetic requested update",
                    "binding": {"provider": "smoke", "conversation_id": "second-process"}})
                assert resumed["task_id"] == task_id
                revision = resumed["revision"]
            reserved = call("reserve_output", {"task_id": task_id, "operation_id": f"reserve-{version}",
                "expected_revision": revision, "name": "guide.md"})
            text = "# Visitor guide\n\nSign in at reception.\n"
            if version == 2:
                text += "Ask reception for assistance.\n"
            Path(reserved["write_path"]).write_text(text)
            committed = call("commit_artifact", {"task_id": task_id,
                "operation_id": f"commit-{version}", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            artifact = committed["artifact"]
            path = workspace / artifact["path"]
            assert path.read_text() == text
            assert call("verify", {"task_id": task_id})["ok"]
            for old, checksum in preserved.items():
                assert hashlib.sha256(Path(old).read_bytes()).hexdigest() == checksum
            preserved[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            checked = call("checkpoint", {"task_id": task_id, "operation_id": f"check-{version}",
                "expected_revision": committed["revision"], "changes": {"criteria_results": [
                    {"criterion": "saved", "status": "passed",
                     "evidence_ref": f"artifact:{artifact['artifact_id']}:{artifact['revision']}"}]}})
            done = call("complete_task", {"task_id": task_id, "operation_id": f"complete-{version}",
                "expected_revision": checked["revision"]})
            assert done["status"] == "completed"
            revision = done["revision"]
        tool_count = asyncio.run(wire(server, task_id))
        restore = ["scripts/restore_codex.py", "--snapshot", installed["snapshot"],
                   "--codex-home", codex, "--engine-config", config, "--skills-home", skills,
                   "--snapshot-home", snapshots]
        before_restore = hashes(root)
        assert run(restore)["status"] == "ready"
        assert hashes(root) == before_restore
        assert run([*restore, "--apply"])["status"] == "restored"
        for name, raw in originals.items():
            assert (codex / name).read_bytes() == raw
        assert not config.exists() and not (skills / "retulo/SKILL.md").exists()
        for path, checksum in preserved.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == checksum
        print(json.dumps({"ok": True, "install_preview_read_only": True, "reinstall_no_change": True,
            "hook_events": 3, "hooks_read_only": True, "completed_versions": len(preserved),
            "same_task_resumed": True, "stdio_mcp_tools": tool_count,
            "restore_preview_read_only": True, "original_settings_restored": True,
            "artifacts_preserved_after_restore": True, "model_calls": 0,
            "host_settings_modified": False}))


if __name__ == "__main__":
    main()
