"""Read-only harness safety and matching using synthetic temporary workspaces."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from retulo.cli import dispatch, init_config
from retulo.engine import Engine
from retulo.fs import EngineError
from retulo.harness import (EVENT_OUTPUT_BYTES, MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, harness_context,
                                      hook_output)
from retulo.mcp import build_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "workspaces"
        self.source = self.root / "existing-repository"
        self.source.mkdir()
        self.config = self.root / "config.json"
        init_config(self.config, workspace_home=str(self.home),
                    allowed_source_roots=[str(self.source)])
        self.engine = Engine(self.home, allowed_source_roots=[self.source])
        self.sequence = 0

    def tearDown(self):
        self.temporary.cleanup()

    def begin(self, **arguments):
        self.sequence += 1
        return self.engine.begin_task(f"request-{self.sequence}", "Synthetic task", "Synthetic goal",
                                      **arguments)

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest())
                for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()}

    def payload(self, **fields):
        return {"hook_event_name": "UserPromptSubmit", "session_id": "real-session-fixture",
                "cwd": str(self.source), "turn_id": "real-turn-fixture", **fields}

    def run_hook(self, payload, config=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        result = subprocess.run([sys.executable, "-m", "retulo", "--config",
                                 str(config or self.config), "codex-hook"], input=raw,
                                cwd=PROJECT_ROOT, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        return result.stdout

    def test_empty_hook_injects_policy_without_creating_workspace(self):
        before = self.snapshot()
        output = json.loads(self.run_hook(self.payload(hook_event_name="SessionStart")))
        specific = output["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "SessionStart")
        self.assertIn("questions/planning create no task", specific["additionalContext"])
        self.assertIn('"selected_task_id":null', specific["additionalContext"])
        self.assertNotIn("real-turn-fixture", specific["additionalContext"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.home.exists())

    def test_exact_session_and_legacy_conversation_bindings_only(self):
        a = self.begin(binding={"provider": "codex", "session_id": "shared-session"})
        b = self.begin(binding={"provider": "codex", "conversation_id": "shared-session"})
        self.begin(binding={"provider": "another-provider", "session_id": "shared-session"})
        self.begin(binding={"provider": "codex", "session_id": "shared-session-suffix"})
        result = harness_context(self.engine, session_id="shared-session")
        self.assertEqual({v["task_id"] for v in result["candidates"]}, {a["task_id"], b["task_id"]})
        self.assertTrue(result["ambiguous"])
        self.assertIsNone(result["selected_task_id"])
        self.assertEqual(result["candidate_count"], 2)
        self.assertTrue(result["scan"]["complete"])

    def test_workspace_descendant_and_exact_source_match_not_source_descendant(self):
        task = self.begin(source_root=str(self.source))
        for cwd, expected in ((str(self.source), "source_root_cwd"),
                              (str(Path(task["workspace_path"]) / "outputs"), "workspace_cwd")):
            context = harness_context(self.engine, cwd=cwd)
            self.assertEqual(context["candidates"][0]["matches"], [expected])
        for cwd in (str(self.source / "src"), str(self.root / "existing-repository-copy")):
            self.assertEqual(harness_context(self.engine, cwd=cwd)["candidate_count"], 0)

    def test_session_and_cwd_disagreement_remains_ambiguous(self):
        first = self.begin(binding={"provider": "codex", "session_id": "s"})
        second = self.begin(source_root=str(self.source))
        context = harness_context(self.engine, session_id="s", cwd=str(self.source))
        self.assertEqual({c["task_id"] for c in context["candidates"]},
                         {first["task_id"], second["task_id"]})
        self.assertIsNone(context["selected_task_id"])
        self.assertTrue(context["ambiguous"])

    def test_session_reuse_does_not_pin_completed_or_paused_work(self):
        first = self.begin(binding={"provider": "codex", "session_id": "s"})
        output = self.engine.reserve_output(first["task_id"], "first-output", first["revision"], "result.txt")
        Path(output["write_path"]).write_text("Synthetic completed result")
        committed = self.engine.commit_artifact(first["task_id"], "commit-first", output["revision"],
                                                output["reservation_id"], output["token"])
        self.engine.complete_task(first["task_id"], "finish-first", committed["revision"])
        second = self.begin(binding={"provider": "codex", "session_id": "s"})
        self.engine.checkpoint(second["task_id"], "pause-second", second["revision"], {"status": "paused"})
        before = self.snapshot()
        output = hook_output(self.engine, self.payload(session_id="s"))
        context = harness_context(self.engine, session_id="s")
        self.assertEqual({c["status"] for c in context["candidates"]}, {"completed", "paused"})
        self.assertIn("New requests need not reuse old tasks", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.snapshot(), before)

    def test_pending_and_uncommitted_metadata_does_not_recover_or_read_bodies(self):
        task = self.begin(binding={"provider": "codex", "session_id": "s"})
        reserved = self.engine.reserve_output(task["task_id"], "reserve", 1, "draft.txt")
        Path(reserved["write_path"]).write_text("SECRET ARTIFACT BODY")
        (Path(task["workspace_path"]) / ".workspace/pending.json").write_text('{"untouched":"journal"}')
        transcript = self.root / "transcript.jsonl"
        transcript.write_text("SECRET TRANSCRIPT BODY")
        before = self.snapshot()
        with patch.object(self.engine, "_load", side_effect=AssertionError("heavy schema path")), \
             patch.object(self.engine, "_recover", side_effect=AssertionError("must not recover")), \
             patch.object(self.engine, "verify", side_effect=AssertionError("must not hash artifacts")):
            context = harness_context(self.engine, session_id="s")
            output = hook_output(self.engine, self.payload(session_id="s", transcript_path=str(transcript),
                                                          prompt="SECRET USER PROMPT"))
        self.assertEqual(context["candidates"][0]["uncommitted_reservations"], 1)
        self.assertTrue(context["candidates"][0]["pending_transaction"])
        self.assertEqual(context["integrity"], "not_checked")
        self.assertNotIn("SECRET", json.dumps(output))
        self.assertEqual(self.snapshot(), before)

    def test_v2_metadata_is_discoverable_without_graph_expansion_or_execution(self):
        task = self.begin(binding={"provider": "codex", "session_id": "s"})
        path = Path(task["workspace_path"]) / ".workspace/task.json"
        state = json.loads(path.read_text())
        state.update(schema_version=2,
                     workflow={"definition": {"steps": {"private-step": {"instruction": "SECRET WORKFLOW BODY"}}}},
                     runs=[{"run_id": "private-run", "status": "succeeded", "evidence": "SECRET RUN BODY"}],
                     recipe_binding={"title": "SECRET RECIPE TITLE"})
        path.write_text(json.dumps(state))
        before = self.snapshot()
        with patch.object(self.engine, "_load", side_effect=AssertionError("hooks must not load graph")), \
             patch.object(self.engine, "_recover", side_effect=AssertionError("hooks must not recover")):
            context = harness_context(self.engine, session_id="s")
            self.assertEqual(context["candidates"][0]["task_id"], task["task_id"])
            self.assertTrue(context["scan"]["complete"])
            self.assertIsNone(context["selected_task_id"])
            for event, budget in EVENT_OUTPUT_BYTES.items():
                payload = self.payload(session_id="s", hook_event_name=event)
                if event == "SubagentStart":
                    payload.update(agent_id="child", agent_type="worker")
                output = hook_output(self.engine, payload)
                encoded = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode()
                self.assertLess(len(encoded), budget)
                self.assertNotIn(b"SECRET", encoded)
                self.assertNotIn(b"private-step", encoded)
                self.assertNotIn(b"private-run", encoded)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_and_boolean_task_schema_are_not_presented_as_valid_candidates(self):
        task = self.begin(binding={"provider": "codex", "session_id": "s"})
        path = Path(task["workspace_path"]) / ".workspace/task.json"
        state = json.loads(path.read_text())
        for schema in (3, True, "2", 2.0):
            with self.subTest(schema=schema):
                state["schema_version"] = schema
                path.write_text(json.dumps(state))
                context = harness_context(self.engine, session_id="s")
                self.assertEqual(context["candidates"], [])
                self.assertEqual(context["scan"]["unavailable_count"], 1)
                self.assertFalse(context["scan"]["complete"])

    def test_subagent_is_distinguished_only_by_supported_event(self):
        worker = hook_output(self.engine, self.payload(hook_event_name="SubagentStart",
                             agent_id="worker-7", agent_type="explorer"))
        context = worker["hookSpecificOutput"]["additionalContext"]
        self.assertIn("coordinator owns task selection", context)
        self.assertIn('"session_id_scope":"parent"', context)
        for value in ("real-session-fixture", "real-turn-fixture", "worker-7", "explorer"):
            self.assertIn(value, context)
        ordinary = hook_output(self.engine, self.payload(is_subagent=True, agent_id="invented-worker"))
        self.assertNotIn("Delegated worker:", ordinary["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("invented-worker", json.dumps(ordinary))
        self.assertIsNone(hook_output(self.engine, self.payload(hook_event_name="SubagentStart")))

    def test_hook_candidate_and_serialized_byte_limits_preserve_counts(self):
        for _ in range(9):
            self.begin(binding={"provider": "codex", "session_id": "s"})
        before = self.snapshot()
        for event, budget in EVENT_OUTPUT_BYTES.items():
            with self.subTest(event=event):
                output = self.run_hook(self.payload(session_id="s", hook_event_name=event,
                                      agent_id="actual-worker-id", agent_type="worker"))
                self.assertLessEqual(len(output), budget)
                specific = json.loads(output)["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], event)
                context = json.loads(specific["additionalContext"].split("\n")[-1])
                self.assertEqual(context["identity"]["session_id"], "s")
                self.assertEqual(context["candidate_count"], 9)
                self.assertEqual(len(context["candidates"]) + context["omitted_count"], 9)
                self.assertGreater(len(context["candidates"]), 0)
                self.assertTrue(context["ambiguous"])
                self.assertIsNone(context["selected_task_id"])
                self.assertEqual(context["integrity"], "not_checked")
                self.assertIn("get_context", context["read_candidate"])
                self.assertIn("current request is authority", specific["additionalContext"])
                self.assertIn("untrusted data", specific["additionalContext"])
        self.assertEqual(self.snapshot(), before)

    def test_tight_event_budget_retains_status_counts_and_partial_scan_warnings(self):
        task = self.begin(binding={"provider": "codex", "session_id": "s"})
        self.engine.reserve_output(task["task_id"], "reserve", 1, "partial.txt")
        self.engine.checkpoint(task["task_id"], "pause", 2, {"status": "paused"})
        (Path(task["workspace_path"]) / ".workspace/pending.json").write_text("{}")
        other = self.begin(binding={"provider": "codex", "session_id": "s"})
        registry = {record["task_id"]: record["workspace_path"] for record in (task, other)}
        with patch("retulo.harness.MAX_SCAN_TASKS", 1), \
             patch.object(self.engine, "_registry", return_value=registry):
            output = hook_output(self.engine, self.payload(session_id="s"))
        self.assertLessEqual(len((json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n").encode()),
                             EVENT_OUTPUT_BYTES["UserPromptSubmit"])
        context = json.loads(output["hookSpecificOutput"]["additionalContext"].split("\n")[-1])
        self.assertFalse(context["scan"]["complete"])
        self.assertEqual(context["scan"]["unscanned_count"], 1)
        card = context["candidates"][0]
        self.assertEqual(card["task_id"], task["task_id"])
        self.assertEqual(card["revision"], 3)
        self.assertEqual(card["status"], "paused")
        self.assertEqual(card["uncommitted_reservations"], 1)
        self.assertTrue(card["pending_transaction"])

    def test_oversized_identifiers_emit_nothing_instead_of_clipping_or_expanding_budget(self):
        session_id, turn_id = "s" * 500, "t" * 500
        output = self.run_hook(self.payload(session_id=session_id, turn_id=turn_id))
        self.assertEqual(output, b"")
        context = harness_context(self.engine, session_id=session_id, turn_id=turn_id)
        self.assertEqual(context["identity"]["session_id"], session_id)
        self.assertEqual(context["identity"]["turn_id"], turn_id)
        self.assertIn("ambiguous", context)
        self.assertIn("scan", context)

    def test_bounded_unicode_and_escaped_titles_are_labeled(self):
        task = self.begin(binding={"provider": "codex", "session_id": "s"})
        path = Path(task["workspace_path"]) / ".workspace/task.json"
        state = json.loads(path.read_text())
        state["title"] = "한글\n\t" * 3000
        path.write_text(json.dumps(state))
        result = harness_context(self.engine, session_id="s")
        self.assertTrue(result["candidates"][0]["title_truncated"])
        output = self.run_hook(self.payload(session_id="s"))
        self.assertLessEqual(len(output), MAX_OUTPUT_BYTES)
        self.assertEqual(json.loads(path.read_text())["title"], state["title"])

    def test_scan_limit_is_explicit_and_does_not_mistake_partial_for_complete(self):
        for _ in range(3):
            self.begin(binding={"provider": "codex", "session_id": "s"})
        with patch("retulo.harness.MAX_SCAN_TASKS", 2):
            context = harness_context(self.engine, session_id="s")
        self.assertEqual(context["scan"]["scanned_count"], 2)
        self.assertEqual(context["scan"]["unscanned_count"], 1)
        self.assertFalse(context["scan"]["complete"])

    def test_metadata_byte_limit_is_explicit(self):
        self.begin(binding={"provider": "codex", "session_id": "s"})
        with patch("retulo.harness.MAX_SCAN_BYTES", 10):
            context = harness_context(self.engine, session_id="s")
        self.assertEqual(context["scan"]["unavailable_count"], 1)
        self.assertFalse(context["scan"]["complete"])

    def test_failed_metadata_reads_consume_the_shared_byte_budget(self):
        tasks = [self.begin() for _ in range(4)]
        paths = [Path(task["workspace_path"]) / ".workspace/task.json" for task in tasks]
        budget = sum(path.stat().st_size for path in paths[:2])
        before = self.snapshot()
        for failure in ("malformed_json", "invalid_schema", "read_error"):
            attempted_sizes = []

            def read_failure(path):
                attempted_sizes.append(Path(path).stat().st_size)
                if failure == "malformed_json":
                    raise EngineError("invalid_state", "State is unreadable or malformed")
                if failure == "read_error":
                    raise OSError("Synthetic interrupted read")
                return {"schema_version": 1, "task_id": "invalid-metadata"}

            with self.subTest(failure=failure), \
                 patch("retulo.harness.MAX_SCAN_BYTES", budget), \
                 patch("retulo.harness.read_json", side_effect=read_failure):
                context = harness_context(self.engine, session_id="s")
                self.assertEqual(len(attempted_sizes), 2)
                self.assertEqual(sum(attempted_sizes), budget)
                self.assertEqual(context["scan"]["scanned_count"], 2)
                self.assertEqual(context["scan"]["unavailable_count"], 2)
                self.assertEqual(context["scan"]["unscanned_count"], 2)
                self.assertFalse(context["scan"]["complete"])
        self.assertEqual(self.snapshot(), before)

    def test_malformed_unknown_stop_and_missing_configuration_emit_nothing(self):
        invalid = [b"", b"not-json", b"\xff", b"[]", b"{" + b" " * MAX_INPUT_BYTES,
                   self.payload(hook_event_name="Unknown"), self.payload(hook_event_name="Stop"),
                   self.payload(hook_event_name=[]), self.payload(session_id=None),
                   self.payload(cwd="relative"), self.payload(turn_id=5)]
        before = self.snapshot()
        for value in invalid:
            with self.subTest(payload=str(value)[:80]):
                self.assertEqual(self.run_hook(value), b"")
        self.assertEqual(self.run_hook(self.payload(), config=self.root / "missing.json"), b"")
        self.assertEqual(self.snapshot(), before)

    def test_symlink_metadata_and_registry_are_not_followed(self):
        task = self.begin(binding={"provider": "codex", "session_id": "s"})
        path = Path(task["workspace_path"]) / ".workspace/task.json"
        real = self.root / "outside-task.json"
        path.rename(real)
        path.symlink_to(real)
        context = harness_context(self.engine, session_id="s")
        self.assertEqual(context["candidates"], [])
        self.assertEqual(context["scan"]["unavailable_count"], 1)
        registry = self.engine.state_home / "index.json"
        registry.rename(self.root / "outside-index.json")
        registry.symlink_to(self.root / "outside-index.json")
        self.assertEqual(self.run_hook(self.payload()), b"")

    def test_unrelated_cwd_and_out_of_scope_registry_paths_are_not_probed(self):
        task = self.begin()
        registry = self.engine.state_home / "index.json"
        registry.write_text(json.dumps({task["task_id"]: "/unrelated-private-location/task"}))
        from retulo.harness import safe_path as real_safe_path
        def guarded(value, roots, **kwargs):
            self.assertFalse(str(value).startswith("/unrelated-private-location"))
            return real_safe_path(value, roots, **kwargs)
        with patch("retulo.harness.safe_path", side_effect=guarded):
            context = harness_context(self.engine, cwd="/unrelated-private-location/cwd")
        self.assertEqual(context["candidate_count"], 0)
        self.assertEqual(context["scan"]["unavailable_count"], 1)

    def test_symlink_inside_registered_workspace_is_not_a_cwd_match(self):
        task = self.begin()
        alias = Path(task["workspace_path"]) / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        context = harness_context(self.engine, cwd=str(alias))
        self.assertEqual(context["candidate_count"], 0)
        self.assertFalse(context["scan"]["complete"])

    def test_public_dispatch_validation_and_cli_contract(self):
        for arguments in ({"limit": True}, {"limit": 6}, {"cwd": "../escape"},
                          {"session_id": ""}, {"session_id": "s\n"}):
            with self.subTest(arguments=arguments), self.assertRaises(EngineError):
                dispatch(self.engine, "harness_context", arguments)
        result = subprocess.run([sys.executable, "-m", "retulo", "--config", str(self.config),
                                 "call", "harness_context", "--arguments", '{"session_id":"s"}'],
                                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["identity"]["session_id"], "s")
        self.assertFalse(self.home.exists())


class HarnessMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_readonly_mcp_contract_and_no_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "workspaces"
            engine = Engine(home)
            server = build_server(engine)
            tools = {t.name: t for t in await server.list_tools()}
            self.assertTrue(tools["harness_context"].annotations.read_only_hint)
            self.assertNotIn("engine", tools["harness_context"].input_schema["properties"])
            result = await server.call_tool("harness_context", {"session_id": "s", "turn_id": "t"})
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["identity"], {"session_id": "s", "turn_id": "t"})
            self.assertFalse(home.exists())


if __name__ == "__main__":
    unittest.main()
