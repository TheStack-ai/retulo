"""Transport behavior and real official-SDK stdio round trips, using synthetic files only."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from retulo.cli import ConfigError, METHODS, RECORD_SECTIONS, dispatch, init_config, load_config, validate_config
from retulo.mcp import INSTRUCTIONS, build_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REMOVED_TOOLS = frozenset({"brain_prepare", "brain_capture", "brain_sync", "brain_receipt", "review_context"})


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config = self.root / "config/config.json"
        self.home = self.root / "workspaces"

    def tearDown(self):
        self.temporary.cleanup()

    def test_explicit_config_creation_does_not_create_workspace(self):
        result = init_config(self.config, workspace_home=str(self.home))
        self.assertFalse(self.home.exists())
        self.assertFalse(result["workspace_created"])
        self.assertEqual(load_config(self.config)["workspace_home"], str(self.home))
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_existing_configuration_is_never_overwritten(self):
        init_config(self.config, workspace_home=str(self.home))
        before = self.config.read_bytes()
        with self.assertRaises(ConfigError):
            init_config(self.config, workspace_home=str(self.root / "different"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list(self.config.parent.glob(".config-*")), [])

    def test_missing_read_leaves_filesystem_unchanged(self):
        with self.assertRaises(ConfigError):
            load_config(self.config)
        self.assertFalse(self.config.parent.exists())
        self.assertFalse(self.home.exists())

    def test_symlink_configuration_is_rejected_and_original_preserved(self):
        original = self.root / "original.json"
        original.write_text('{"workspace_home":"/example"}')
        alias = self.root / "alias.json"
        alias.symlink_to(original)
        with self.assertRaises(ConfigError):
            load_config(alias)
        with self.assertRaises(ConfigError):
            init_config(alias, workspace_home=str(self.home))
        self.assertEqual(original.read_text(), '{"workspace_home":"/example"}')

    def test_configuration_schema_rejects_path_and_type_errors(self):
        for bad in ({"workspace_home": "relative"}, {"workspace_home": "/ok/../escape"},
                    {"workspace_home": "/ok", "allowed_source_roots": "/all"},
                    {"workspace_home": "/ok", "schema_version": True},
                    {"workspace_home": "/ok", "unexpected": "value"}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                validate_config(bad)

    def test_removed_integration_configuration_is_unsupported_and_not_applied(self):
        self.config.parent.mkdir(parents=True)
        for value in ({}, None, {"command": "synthetic-unavailable-command"}):
            with self.subTest(value=value):
                self.config.write_text(json.dumps({"workspace_home": str(self.home), "main_brain": value}))
                before = self.config.read_bytes()
                with self.assertRaisesRegex(ConfigError, "unsupported keys: main_brain"):
                    load_config(self.config)
                result = subprocess.run([sys.executable, "-m", "retulo", "--config", str(self.config),
                                         "list", "--json"], cwd=PROJECT_ROOT, text=True,
                                        capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "configuration_error")
                self.assertIn("unsupported keys: main_brain", result.stdout)
                self.assertEqual(self.config.read_bytes(), before)
                self.assertFalse(self.home.exists())

    def test_cli_rejects_removed_tool_names_before_loading_configuration(self):
        for name in sorted(REMOVED_TOOLS):
            with self.subTest(name=name):
                result = subprocess.run([sys.executable, "-m", "retulo", "--config", str(self.config),
                                         "call", name], cwd=PROJECT_ROOT, text=True,
                                        capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid choice", result.stderr)
                self.assertFalse(self.config.parent.exists())
                self.assertFalse(self.home.exists())

    def test_help_and_missing_config_read_have_no_side_effects(self):
        before = set(self.root.rglob("*"))
        help_result = subprocess.run([sys.executable, "-m", "retulo", "--config", str(self.config),
                                      "--help"], cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        failed_read = subprocess.run([sys.executable, "-m", "retulo", "--config", str(self.config),
                                     "list", "--json"], cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(failed_read.returncode, 2)
        self.assertEqual(json.loads(failed_read.stdout)["error"]["code"], "configuration_error")
        self.assertNotIn("Traceback", failed_read.stderr)
        self.assertEqual(set(self.root.rglob("*")), before)


class SDKSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_sdk_typed_schemas_and_annotations(self):
        server = build_server(object())
        listed = await server.list_tools()
        encoded = json.dumps([tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                              for tool in listed], ensure_ascii=False, separators=(",", ":")).encode()
        self.assertLessEqual(len(encoded), 24 * 1024, "Exported tool definitions exceeded their context budget")
        self.assertLessEqual(len(INSTRUCTIONS.encode()), 1200, "Shared MCP guidance should stay concise")
        tools = {tool.name: tool for tool in listed}
        self.assertEqual(set(tools), METHODS)
        self.assertTrue(REMOVED_TOOLS.isdisjoint(tools))
        self.assertTrue(tools["harness_context"].annotations.read_only_hint)
        self.assertEqual(tools["harness_context"].input_schema["properties"]["limit"]["default"], 5)
        self.assertTrue(tools["read_records"].annotations.read_only_hint)
        self.assertFalse(tools["attach_workspace"].annotations.read_only_hint)
        self.assertFalse(tools["abandon_output"].annotations.read_only_hint)
        self.assertTrue(tools["verify"].annotations.read_only_hint)
        self.assertFalse(tools["resume_task"].annotations.read_only_hint)
        begin_schema = tools["begin_task"].input_schema
        self.assertNotIn("invoke", begin_schema["properties"])
        self.assertEqual(set(begin_schema["required"]), {"start_request_id", "title", "goal"})
        criterion = begin_schema["$defs"]["CompletionCriterionInput"]
        self.assertEqual(criterion["required"], ["text"])
        self.assertFalse(criterion["additionalProperties"])
        self.assertEqual(criterion["properties"]["human_review"]["anyOf"],
                         [{"type": "string"}, {"const": False, "type": "boolean"}])
        self.assertEqual(criterion["properties"]["requires_approval"]["type"], "boolean")
        checkpoint_defs = tools["checkpoint"].input_schema["$defs"]
        changes = checkpoint_defs["CheckpointChangesInput"]
        self.assertFalse(changes["additionalProperties"])
        self.assertEqual(set(changes["properties"]), {"status", "next_action", "mode", "tags", "decisions",
                                                     "next_review_at", "criteria_results", "attention"})
        result = checkpoint_defs["CriterionResultInput"]
        self.assertFalse(result["additionalProperties"])
        self.assertEqual(set(result["required"]), {"criterion", "status"})
        self.assertEqual(set(result["properties"]), {"criterion", "status", "evidence_ref", "note", "review"})
        self.assertEqual(result["properties"]["note"]["type"], "string")
        self.assertEqual(tools["get_context"].input_schema["properties"]["detail"]["default"], "compact")
        self.assertEqual(tools["get_context"].input_schema["properties"]["detail"]["enum"], ["compact", "full"])
        self.assertEqual(tools["resume_task"].input_schema["properties"]["detail"]["default"], "compact")
        self.assertEqual(tools["resume_task"].input_schema["properties"]["detail"]["enum"], ["compact", "full"])
        self.assertIn("criteria_history", tools["read_records"].input_schema["properties"]["section"]["enum"])
        self.assertEqual(set(tools["read_records"].input_schema["properties"]["section"]["enum"]),
                         set(RECORD_SECTIONS))
        for name in ("plan_rebuild", "check_recipe"):
            self.assertTrue(tools[name].annotations.read_only_hint)
        for name in ("define_workflow", "bind_input", "begin_step_run", "finish_step_run", "begin_from_recipe"):
            self.assertFalse(tools[name].annotations.read_only_hint)
            self.assertTrue(tools[name].annotations.idempotent_hint)
        self.assertEqual(tools["bind_input"].input_schema["properties"]["mode"]["enum"], ["follow", "pinned"])
        self.assertEqual(tools["begin_step_run"].input_schema["properties"]["dependency_coverage"]["default"], "unknown")
        self.assertIn("target_ids", tools["begin_step_run"].input_schema["properties"])
        finish_schema = tools["finish_step_run"].input_schema
        self.assertEqual(finish_schema["properties"]["outcome"]["enum"], ["succeeded", "failed", "interrupted"])
        self.assertFalse(finish_schema["$defs"]["StepCheckInput"]["additionalProperties"])
        recipe_schema = tools["begin_from_recipe"].input_schema
        self.assertEqual(recipe_schema["$defs"]["RecipeFileInput"]["required"], ["path"])
        self.assertFalse(recipe_schema["$defs"]["RecipeFileInput"]["additionalProperties"])
        self.assertIn("run_id", tools["reserve_output"].input_schema["properties"])
        self.assertIn("output_slot", tools["reserve_output"].input_schema["properties"])
        plan_schema = tools["plan_rebuild"].input_schema
        self.assertEqual(plan_schema["properties"]["detail"]["default"], "compact")
        self.assertEqual(plan_schema["properties"]["limit"]["default"], 20)
        self.assertIn("expected_plan_digest", plan_schema["properties"])
        self.assertEqual(tools["check_recipe"].input_schema["properties"]["detail"]["default"], "compact")

    async def test_new_tools_preserve_typed_values_and_reject_coercion(self):
        class FakeEngine:
            def __init__(self):
                self.calls = []

            def define_workflow(self, task_id, operation_id, expected_revision, definition,
                                parameters=None, environment=None):
                self.calls.append(("define_workflow", definition, parameters, environment))
                return {"definition": definition, "parameters": parameters, "environment": environment}

            def finish_step_run(self, task_id, operation_id, expected_revision, run_id,
                                outcome="succeeded", checks=None, evidence_ref=None):
                self.calls.append(("finish_step_run", outcome, checks))
                return {"outcome": outcome, "checks": checks}

            def begin_from_recipe(self, start_request_id, recipe_task_id, recipe_artifact_id,
                                  recipe_artifact_revision, input_bindings, parameters, title, goal,
                                  environment=None, workspace_root=None, source_root=None, binding=None):
                self.calls.append(("begin_from_recipe", input_bindings, parameters))
                return {"input_bindings": input_bindings, "parameters": parameters}

        engine = FakeEngine()
        server = build_server(engine)
        base = {"task_id": "synthetic", "operation_id": "define", "expected_revision": 1}
        result = await server.call_tool("define_workflow", {**base, "definition": {"workflow_schema_version": 1},
            "parameters": {"count": 0, "enabled": False, "label": ""}, "environment": {"python": "3.11"}})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["parameters"], {"count": 0, "enabled": False, "label": ""})
        for revision in (True, "1", 1.0):
            with self.subTest(revision=revision), self.assertRaises(ToolError):
                await server.call_tool("define_workflow", {**base, "expected_revision": revision, "definition": {}})
        checks = {"content": {"status": "passed", "evidence_ref": "artifact:synthetic:1", "note": "checked"}}
        finish = await server.call_tool("finish_step_run", {**base, "run_id": "run", "checks": checks})
        self.assertEqual(finish.structured_content["checks"], checks)
        for invalid in ({"status": "passed", "approved": True}, {"status": "done"}, {"status": "passed", "note": 3}):
            with self.subTest(check=invalid), self.assertRaises(ToolError):
                await server.call_tool("finish_step_run", {**base, "run_id": "run", "checks": {"content": invalid}})
        recipe = {"start_request_id": "repeat", "recipe_task_id": "source", "recipe_artifact_id": "recipe",
                  "recipe_artifact_revision": 1, "title": "New task", "goal": "New output", "parameters": {}}
        bound = await server.call_tool("begin_from_recipe", {**recipe, "input_bindings": {"sales": {"path": "/synthetic.csv"}}})
        self.assertFalse(bound.is_error)
        for invalid in ({"path": 7}, {"path": "/synthetic.csv", "allow_root": "/"}, {"source_id": "old"}):
            with self.subTest(binding=invalid), self.assertRaises(ToolError):
                await server.call_tool("begin_from_recipe", {**recipe, "input_bindings": {"sales": invalid}})
        self.assertEqual(len(engine.calls), 3)

    async def test_incremental_dispatch_is_allowlisted_and_signature_checked(self):
        class FakeEngine:
            def plan_rebuild(self, task_id, target_ids=None):
                return {"task_id": task_id, "target_ids": target_ids}

        expected = {"define_workflow", "bind_input", "plan_rebuild", "begin_step_run",
                    "finish_step_run", "check_recipe", "begin_from_recipe"}
        self.assertTrue(expected.issubset(METHODS))
        self.assertEqual(dispatch(FakeEngine(), "plan_rebuild", {"task_id": "synthetic", "target_ids": ["report"]}),
                         {"task_id": "synthetic", "target_ids": ["report"]})
        with self.assertRaises(ConfigError):
            dispatch(FakeEngine(), "plan_rebuild", {"task_id": "synthetic", "execute": True})
        with self.assertRaises(ConfigError):
            dispatch(FakeEngine(), "_create_task", {})

    async def test_closure_does_not_leak_into_engine_arguments(self):
        class FakeEngine:
            def begin_task(self, start_request_id, title, goal, completion_criteria=None, kind="general",
                           workspace_root=None, source_root=None, task_id=None, binding=None):
                return {"task_id": "synthetic", "goal": goal}
        result = await build_server(FakeEngine()).call_tool("begin_task", {
            "start_request_id": "stable", "title": "test", "goal": "create synthetic artifact"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["task_id"], "synthetic")

    async def test_removed_tools_are_unavailable(self):
        server = build_server(object())
        for name in sorted(REMOVED_TOOLS):
            with self.subTest(name=name), self.assertRaises(ToolError):
                await server.call_tool(name, {"task_id": "synthetic"})

    async def test_typed_criterion_preserves_false_and_rejects_coercion_before_dispatch(self):
        class FakeEngine:
            def __init__(self):
                self.calls = []

            def begin_task(self, start_request_id, title, goal, completion_criteria=None, **kwargs):
                self.calls.append(completion_criteria)
                return {"criteria": completion_criteria}

        engine = FakeEngine()
        server = build_server(engine)
        arguments = {"start_request_id": "typed", "title": "Synthetic", "goal": "Save result"}
        for value in (False, "User checks the rendering"):
            result = await server.call_tool("begin_task", {**arguments,
                "completion_criteria": [{"text": "Check", "human_review": value}]})
            self.assertFalse(result.is_error)
            self.assertIs(type(engine.calls[-1][0]["human_review"]), type(value))
            self.assertEqual(engine.calls[-1][0]["human_review"], value)
        for invalid in ({"human_review": True}, {"human_review": 0}, {"human_review": None},
                        {"requires_approval": "false"}, {"unexpected_field": False}):
            with self.subTest(invalid=invalid), self.assertRaises(ToolError):
                await server.call_tool("begin_task", {**arguments,
                    "completion_criteria": [{"text": "Check", **invalid}]})
        self.assertEqual(len(engine.calls), 2)

    async def test_typed_checkpoint_rejects_wrong_fields_and_coercion_before_dispatch(self):
        class FakeEngine:
            def __init__(self):
                self.calls = []

            def checkpoint(self, task_id, operation_id, expected_revision, changes):
                self.calls.append(changes)
                return {"changes": changes}

        engine = FakeEngine()
        server = build_server(engine)
        arguments = {"task_id": "synthetic", "operation_id": "check", "expected_revision": 1}
        valid = {"status": "active", "next_action": "Inspect result", "mode": "light", "tags": ["check"],
                 "decisions": [], "next_review_at": None, "attention": None, "criteria_results": [
                     {"criterion": "saved", "status": "unknown", "note": "Inspection remains",
                      "review": {"mode": "self"}}]}
        result = await server.call_tool("checkpoint", {**arguments, "changes": valid})
        self.assertFalse(result.is_error)
        self.assertEqual(engine.calls, [valid])
        for invalid in ({"goal": "Changed goal"}, {"completion_criteria": []},
                        {"criteria_results": [{"criterion": "saved", "status": "unknown", "text": "Wrong key"}]},
                        {"criteria_results": [{"criterion": "saved", "status": "unknown", "note": 123}]},
                        {"next_action": False}, {"tags": "check"}):
            with self.subTest(invalid=invalid), self.assertRaises(ToolError):
                await server.call_tool("checkpoint", {**arguments, "changes": invalid})
        self.assertEqual(engine.calls, [valid])


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config = self.root / "config.json"
        self.home = self.root / "workspaces"
        init_config(self.config, workspace_home=str(self.home))

    async def asyncTearDown(self):
        self.temporary.cleanup()

    @asynccontextmanager
    async def connect(self):
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "retulo", "--config", str(self.config), "mcp"], cwd=str(PROJECT_ROOT))
        with open(os.devnull, "w") as errors:
            async with stdio_client(params, errlog=errors) as streams:
                async with ClientSession(*streams, read_timeout_seconds=15) as client:
                    initialized = await client.initialize()
                    self.assertEqual(initialized.server_info.name, "retulo")
                    yield client

    async def call(self, client, name, arguments=None):
        result = await client.call_tool(name, arguments or {})
        self.assertFalse(result.is_error, result.content)
        return result.structured_content

    async def test_stdio_workflow_run_reuse_and_cli_revision_bound_details(self):
        definition = {
            "workflow_schema_version": 1, "inputs": {},
            "parameters": {"period": {"type": "string", "required": True}}, "environment": {},
            "steps": {"report": {
                "action": {"kind": "agent", "instruction": "Write the synthetic period report"},
                "inputs": {}, "parameters": ["period"], "environment": [],
                "outputs": {"report": {"logical_name": "report.txt", "role": "deliverable", "format": "text"}},
                "reuse_policy": "if_unchanged", "required_checks": []}},
            "targets": {"report": {"outputs": [{"step": "report", "output": "report"}], "required_criteria": []}},
        }
        definition["steps"]["supplement"] = {
            "action": {"kind": "agent", "instruction": "Write a separate optional supplement"},
            "inputs": {}, "parameters": [], "environment": [],
            "outputs": {"note": {"logical_name": "supplement.txt", "role": "deliverable", "format": "text"}},
            "reuse_policy": "if_unchanged", "required_checks": []}
        definition["targets"]["supplement"] = {"outputs": [{"step": "supplement", "output": "note"}]}
        async with self.connect() as client:
            started = await self.call(client, "begin_task", {"start_request_id": "workflow-wire-start",
                "title": "Synthetic workflow", "goal": "Exercise explicit graph tools"})
            task_id = started["task_id"]
            defined = await self.call(client, "define_workflow", {"task_id": task_id,
                "operation_id": "define", "expected_revision": started["revision"],
                "definition": definition, "parameters": {"period": "2026-11"}})
            path = Path(started["workspace_path"]) / ".workspace/task.json"
            before_plan = path.read_bytes()
            plan = await self.call(client, "plan_rebuild", {"task_id": task_id, "target_ids": ["report"]})
            self.assertEqual(plan["steps"]["report"]["state"], "needs_run")
            self.assertNotIn("supplement", plan["steps"])
            self.assertEqual(path.read_bytes(), before_plan, "Planning mutated the task")
            running = await self.call(client, "begin_step_run", {"task_id": task_id,
                "operation_id": "start-report", "expected_revision": defined["revision"],
                "step_id": "report", "plan_digest": plan["plan_digest"],
                "dependency_coverage": "declared_complete", "target_ids": ["report"]})
            full = await self.call(client, "get_context", {"task_id": task_id, "detail": "full"})
            run_id = full["state"]["runs"][-1]["run_id"]
            reserved = await self.call(client, "reserve_output", {"task_id": task_id,
                "operation_id": "reserve-report", "expected_revision": running["revision"],
                "name": "report.txt", "role": "deliverable", "format": "text",
                "run_id": run_id, "output_slot": "report"})
            Path(reserved["write_path"]).write_text("Synthetic period: 2026-11\n")
            committed = await self.call(client, "commit_artifact", {"task_id": task_id,
                "operation_id": "commit-report", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            ref = f"artifact:{committed['artifact']['artifact_id']}:{committed['artifact']['revision']}"
            finished = await self.call(client, "finish_step_run", {"task_id": task_id,
                "operation_id": "finish-report", "expected_revision": committed["revision"],
                "run_id": run_id, "evidence_ref": ref})
            after_run = path.read_bytes()
            reusable = await self.call(client, "plan_rebuild", {"task_id": task_id})
            self.assertEqual(reusable["steps"]["report"]["state"], "reusable")
            compact = await self.call(client, "get_context", {"task_id": task_id})
            self.assertEqual(compact["state"]["schema_version"], 2)
            self.assertEqual(compact["state"]["workflow_summary"]["freshness"], "not_inspected")
            self.assertNotIn("workflow", compact["state"])
            page = await self.call(client, "read_records", {"task_id": task_id, "section": "steps",
                "expected_revision": finished["revision"]})
            self.assertEqual(page["records"][0]["key"], "report")
            self.assertTrue(page["records"][0]["complete"])
            self.assertEqual(path.read_bytes(), after_run)
        async with self.connect() as client:
            resumed = await self.call(client, "plan_rebuild", {"task_id": task_id})
            self.assertEqual(resumed["steps"]["report"]["state"], "reusable")
        cli = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "retulo", "--config", str(self.config), "read-records",
             "--task-id", task_id, "--section", "steps", "--expected-revision", str(finished["revision"]), "--json"],
            cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout), page)

    async def test_stdio_recipe_creates_new_pinned_task_and_replays_after_source_change(self):
        manifest = {"recipe_schema_version": 1, "recipe_id": str(uuid.uuid4()), "revision": 1,
                    "title": "Synthetic report recipe", "workflow": {
                        "workflow_schema_version": 1, "inputs": {}, "environment": {},
                        "parameters": {"period": {"type": "string", "required": True}},
                        "steps": {"report": {"action": {"kind": "agent", "instruction": "Write the requested period"},
                            "inputs": {}, "parameters": ["period"], "environment": [], "required_checks": [],
                            "reuse_policy": "if_unchanged",
                            "outputs": {"report": {"logical_name": "report.txt", "role": "deliverable", "format": "text"}}}},
                        "targets": {"report": {"outputs": [{"step": "report", "output": "report"}], "required_criteria": []}}},
                    "completion_criteria": []}
        description = "Synthetic optional input: " + "x" * 1970
        manifest["workflow"]["inputs"] = {f"optional-{index}": {"required": False, "description": description}
                                          for index in range(20)}
        async with self.connect() as client:
            source = await self.call(client, "begin_task", {"start_request_id": "recipe-authoring",
                "title": "Synthetic recipe", "goal": "Register a reusable manifest"})
            reserved = await self.call(client, "reserve_output", {"task_id": source["task_id"],
                "operation_id": "reserve-recipe", "expected_revision": source["revision"],
                "name": "recipe.json", "role": "recipe", "format": "json"})
            Path(reserved["write_path"]).write_text(json.dumps(manifest))
            committed = await self.call(client, "commit_artifact", {"task_id": source["task_id"],
                "operation_id": "commit-recipe", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            reference = {"recipe_task_id": source["task_id"], "recipe_artifact_id": committed["artifact"]["artifact_id"],
                         "recipe_artifact_revision": committed["artifact"]["revision"]}
            before_check = (Path(source["workspace_path"]) / ".workspace/task.json").read_bytes()
            checked = await self.call(client, "check_recipe", reference)
            encoded = json.dumps(checked, ensure_ascii=False, separators=(",", ":")).encode()
            self.assertLessEqual(len(encoded), 24576)
            self.assertNotIn(description.encode(), encoded)
            full_check = await self.call(client, "check_recipe", {**reference, "detail": "full"})
            self.assertEqual(full_check["required_inputs"]["optional-0"]["description"], description)
            self.assertEqual((Path(source["workspace_path"]) / ".workspace/task.json").read_bytes(), before_check)
            request = {**reference, "start_request_id": "recipe-new-period", "input_bindings": {},
                       "parameters": {"period": "2026-12"}, "title": "December report", "goal": "Create new period output"}
            created = await self.call(client, "begin_from_recipe", request)
            self.assertNotEqual(created["task_id"], source["task_id"])
            full = await self.call(client, "get_context", {"task_id": created["task_id"], "detail": "full"})
            state = full["state"]
            self.assertEqual(state["schema_version"], 2)
            self.assertEqual(state["workflow"]["parameters"], {"period": "2026-12"})
            self.assertEqual(state["runs"], [])
            self.assertEqual(state["approvals"], [])
            self.assertEqual(state["criteria_results"], [])
            self.assertTrue(state["recipe_binding"])
            changed = await client.call_tool("begin_from_recipe", {**request, "parameters": {"period": "2027-01"}})
            self.assertTrue(changed.is_error)
            self.assertEqual(changed.structured_content["error"]["code"], "idempotency_conflict")
            # A lost creation response must still recover its task even if the
            # old manifest later becomes unavailable or changes outside Retulo.
            Path(reserved["final_path"]).write_text("changed after task creation")
        async with self.connect() as client:
            replayed = await self.call(client, "begin_from_recipe", request)
            self.assertEqual(replayed["task_id"], created["task_id"])
            self.assertTrue(replayed["replayed"])
            recovered = await self.call(client, "get_context", {"task_id": created["task_id"], "detail": "full"})
            self.assertEqual(recovered["state"]["recipe_binding"], state["recipe_binding"])
            failed = await client.call_tool("check_recipe", reference)
            self.assertTrue(failed.is_error)

    async def test_stdio_large_plan_pages_are_bounded_and_pin_external_input_state(self):
        sources = self.root / "sources"
        sources.mkdir()
        data = sources / "data.csv"
        data.write_text("value\n1\n")
        self.config = self.root / "workflow-config.json"
        init_config(self.config, workspace_home=str(self.home), allowed_source_roots=[str(sources)])
        definition = {"workflow_schema_version": 1, "inputs": {"data": {"format": "csv"}},
                      "parameters": {}, "environment": {}, "steps": {}, "targets": {}}
        for index in range(128):
            name = f"step-{index:03d}"
            definition["steps"][name] = {
                "action": {"kind": "agent", "instruction": "Make a synthetic result"},
                "inputs": {"data": {"input": "data"}},
                "outputs": {"result": {"logical_name": f"result-{index:03d}.txt"}},
                "reuse_policy": "if_unchanged"}
        definition["targets"]["report"] = {
            "outputs": [{"step": name, "output": "result"} for name in definition["steps"]]}
        async with self.connect() as client:
            started = await self.call(client, "begin_task", {"start_request_id": "large-plan",
                "title": "Synthetic large plan", "goal": "Bound plan context without losing dependency state"})
            task_id = started["task_id"]
            defined = await self.call(client, "define_workflow", {"task_id": task_id,
                "operation_id": "define", "expected_revision": started["revision"], "definition": definition})
            source = await self.call(client, "add_source", {"task_id": task_id,
                "operation_id": "add-input", "expected_revision": defined["revision"], "source_path": str(data)})
            bound = await self.call(client, "bind_input", {"task_id": task_id,
                "operation_id": "bind-input", "expected_revision": source["revision"],
                "slot": "data", "source_id": source["source"]["source_id"]})
            state_path = Path(started["workspace_path"]) / ".workspace/task.json"
            before = state_path.read_bytes()
            first = await self.call(client, "plan_rebuild", {"task_id": task_id, "limit": 7})
            self.assertLessEqual(len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode()), 24576)
            self.assertEqual(first["counts"]["needs_run"], 128)
            self.assertEqual(len(first["steps"]), 7)
            self.assertFalse(first["complete"])
            self.assertTrue(first["ready"])
            offset, recovered = first["next_offset"], list(first["steps"])
            while offset is not None:
                page = await self.call(client, "plan_rebuild", {"task_id": task_id,
                    "offset": offset, "limit": 19, "expected_plan_digest": first["plan_digest"]})
                self.assertLessEqual(len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()), 24576)
                self.assertEqual(page["counts"]["needs_run"], 128)
                recovered.extend(page["steps"])
                offset = page["next_offset"]
            self.assertEqual(recovered, sorted(definition["steps"]))
            explicit = await self.call(client, "plan_rebuild", {"task_id": task_id,
                "detail": "full", "expected_plan_digest": first["plan_digest"]})
            self.assertEqual(len(explicit["steps"]), 128)
            self.assertEqual(state_path.read_bytes(), before)
            data.write_text("value\n2\n")
            stale = await client.call_tool("plan_rebuild", {"task_id": task_id,
                "offset": 7, "limit": 7, "expected_plan_digest": first["plan_digest"]})
            self.assertTrue(stale.is_error)
            self.assertEqual(stale.structured_content["error"]["code"], "plan_conflict")
            self.assertEqual(json.loads(state_path.read_text())["revision"], bound["revision"])

    async def test_stdio_artifact_roundtrip_idempotency_and_restart(self):
        request = {"start_request_id": "transport-document-start", "title": "Synthetic proposal",
                   "goal": "Create one synthetic proposal", "completion_criteria": []}
        async with self.connect() as client:
            listed = await client.list_tools()
            self.assertIn("commit_artifact", {tool.name for tool in listed.tools})
            await self.call(client, "capabilities")
            await self.call(client, "list_tasks")
            self.assertFalse(self.home.exists(), "Read-only calls created workspace directories")
            started = await self.call(client, "begin_task", request)
            repeated = await self.call(client, "begin_task", request)
            self.assertEqual(started["task_id"], repeated["task_id"])
            self.assertEqual(started["revision"], repeated["revision"])
            self.assertTrue(repeated["replayed"])
            task_id = started["task_id"]
            reserved = await self.call(client, "reserve_output", {"task_id": task_id,
                "operation_id": "transport-reserve", "expected_revision": started["revision"],
                "name": "proposal.md"})
            output = Path(reserved["write_path"])
            output.write_text("# Synthetic proposal\n\nNo customer data.\n", encoding="utf-8")
            expected_hash = hashlib.sha256(output.read_bytes()).hexdigest()
            committed = await self.call(client, "commit_artifact", {"task_id": task_id,
                "operation_id": "transport-commit", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            final_path = Path(reserved["final_path"])
            self.assertEqual(hashlib.sha256(final_path.read_bytes()).hexdigest(), expected_hash)
            verified = await self.call(client, "verify", {"task_id": task_id})
            self.assertTrue(verified["ok"])
            self.assertEqual(verified["revision"], committed["revision"])
            stale = await client.call_tool("checkpoint", {"task_id": task_id,
                "operation_id": "stale-change", "expected_revision": started["revision"],
                "changes": {"next_action": "This stale mutation must be rejected"}})
            self.assertTrue(stale.is_error)
            self.assertEqual(stale.structured_content["error"]["code"], "revision_conflict")
        async with self.connect() as resumed_client:
            context = await self.call(resumed_client, "get_context", {"task_id": task_id})
            self.assertEqual(context["revision"], committed["revision"])
            await self.call(resumed_client, "resume_task", {"task_id": task_id})
            verified = await self.call(resumed_client, "verify", {"task_id": task_id})
            self.assertTrue(verified["ok"])
            self.assertEqual(hashlib.sha256(final_path.read_bytes()).hexdigest(), expected_hash)
        cli = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "retulo", "--config", str(self.config),
             "verify", "--task-id", task_id, "--json"],
            cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout), verified)

    async def test_stdio_removed_tools_are_unknown_without_mutating_workspace(self):
        async with self.connect() as client:
            listed = await client.list_tools()
            self.assertEqual({tool.name for tool in listed.tools}, METHODS)
            for name in sorted(REMOVED_TOOLS):
                with self.subTest(name=name):
                    result = await client.call_tool(name, {"task_id": "not-created"})
                    self.assertTrue(result.is_error)
                    self.assertIsNone(result.structured_content)
                    self.assertTrue(any("Unknown tool" in item.text for item in result.content))
                    self.assertFalse(self.home.exists())

    async def test_stdio_false_human_review_replays_omission_and_preserves_approval_gate(self):
        async with self.connect() as client:
            request = {"start_request_id": "false-human-review", "title": "Synthetic report",
                       "goal": "Save and check the report", "completion_criteria": [
                           {"id": "saved", "text": "Checked artifact saved", "human_review": False}]}
            started = await self.call(client, "begin_task", request)
            replayed = await self.call(client, "begin_task", {**request, "completion_criteria": [
                {"id": "saved", "text": "Checked artifact saved"}]})
            self.assertTrue(replayed["replayed"])
            self.assertEqual(replayed["task_id"], started["task_id"])
            task_id = started["task_id"]
            full = await self.call(client, "get_context", {"task_id": task_id, "detail": "full"})
            criterion = full["state"]["completion_criteria"][0]
            self.assertNotIn("human_review", criterion)
            self.assertFalse(criterion["requires_approval"])
            reserved = await self.call(client, "reserve_output", {"task_id": task_id,
                "operation_id": "reserve", "expected_revision": started["revision"], "name": "report.txt"})
            Path(reserved["write_path"]).write_text("Synthetic checked result")
            committed = await self.call(client, "commit_artifact", {"task_id": task_id,
                "operation_id": "commit", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            checked = await self.call(client, "checkpoint", {"task_id": task_id,
                "operation_id": "check", "expected_revision": committed["revision"],
                "changes": {"criteria_results": [{"criterion": "saved", "status": "passed",
                    "evidence_ref": reserved["artifact_id"]}]}})
            completed = await self.call(client, "complete_task", {"task_id": task_id,
                "operation_id": "finish", "expected_revision": checked["revision"]})
            self.assertEqual(completed["status"], "completed")
            approval_task = await self.call(client, "begin_task", {**request,
                "start_request_id": "explicit-approval", "completion_criteria": [
                    {"text": "User approves artifact", "human_review": False, "requires_approval": True}]})
            approval_state = await self.call(client, "get_context",
                {"task_id": approval_task["task_id"], "detail": "full"})
            self.assertTrue(approval_state["state"]["completion_criteria"][0]["requires_approval"])

    async def test_stdio_unknown_independent_review_and_attention_are_distinct(self):
        async with self.connect() as client:
            started = await self.call(client, "begin_task", {"start_request_id": "judgment-table-start",
                "title": "Synthetic evidence review", "goal": "Keep unknown results and attention explicit",
                "completion_criteria": [{"id": "quality", "text": "Saved report has a separate review result",
                    "basis": "Synthetic requirement", "requires_independent_review": True}]})
            task_id = started["task_id"]
            reserved = await self.call(client, "reserve_output", {"task_id": task_id,
                "operation_id": "judgment-reserve", "expected_revision": started["revision"], "name": "review.md"})
            Path(reserved["write_path"]).write_text("Synthetic review record\n")
            committed = await self.call(client, "commit_artifact", {"task_id": task_id,
                "operation_id": "judgment-commit", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            reference = reserved["artifact_id"]
            attention = {"kind": "review", "owner": "user", "reason": "Synthetic unresolved choice",
                         "requested_action": "Choose the remaining synthetic direction"}
            unknown = await self.call(client, "checkpoint", {"task_id": task_id,
                "operation_id": "record-unknown", "expected_revision": committed["revision"],
                "changes": {"criteria_results": [{"criterion": "quality", "status": "unknown",
                    "evidence_ref": reference}], "attention": attention}})
            unchanged_attention = await self.call(client, "checkpoint", {"task_id": task_id,
                "operation_id": "update-next-action", "expected_revision": unknown["revision"],
                "changes": {"next_action": "Obtain a separate review"}})
            full = await self.call(client, "get_context", {"task_id": task_id, "detail": "full"})
            self.assertEqual(full["state"]["criteria_results"][0]["status"], "unknown")
            self.assertEqual(full["state"]["attention"], attention)
            self.assertEqual(full["state"]["status"], "active")
            compact = await self.call(client, "get_context", {"task_id": task_id})
            self.assertEqual(compact["state"]["attention_status"], "open")
            self.assertEqual(compact["state"]["attention"], attention)
            blocked = await client.call_tool("complete_task", {"task_id": task_id,
                "operation_id": "cannot-complete-unknown", "expected_revision": unchanged_attention["revision"]})
            self.assertTrue(blocked.is_error)
            repeated_self = await client.call_tool("checkpoint", {"task_id": task_id,
                "operation_id": "not-independent", "expected_revision": unchanged_attention["revision"],
                "changes": {"criteria_results": [{"criterion": "quality", "status": "passed",
                    "evidence_ref": reference, "review": {"mode": "independent",
                        "reviewer_ref": "same-run", "execution_ref": "same-run"}}]}})
            self.assertTrue(repeated_self.is_error)
            reviewed = await self.call(client, "checkpoint", {"task_id": task_id,
                "operation_id": "record-independent", "expected_revision": unchanged_attention["revision"],
                "changes": {"criteria_results": [{"criterion": "quality", "status": "passed",
                    "evidence_ref": reference, "review": {"mode": "independent",
                        "reviewer_ref": "synthetic-review-run", "execution_ref": "synthetic-execution-run"}}]}})
            pending_attention = await client.call_tool("complete_task", {"task_id": task_id,
                "operation_id": "cannot-complete-attention", "expected_revision": reviewed["revision"]})
            self.assertTrue(pending_attention.is_error)
            resolved = await self.call(client, "checkpoint", {"task_id": task_id,
                "operation_id": "resolve-attention", "expected_revision": reviewed["revision"],
                "changes": {"attention": None}})
            completed = await self.call(client, "complete_task", {"task_id": task_id,
                "operation_id": "complete-reviewed", "expected_revision": resolved["revision"]})
            self.assertEqual(completed["status"], "completed")

    async def test_stdio_compact_full_section_paging_and_cli_drilldown(self):
        async with self.connect() as client:
            request = {"start_request_id": "compact-records-start",
                       "title": "Synthetic context", "goal": "Read only relevant records"}
            started = await self.call(client, "begin_task", request)
            task_id = started["task_id"]
            decisions = [{"id": f"decision-{i:02d}", "text": f"Synthetic proposal {i}: " + "bounded record " * 120,
                          "status": "proposed"} for i in range(30)]
            changed = await self.call(client, "checkpoint", {"task_id": task_id,
                "operation_id": "record-proposals", "expected_revision": started["revision"],
                "changes": {"decisions": decisions}})
            state_path = Path(started["workspace_path"]) / ".workspace/task.json"
            state_before_reads = state_path.read_bytes()
            compact = await self.call(client, "get_context", {"task_id": task_id})
            full = await self.call(client, "get_context", {"task_id": task_id, "detail": "full"})
            resumed = await self.call(client, "resume_task", {"task_id": task_id})
            resumed_full = await self.call(client, "resume_task", {"task_id": task_id, "detail": "full"})
            restarted = await self.call(client, "begin_task", request)
            existing = await self.call(client, "begin_task", {**request, "task_id": task_id})
            self.assertEqual(compact["projection"]["detail"], "compact")
            self.assertNotIn("operations", compact["state"])
            self.assertEqual(len(full["state"]["decisions"]), 30)
            self.assertLess(len(compact["state"]["decisions"]), 30)
            self.assertLess(len(json.dumps(compact)), len(json.dumps(full)))
            self.assertLessEqual(len(json.dumps(compact, ensure_ascii=False,
                                               separators=(",", ":")).encode("utf-8")), 24576)
            self.assertEqual(resumed["projection"]["detail"], "compact")
            self.assertNotIn("operations", resumed["state"])
            self.assertLessEqual(len(json.dumps(resumed, ensure_ascii=False,
                                               separators=(",", ":")).encode("utf-8")), 24576)
            self.assertEqual(resumed_full["state"], full["state"])
            self.assertEqual(resumed_full["verification"], await self.call(client, "verify", {"task_id": task_id}))
            for result in (restarted, existing):
                self.assertTrue(result["replayed"])
                self.assertEqual(result["revision"], changed["revision"])
                self.assertEqual(result["projection"]["detail"], "compact")
                self.assertNotIn("operations", result["state"])
                self.assertLessEqual(len(json.dumps(result, ensure_ascii=False,
                                                   separators=(",", ":")).encode("utf-8")), 24576)
            page = await self.call(client, "read_records", {"task_id": task_id, "section": "decisions",
                "offset": 2, "limit": 3, "expected_revision": changed["revision"]})
            self.assertEqual(page["total"], 30)
            self.assertEqual(page["next_offset"], 5)
            self.assertEqual([item["record"] for item in page["records"]], full["state"]["decisions"][2:5])
            self.assertTrue(all(item["complete"] for item in page["records"]))
            offset, recovered = 0, []
            while offset is not None:
                records = await self.call(client, "read_records", {"task_id": task_id,
                    "section": "decisions", "offset": offset, "limit": 11,
                    "expected_revision": changed["revision"]})
                recovered.extend(item["record"] for item in records["records"])
                next_offset = records["next_offset"]
                if next_offset is not None:
                    self.assertGreater(next_offset, offset)
                offset = next_offset
            self.assertTrue(records["complete"])
            self.assertEqual(recovered, full["state"]["decisions"])
            stale = await client.call_tool("read_records", {"task_id": task_id, "section": "decisions",
                "expected_revision": started["revision"]})
            self.assertTrue(stale.is_error)
            self.assertEqual(stale.structured_content["error"]["code"], "revision_conflict")
            self.assertEqual(state_path.read_bytes(), state_before_reads)
        for arguments, expected in (
            (["get-context", "--task-id", task_id, "--json"], compact),
            (["get-context", "--task-id", task_id, "--detail", "full", "--json"], full),
            (["resume", "--task-id", task_id, "--json"], resumed),
            (["resume", "--task-id", task_id, "--detail", "full", "--json"], resumed_full),
            (["read-records", "--task-id", task_id, "--section", "decisions", "--offset", "2",
              "--limit", "3", "--expected-revision", str(changed["revision"]), "--json"], page),
        ):
            result = await asyncio.to_thread(subprocess.run,
                [sys.executable, "-m", "retulo", "--config", str(self.config), *arguments],
                cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(state_path.read_bytes(), state_before_reads)

    async def test_stdio_explicit_file_move_and_whole_workspace_attach(self):
        request = {"start_request_id": "transport-move-start", "title": "Synthetic move",
                   "goal": "Preserve a task across an explicit user move"}
        async with self.connect() as client:
            started = await self.call(client, "begin_task", request)
            task_id, workspace = started["task_id"], Path(started["workspace_path"])
            reserved = await self.call(client, "reserve_output", {"task_id": task_id,
                "operation_id": "move-reserve", "expected_revision": started["revision"], "name": "report.md"})
            Path(reserved["write_path"]).write_text("Synthetic move evidence\n")
            committed = await self.call(client, "commit_artifact", {"task_id": task_id,
                "operation_id": "move-commit", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            destination = workspace / "organized/report.md"
            destination.parent.mkdir()
            Path(reserved["final_path"]).rename(destination)
            adopted = await self.call(client, "resume_task", {"task_id": task_id,
                "operation_id": "adopt-user-move", "expected_revision": committed["revision"],
                "path_updates": {reserved["artifact_id"]: "organized/report.md"}})
            self.assertTrue(adopted["verification"]["ok"])
            self.assertEqual(destination.read_text(), "Synthetic move evidence\n")
        moved_root = self.home / "moved-by-user"
        workspace.rename(moved_root)
        async with self.connect() as client:
            attached = await self.call(client, "attach_workspace", {"workspace_path": str(moved_root)})
            self.assertEqual(attached["task_id"], task_id)
            self.assertEqual(attached["workspace_path"], str(moved_root))
            repeated = await self.call(client, "begin_task", request)
            self.assertEqual(repeated["task_id"], task_id)
            self.assertEqual(repeated["workspace_path"], str(moved_root))
            self.assertFalse(workspace.exists())
            verified = await self.call(client, "verify", {"task_id": task_id})
            self.assertTrue(verified["ok"])

    async def test_stdio_abandon_preserves_partial_and_reopen_is_explicit(self):
        async with self.connect() as client:
            started = await self.call(client, "begin_task", {"start_request_id": "transport-reopen-start",
                "title": "Synthetic interruption", "goal": "Preserve partial output and explicit reopen"})
            task_id = started["task_id"]
            reserved = await self.call(client, "reserve_output", {"task_id": task_id,
                "operation_id": "partial-reserve", "expected_revision": started["revision"], "name": "partial.md"})
            partial = Path(reserved["write_path"])
            partial.write_text("Incomplete synthetic bytes\n")
            abandoned = await self.call(client, "abandon_output", {"task_id": task_id,
                "operation_id": "abandon-partial", "expected_revision": reserved["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            self.assertTrue(abandoned["staging_preserved"])
            self.assertEqual(partial.read_text(), "Incomplete synthetic bytes\n")
            late = await client.call_tool("commit_artifact", {"task_id": task_id,
                "operation_id": "late-commit", "expected_revision": abandoned["revision"],
                "reservation_id": reserved["reservation_id"], "token": reserved["token"]})
            self.assertTrue(late.is_error)
            self.assertEqual(late.structured_content["error"]["code"], "reservation_closed")
            reserved2 = await self.call(client, "reserve_output", {"task_id": task_id,
                "operation_id": "complete-reserve", "expected_revision": abandoned["revision"], "name": "complete.md"})
            Path(reserved2["write_path"]).write_text("Completed synthetic bytes\n")
            committed = await self.call(client, "commit_artifact", {"task_id": task_id,
                "operation_id": "complete-commit", "expected_revision": reserved2["revision"],
                "reservation_id": reserved2["reservation_id"], "token": reserved2["token"]})
            completed = await self.call(client, "complete_task", {"task_id": task_id,
                "operation_id": "complete-task", "expected_revision": committed["revision"]})
            inspected = await self.call(client, "resume_task", {"task_id": task_id})
            full_inspected = await self.call(client, "get_context", {"task_id": task_id, "detail": "full"})
            self.assertEqual(full_inspected["state"]["status"], "completed")
            self.assertEqual(inspected["revision"], completed["revision"])
            reopened = await self.call(client, "resume_task", {"task_id": task_id,
                "operation_id": "explicit-reopen", "expected_revision": completed["revision"],
                "reopen": True, "reason": "User requested a further synthetic revision"})
            self.assertTrue(reopened["reopened"])
            context = await self.call(client, "get_context", {"task_id": task_id, "detail": "full"})
            self.assertEqual(context["state"]["status"], "active")
            self.assertEqual(len(context["state"]["completion_history"]), 1)
            self.assertEqual(partial.read_text(), "Incomplete synthetic bytes\n")


if __name__ == "__main__":
    unittest.main()
