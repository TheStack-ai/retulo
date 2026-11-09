"""Git extension transport contracts exercised on synthetic local repositories."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from retulo.cli import ConfigError, METHODS, build_engine, init_config, load_config, validate_config
from retulo.mcp import _compact_schema, build_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GIT_TOOLS = {"git_status", "git_bind", "git_create_worktree", "git_prepare_integration",
             "git_integrate", "git_prepare_remove", "git_remove_worktree", "git_record_snapshot", "git_abandon_pending"}


class GitConfigTests(unittest.TestCase):
    def test_old_config_disables_worktree_creation_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = validate_config({"workspace_home": str(root / "workspace")})
            self.assertEqual(config["allowed_git_worktree_roots"], [])
            engine = build_engine(config)
            self.assertFalse(engine.capabilities()["features"]["git_worktree_creation_enabled"])
            self.assertEqual(list(root.iterdir()), [])

    def test_git_roots_are_explicit_config_and_not_source_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = root / "config.json"
            result = subprocess.run([sys.executable, "-m", "retulo", "--config", str(config),
                "init-config", "--workspace-home", str(root / "workspaces"),
                "--allow-git-worktree-root", str(root / "git-worktrees"), "--json"],
                cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            loaded = load_config(config)
            self.assertEqual(loaded["allowed_git_worktree_roots"], [str(root / "git-worktrees")])
            self.assertEqual(loaded["allowed_source_roots"], [])
            self.assertFalse((root / "git-worktrees").exists())
            self.assertFalse((root / "workspaces").exists())
            for invalid in ("/all", ["relative"], ["/ok/../elsewhere"], [True]):
                with self.subTest(invalid=invalid), self.assertRaises(ConfigError):
                    validate_config({"workspace_home": str(root / "workspaces"), "allowed_git_worktree_roots": invalid})


class GitToolSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_projection_preserves_title_fields_and_validation(self):
        schema = {"title": "Input", "type": "object", "required": ["title"], "additionalProperties": False,
                  "properties": {"title": {"title": "Title", "type": "string", "minLength": 1},
                                 "data": {"anyOf": [{"title": "Number", "type": "integer"}, {"type": "null"}]}},
                  "$defs": {"Nested": {"title": "Nested", "properties": {"title": {"type": "string"}}}}}
        projected = _compact_schema(schema)
        self.assertNotIn("title", projected)
        self.assertEqual(projected["properties"]["title"], {"type": "string", "minLength": 1})
        self.assertEqual(projected["required"], ["title"])
        self.assertFalse(projected["additionalProperties"])
        self.assertIn("title", projected["$defs"]["Nested"]["properties"])
        self.assertEqual(schema["title"], "Input")
        self.assertEqual(schema["properties"]["title"]["title"], "Title")

    async def test_git_tools_are_typed_and_annotations_match_effects(self):
        tools = {tool.name: tool for tool in await build_server(object()).list_tools()}
        self.assertTrue(GIT_TOOLS.issubset(METHODS))
        for name in GIT_TOOLS:
            self.assertIn(name, tools)
            annotations = tools[name].annotations
            self.assertEqual(annotations.read_only_hint, name in {"git_status", "git_prepare_integration", "git_prepare_remove"})
            self.assertEqual(annotations.destructive_hint, name in {"git_integrate", "git_remove_worktree"})
            self.assertFalse(annotations.open_world_hint)
        self.assertNotIn("expected_revision", tools["git_bind"].input_schema["properties"])
        self.assertIn("expected_git_revision", tools["git_bind"].input_schema["properties"])
        self.assertIn("expected_revision", tools["git_record_snapshot"].input_schema["properties"])

    async def test_strict_revisions_and_paths_reject_coercion_before_engine_call(self):
        class FakeEngine:
            def __init__(self):
                self.calls = 0
            def git_bind(self, task_id, operation_id, expected_git_revision, path):
                self.calls += 1
                return {"git_revision": expected_git_revision + 1}
        engine = FakeEngine()
        server = build_server(engine)
        arguments = {"task_id": "synthetic", "operation_id": "bind", "expected_git_revision": 0, "path": "/synthetic"}
        for revision in (True, "0", 0.0):
            with self.subTest(revision=revision), self.assertRaises(ToolError):
                await server.call_tool("git_bind", {**arguments, "expected_git_revision": revision})
        with self.assertRaises(ToolError):
            await server.call_tool("git_bind", {**arguments, "path": 42})
        self.assertEqual(engine.calls, 0)
        result = await server.call_tool("git_bind", arguments)
        self.assertFalse(result.is_error)
        self.assertEqual(engine.calls, 1)


class GitWireTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "한글 repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Synthetic")
        self.git("config", "user.email", "synthetic@example.invalid")
        (self.repo / "code.txt").write_text("original\n")
        self.git("add", "code.txt")
        self.git("commit", "-m", "initial")
        self.config = self.root / "retulo.json"
        self.worktrees = self.root / "managed"
        self.worktrees.mkdir()
        init_config(self.config, workspace_home=str(self.root / "workspaces"),
                    allowed_source_roots=[str(self.repo)], allowed_git_worktree_roots=[str(self.worktrees)])

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args, cwd=None):
        result = subprocess.run(["git", *args], cwd=cwd or self.repo, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    @asynccontextmanager
    async def connect(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        server = StdioServerParameters(command=sys.executable,
            args=["-m", "retulo", "--config", str(self.config), "mcp"], env=env)
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                yield client

    async def call(self, client, name, arguments):
        result = await client.call_tool(name, arguments)
        self.assertFalse(result.is_error, result.structured_content)
        return result.structured_content

    async def test_real_stdio_lifecycle_cli_parity_and_original_task_preserved(self):
        async with self.connect() as client:
            task = await self.call(client, "begin_task", {"start_request_id": "git-wire",
                "title": "Git wire test", "goal": "Exercise synthetic Git lifecycle", "source_root": str(self.repo)})
            tid = task["task_id"]
            task_file = Path(task["workspace_path"]) / ".workspace/task.json"
            before = task_file.read_bytes()
            state = await self.call(client, "git_status", {"task_id": tid})
            self.assertEqual(state["status"], "unbound")
            self.assertEqual(state["git_revision"], 0)
            self.assertEqual(task_file.read_bytes(), before)
            path = self.worktrees / "task worktree"
            create_args = {"task_id": tid, "operation_id": "create", "expected_git_revision": 0,
                           "repo_path": str(self.repo), "path": str(path), "branch": "codex/wire"}
            created = await self.call(client, "git_create_worktree", create_args)
            self.assertTrue(path.is_dir())
            self.assertEqual(created["git_revision"], 1)
            self.assertEqual(task_file.read_bytes(), before)
            replay = await self.call(client, "git_create_worktree", create_args)
            self.assertTrue(replay["replayed"])
            # CLI and official SDK read the same persisted association.
            cli = subprocess.run([sys.executable, "-m", "retulo", "--config", str(self.config), "call", "git_status",
                "--arguments", json.dumps({"task_id": tid}), "--json"], cwd=PROJECT_ROOT, text=True,
                capture_output=True, timeout=20)
            self.assertEqual(cli.returncode, 0, cli.stderr + cli.stdout)
            observed = json.loads(cli.stdout)
            self.assertEqual(observed["binding"]["path"], str(path))
            self.assertEqual(observed["git_revision"], created["git_revision"])
            (path / "code.txt").write_text("updated\n")
            self.git("add", "code.txt", cwd=path)
            self.git("commit", "-m", "feature", cwd=path)
            plan = await self.call(client, "git_prepare_integration", {"task_id": tid, "target_path": str(self.repo)})
            self.assertTrue(plan["eligible"])
            applied = await self.call(client, "git_integrate", {"task_id": tid, "operation_id": "integrate",
                "expected_git_revision": 1, "plan": plan})
            self.assertTrue(applied["integrated"])
            self.assertEqual((self.repo / "code.txt").read_text(), "updated\n")
            removal = await self.call(client, "git_prepare_remove", {"task_id": tid, "target_path": str(self.repo)})
            removed = await self.call(client, "git_remove_worktree", {"task_id": tid, "operation_id": "remove",
                "expected_git_revision": applied["git_revision"], "plan": removal})
            self.assertTrue(removed["removed"])
            self.assertFalse(path.exists())
            self.assertTrue(self.git("rev-parse", "refs/heads/codex/wire"))
            self.assertEqual(task_file.read_bytes(), before)
        # Fresh MCP process can inspect the completed Git lifecycle.
        async with self.connect() as client:
            observed = await self.call(client, "git_status", {"task_id": tid})
            self.assertEqual(observed["status"], "removed")
            self.assertEqual(observed["task_revision"], task["revision"])

    async def test_wire_invalid_mutation_arguments_leave_git_unbound(self):
        async with self.connect() as client:
            task = await self.call(client, "begin_task", {"start_request_id": "invalid-wire",
                "title": "Invalid request", "goal": "Reject unsafe request"})
            arguments = {"task_id": task["task_id"], "operation_id": "bind", "expected_git_revision": 0,
                         "path": str(self.repo)}
            for bad in ({**arguments, "expected_git_revision": True}, {**arguments, "path": str(self.root)}):
                result = await client.call_tool("git_bind", bad)
                self.assertTrue(result.is_error)
            status = await self.call(client, "git_status", {"task_id": task["task_id"]})
            self.assertEqual(status["status"], "unbound")
            self.assertEqual(status["git_revision"], 0)
