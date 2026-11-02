import copy
import json
import unittest
from unittest.mock import patch

from retulo.fs import EngineError
from retulo.projection import MAX_RESPONSE_BYTES, compact_context, compact_recipe_check, plan_page, record_page


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def fixture():
    return {
        "task_id": "d512516e-83da-4c15-b30c-3efebbb773fe", "revision": 7,
        "workspace_path": "/synthetic/tasks/example",
        "state": {
            "task_id": "d512516e-83da-4c15-b30c-3efebbb773fe", "revision": 7,
            "schema_version": 1, "title": "Proposal", "goal": "Create a verified proposal",
            "status": "active", "mode": "expanded", "next_action": "Review the new result",
            "completion_criteria": [
                {"id": "one", "text": "Has source references", "basis": "user request",
                 "requires_approval": False, "requires_independent_review": True},
                {"id": "two", "text": "Design approved", "human_review": "Visual quality",
                 "requires_approval": True}],
            "criteria_results": [{"criterion": "one", "status": "passed", "evidence_ref": "artifact:a:2"}],
            "criteria_history": [{"criterion": "one", "status": "failed", "note": "Missing references"}],
            "completion_history": [{"completed_at": "2026-01-01", "reason": "Explicit further work"}],
            "artifacts": [{"artifact_id": "a", "revision": 1, "path": "proposal-v001.md", "sha256": "old"},
                          {"artifact_id": "a", "revision": 2, "path": "proposal-v002.md", "sha256": "new"}],
            "latest": {"a": 2}, "sources": [], "bindings": [],
            "decisions": [{"id": "old", "text": "Old", "status": "superseded"},
                          {"id": "new", "text": "Current", "status": "confirmed", "evidence_ref": "source-1"},
                          {"id": "idea", "text": "Suggestion", "status": "proposed"}],
            "approvals": [{"approval_id": "approved-1", "artifact_id": "a", "artifact_revision": 1,
                           "sha256": "old", "scope": "draft", "snapshot_path": ".workspace/approved/a-v001"}],
            "operations": {"write": {"result": {"token": "private-reservation-token"}}},
            "reservations": {"r": {"status": "committed", "token_hash": "private-token-hash"}},
        },
        "guide": "Long guide " * 2000, "warnings": ["guide_manually_modified"],
    }


def workflow_fixture():
    result = fixture()
    state = result["state"]
    state.update(
        schema_version=2,
        workflow={
            "revision": 2, "generation": 3, "digest": "d" * 64,
            "definition": {
                "workflow_schema_version": 1,
                "inputs": {"sales": {"required": True}, "logo": {"required": False}},
                "parameters": {"period": {"type": "string", "required": True}},
                "environment": {"renderer": {"required": True}},
                "steps": {
                    "calculate": {"action": {"kind": "agent", "instruction": "private-workflow-instruction"}},
                    "render": {"action": {"kind": "agent", "instruction": "render output"}},
                },
                "targets": {"report": {"outputs": [{"step": "render", "output": "report"}]}},
            },
            "parameters": {"period": "private-current-parameter"},
            "environment": {"renderer": "private-environment-detail"},
        },
        workflow_history=[{"revision": 1, "definition": {"private": "historical-definition"}}],
        input_bindings={"sales": {"ref": "source:sales-2", "mode": "follow"}},
        runs=[
            {"run_id": "first", "step_id": "calculate", "status": "succeeded", "outputs": {"totals": "artifact:t:1"}},
            {"run_id": "second", "step_id": "render", "status": "failed", "evidence_ref": "private-failure-reference"},
            {"run_id": "third", "step_id": "render", "status": "started"},
        ],
        active_outputs={"calculate": {"run_id": "first", "outputs": {"totals": "artifact:t:1"}}},
        current_deliverable_set={
            "target_ids": ["report"],
            "outputs": {"report": [{"step": "render", "output": "report", "ref": "artifact:a:2", "sha256": "new"},
                                    {"step": "render", "output": "chart", "ref": "artifact:c:1", "sha256": "chart"}]},
            "run_ids": ["first", "old-render"], "workflow_digest": "c" * 64,
            "input_digest": "e" * 64, "recorded_at": "2026-01-01T00:00:00Z",
        },
        recipe_binding={
            "recipe_id": "2173f161-7662-4eb5-ae66-178338008c37", "revision": 4,
            "sha256": "a" * 64, "semantic_digest": "b" * 64,
            "origin_task_id": "54cb033f-c859-4b6c-8fba-b17737d9f2ef", "artifact_ref": "artifact:recipe:4",
            "manifest": {"private": "private-recipe-manifest"},
        },
    )
    return result


class ProjectionTests(unittest.TestCase):
    def test_compact_preserves_meaning_without_receipts_or_guide(self):
        original = fixture()
        before = encoded(original)
        result = compact_context(original)
        self.assertEqual(encoded(original), before)
        self.assertEqual(result["task_id"], original["task_id"])
        self.assertEqual(result["state_path"], original["workspace_path"] + "/.workspace/task.json")
        self.assertEqual(result["state"]["criteria_summary"]["statuses"], {"passed": 1, "unknown": 1})
        self.assertEqual(result["state"]["criteria_summary"]["unreviewed"], 1)
        self.assertEqual(result["state"]["attention_status"], "unknown")
        self.assertEqual([a["revision"] for a in result["state"]["artifacts"]], [2])
        self.assertEqual(result["state"]["approvals"][0]["artifact_revision"], 1)
        self.assertTrue(result["state"]["approval_summary"]["latest_does_not_mean_approved"])
        self.assertEqual(result["state"]["decision_summary"], {"superseded": 1, "confirmed": 1, "proposed": 1})
        self.assertNotIn("guide", result)
        self.assertNotIn("operations", result["state"])
        self.assertNotIn("reservations", result["state"])
        self.assertNotIn(b"private-reservation-token", encoded(result))
        self.assertNotIn(b"private-token-hash", encoded(result))
        self.assertEqual(result["warnings"], ["guide_manually_modified"])
        self.assertEqual(result["projection"]["omitted"]["criteria_history"]["omitted"], 1)
        self.assertEqual(result["state"]["counts"]["completion_history"], 1)
        self.assertEqual(result["projection"]["read"]["full"]["arguments"]["detail"], "full")
        self.assertEqual(result["projection"]["response_bytes"], len(encoded(result)))

    def test_large_ledger_and_korean_fields_remain_bounded_and_explicit(self):
        original = fixture()
        original["workspace_path"] = "/" + "경로" * 680  # near POSIX path limit in bytes
        state = original["state"]
        state["goal"] = "중요한 현재 목표" * 10000
        state["next_action"] = "다음 행동 확인" * 1000
        state["operations"] = {f"op-{n:05}": {"result": {"token": "secret", "body": "가" * 500}}
                               for n in range(2500)}
        state["completion_criteria"] = [{"id": str(n), "text": "중요 기준" * 500,
                                          "basis": "근거" * 500, "human_review": "판단" * 500,
                                          "requires_approval": True} for n in range(100)]
        state["attention"] = {"kind": "question", "owner": "user", "reason": "이유" * 3000,
                              "requested_action": "결정" * 3000}
        before = encoded(original)
        result = compact_context(original)
        self.assertLessEqual(len(encoded(result)), MAX_RESPONSE_BYTES)
        self.assertEqual(result["state"]["counts"]["operations"], 2500)
        self.assertEqual(result["state"]["criteria_summary"]["total"], 100)
        self.assertEqual(result["state"]["attention_status"], "open")
        self.assertEqual(result["state"]["attention"]["owner"], "user")
        self.assertIn("reason", result["state"]["attention"]["_projection"]["truncated_fields"])
        self.assertGreater(result["projection"]["omitted"]["completion_criteria"]["omitted"], 0)
        self.assertEqual(before, encoded(original))
        self.assertGreater(result["projection"]["original_bytes"], len(encoded(result)) * 50)

    def test_unknown_clear_and_open_attention_are_distinct(self):
        original = fixture()
        self.assertEqual(compact_context(original)["state"]["attention_status"], "unknown")
        original["state"]["attention"] = None
        self.assertEqual(compact_context(original)["state"]["attention_status"], "clear")
        original["state"].update(status="paused", attention={"kind": "blocked", "owner": "external",
                                                           "reason": "Missing data", "requested_action": "Wait"})
        result = compact_context(original)
        self.assertEqual(result["state"]["status"], "paused")
        self.assertEqual(result["state"]["attention"]["reason"], "Missing data")
        self.assertEqual(original["state"]["status"], "paused")

    def test_escaped_strings_cannot_expand_past_wire_budget(self):
        original = fixture()
        for field in ("title", "goal", "kind", "next_action"):
            original["state"][field] = "\x01\n\\\"" * 4000
        original["state"]["attention"] = {
            "kind": "question", "owner": "user", "reason": "\x02" * 10000,
            "requested_action": "\x03" * 10000, "evidence_ref": "\x04" * 10000}
        original["warnings"] = ["\x05" * 10000] * 100
        result = compact_context(original)
        self.assertLessEqual(len(encoded(result)), MAX_RESPONSE_BYTES)
        self.assertEqual(result["projection"]["response_bytes"], len(encoded(result)))
        self.assertEqual(result["state"]["attention_status"], "open")

    def test_current_source_root_and_review_provenance_are_visible(self):
        original = fixture()
        original["state"]["source_root"] = "/synthetic/existing-project"
        original["state"]["criteria_results"][0]["review"] = {
            "mode": "independent", "reviewer_ref": "session-review", "execution_ref": "session-work"}
        result = compact_context(original)
        self.assertEqual(result["state"]["source_root"], "/synthetic/existing-project")
        self.assertEqual(result["state"]["criteria_results"][0]["review"]["mode"], "independent")
        self.assertEqual(result["state"]["criteria_summary"]["claim_origin"],
                         "caller_reported; not independent validation")

    def test_verification_summary_and_replay_are_inside_the_same_budget(self):
        original = fixture()
        original["replayed"] = True
        original["workspace_path"] = "/" + "경로" * 680
        original["state"]["goal"] = "긴 목표" * 2000
        original["state"]["artifacts"] *= 1500
        original["state"]["operations"] = {str(n): {"receipt": "보존 기록" * 500} for n in range(1000)}
        summary = {"ok": False, "revision": 7, "mismatch_count": 1200, "artifact_count": 3000,
                   "mismatch_kinds": ["artifact_changed"],
                   "verification_scope": "registered_file_bytes_and_guide; no_content_quality_or_user_approval_claim",
                   "preview": "검사 결과" * 70,
                   "read": {"tool": "resume_task", "arguments": {"task_id": original["task_id"], "detail": "full"}}}
        before, summary_before = encoded(original), encoded(summary)
        result = compact_context(original, verification=summary)
        self.assertLessEqual(len(encoded(result)), MAX_RESPONSE_BYTES)
        self.assertEqual(result["projection"]["response_bytes"], len(encoded(result)))
        self.assertEqual(result["verification"], summary)
        self.assertIs(result["replayed"], True)
        self.assertEqual(before, encoded(original))
        self.assertEqual(summary_before, encoded(summary))
        result["verification"]["read"]["arguments"]["detail"] = "changed"
        self.assertEqual(summary["read"]["arguments"]["detail"], "full")

    def test_verification_cannot_bypass_budget_or_replace_context_fields(self):
        original = fixture()
        for value in ([], True, "verification", {"raw": "가" * 1000}, {"raw": "\x01" * 400}, {"raw": object()}):
            with self.subTest(value=type(value).__name__), self.assertRaises(EngineError):
                compact_context(original, verification=value)
        result = compact_context(original, verification={"state": "not an overlay", "revision": 99, "ok": False})
        self.assertEqual(result["revision"], 7)
        self.assertEqual(result["state"]["status"], "active")
        self.assertFalse(result["verification"]["ok"])

    def test_optional_replay_flag_requires_actual_boolean(self):
        original = fixture()
        self.assertNotIn("replayed", compact_context(original))
        for replayed in (True, False):
            original["replayed"] = replayed
            self.assertIs(compact_context(original)["replayed"], replayed)
        for invalid in (1, 0, "true", None):
            original["replayed"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(EngineError):
                compact_context(original)

    def test_record_page_preserves_exact_records_and_advances(self):
        original = fixture()
        before = encoded(original)
        page = record_page(original, "artifacts", limit=1, expected_revision=7)
        self.assertEqual(page["records"][0]["record"], original["state"]["artifacts"][0])
        self.assertTrue(page["records"][0]["complete"])
        self.assertFalse(page["complete"])
        self.assertEqual(page["next_offset"], 1)
        end = record_page(original, "artifacts", offset=page["next_offset"], limit=1, expected_revision=7)
        self.assertTrue(end["complete"])
        self.assertIsNone(end["next_offset"])
        self.assertEqual(before, encoded(original))

    def test_dictionary_pages_are_stable_and_not_insertion_order(self):
        original = fixture()
        original["state"]["operations"] = {"z": {"x": 1}, "a": {"x": 2}, "m": {"x": 3}}
        first = record_page(original, "operations", limit=2)
        self.assertEqual([row["key"] for row in first["records"]], ["a", "m"])
        last = record_page(original, "operations", offset=first["next_offset"])
        self.assertEqual(last["records"][0]["key"], "z")

    def test_large_records_do_not_make_pages_unbounded_or_stall(self):
        original = fixture()
        original["state"]["operations"] = {f"k{n:03}": {"body": "측정" * 40000} for n in range(50)}
        before = encoded(original)
        page = record_page(original, "operations", limit=50)
        self.assertLessEqual(len(encoded(page)), MAX_RESPONSE_BYTES)
        self.assertGreater(len(page["records"]), 0)
        self.assertFalse(page["complete"])
        self.assertFalse(page["records"][0]["complete"])
        self.assertEqual(page["projection"]["incomplete_records"][0], 0)
        self.assertIn("full", page["projection"]["read"])
        if page["next_offset"] is not None:
            self.assertGreater(page["next_offset"], 0)
        self.assertEqual(before, encoded(original))

    def test_bad_page_arguments_and_stale_revision_are_rejected(self):
        original = fixture()
        for arguments in ({"section": "../secrets"}, {"section": "artifacts", "offset": True},
                          {"section": "artifacts", "limit": False}, {"section": "artifacts", "limit": 0},
                          {"section": "artifacts", "limit": 51}, {"section": "artifacts", "offset": -1},
                          {"section": "artifacts", "expected_revision": True}):
            with self.subTest(arguments=arguments), self.assertRaises(EngineError):
                record_page(original, **arguments)
        with self.assertRaises(EngineError) as caught:
            record_page(original, "artifacts", expected_revision=6)
        self.assertEqual(caught.exception.code, "revision_conflict")

    def test_out_of_range_page_is_an_explicit_empty_end(self):
        page = record_page(fixture(), "artifacts", offset=100)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["records"], [])
        self.assertTrue(page["complete"])
        self.assertIsNone(page["next_offset"])

    def test_workflow_summary_is_static_and_does_not_expose_definition_or_recipe(self):
        original = workflow_fixture()
        before = encoded(original)
        with patch("pathlib.Path.open", side_effect=AssertionError("Projection must not read files")), \
             patch("pathlib.Path.stat", side_effect=AssertionError("Projection must not inspect files")):
            result = compact_context(original)
        state = result["state"]
        summary = state["workflow_summary"]
        self.assertEqual((summary["revision"], summary["generation"]), (2, 3))
        self.assertEqual((summary["input_count"], summary["step_count"], summary["target_count"]), (2, 2, 1))
        self.assertEqual((summary["parameter_count"], summary["environment_count"]), (1, 1))
        self.assertEqual((summary["bound_input_count"], summary["active_step_count"]), (1, 1))
        self.assertEqual(summary["freshness"], "not_inspected")
        self.assertEqual(summary["read"]["tool"], "plan_rebuild")
        self.assertEqual(state["run_summary"]["statuses"], {"succeeded": 1, "failed": 1, "started": 1})
        self.assertIn("not current reusability", state["run_summary"]["meaning"])
        self.assertEqual(state["deliverable_summary"]["freshness"], "not_inspected")
        self.assertEqual(state["deliverable_summary"]["output_count"], 2)
        self.assertEqual(state["deliverable_summary"]["run_count"], 2)
        self.assertEqual(state["recipe_summary"]["revision"], 4)
        self.assertEqual(state["recipe_summary"]["artifact_ref"], "artifact:recipe:4")
        for name in ("workflow", "workflow_history", "runs", "input_bindings", "active_outputs",
                     "current_deliverable_set", "recipe_binding", "steps", "targets"):
            self.assertNotIn(name, state)
            self.assertEqual(result["projection"]["omitted"][name]["shown"], 0)
        for private in ("private-workflow-instruction", "private-current-parameter", "private-environment-detail",
                        "private-failure-reference", "private-recipe-manifest"):
            self.assertNotIn(private.encode(), encoded(result))
        self.assertEqual(before, encoded(original))

    def test_v2_sections_page_exact_records_and_bind_to_revision(self):
        original = workflow_fixture()
        state = original["state"]
        expected = {
            "workflow": [state["workflow"]], "workflow_history": state["workflow_history"],
            "input_bindings": [state["input_bindings"]["sales"]], "runs": state["runs"],
            "active_outputs": [state["active_outputs"]["calculate"]],
            "current_deliverable_set": [state["current_deliverable_set"]],
            "recipe_binding": [state["recipe_binding"]],
            "steps": list(state["workflow"]["definition"]["steps"].values()),
            "targets": list(state["workflow"]["definition"]["targets"].values()),
        }
        before = encoded(original)
        for section, values in expected.items():
            with self.subTest(section=section):
                page = record_page(original, section, limit=50, expected_revision=7)
                self.assertTrue(page["complete"])
                self.assertEqual(page["total"], len(values))
                self.assertEqual([row["record"] for row in page["records"]], values)
                self.assertEqual(page["projection"]["response_bytes"], len(encoded(page)))
                with self.assertRaises(EngineError) as stale:
                    record_page(original, section, expected_revision=6)
                self.assertEqual(stale.exception.code, "revision_conflict")
        self.assertEqual(before, encoded(original))

    def test_optional_v2_singletons_are_empty_pages_and_not_invented_for_v1(self):
        original = fixture()
        original["state"].update(current_deliverable_set=None, recipe_binding=None)
        compact = compact_context(original)
        self.assertNotIn("workflow_summary", compact["state"])
        self.assertNotIn("recipe_summary", compact["state"])
        for section in ("workflow", "workflow_history", "input_bindings", "runs", "active_outputs",
                        "current_deliverable_set", "recipe_binding", "steps", "targets"):
            with self.subTest(section=section):
                page = record_page(original, section)
                self.assertEqual(page["total"], 0)
                self.assertEqual(page["records"], [])
                self.assertTrue(page["complete"])
        original["state"].update(workflow=workflow_fixture()["state"]["workflow"], schema_version=2)
        self.assertEqual(compact_context(original)["state"]["deliverable_summary"],
                         {"recorded": False, "freshness": "not_inspected"})

    def test_large_workflow_is_bounded_but_individual_steps_remain_pageable(self):
        original = workflow_fixture()
        state = original["state"]
        state["workflow"]["definition"]["steps"] = {
            f"step-{n:04}": {"action": {"kind": "agent", "instruction": "업무 지시" * 20}}
            for n in range(500)}
        state["runs"] = [{"run_id": f"run-{n:04}", "step_id": f"step-{n:04}",
                          "status": "succeeded", "detail": "private-run-log" * 3000} for n in range(100)]
        state["recipe_binding"]["manifest"] = {"large-private-recipe": "비공개" * 30000}
        before = encoded(original)
        result = compact_context(original)
        self.assertLessEqual(len(encoded(result)), MAX_RESPONSE_BYTES)
        self.assertEqual(result["state"]["workflow_summary"]["step_count"], 500)
        self.assertEqual(result["state"]["run_summary"]["statuses"], {"succeeded": 100})
        self.assertEqual(result["projection"]["omitted"]["runs"]["omitted"], 100)
        self.assertNotIn(b"private-run-log", encoded(result))
        workflow_page = record_page(original, "workflow")
        self.assertFalse(workflow_page["records"][0]["complete"])
        self.assertFalse(workflow_page["complete"])
        steps_page = record_page(original, "steps", offset=250, limit=1, expected_revision=7)
        self.assertTrue(steps_page["records"][0]["complete"])
        self.assertEqual(steps_page["records"][0]["key"], "step-0250")
        self.assertEqual(steps_page["records"][0]["record"], state["workflow"]["definition"]["steps"]["step-0250"])
        self.assertEqual(steps_page["next_offset"], 251)
        runs_page = record_page(original, "runs", limit=50)
        self.assertLessEqual(len(encoded(runs_page)), MAX_RESPONSE_BYTES)
        self.assertFalse(runs_page["records"][0]["complete"])
        self.assertGreater(runs_page["next_offset"], 0)
        self.assertEqual(before, encoded(original))

    def test_workflow_metadata_cannot_expand_the_wire_budget(self):
        original = workflow_fixture()
        original["workspace_path"] = "/" + "경로" * 680
        state = original["state"]
        for field in ("title", "goal", "kind", "next_action"):
            state[field] = "\x01\n\\\"" * 4000
        state["workflow"]["digest"] = "\x01" * 10000
        state["recipe_binding"].update(sha256="한글" * 10000, artifact_ref="\x01" * 10000,
                                       semantic_digest="\x02" * 10000)
        state["current_deliverable_set"].update(input_digest="\x03" * 10000,
                                                workflow_digest="\x04" * 10000)
        state["attention"] = {"kind": "question", "owner": "user", "reason": "\x02" * 10000,
                              "requested_action": "\x03" * 10000, "evidence_ref": "\x04" * 10000}
        original["warnings"] = ["\x05" * 10000] * 100
        result = compact_context(original, verification={"detail": "compact", "ok": False, "preview": "결과" * 100})
        self.assertLessEqual(len(encoded(result)), MAX_RESPONSE_BYTES)
        self.assertEqual(result["projection"]["response_bytes"], len(encoded(result)))
        self.assertIn("digest", result["state"]["workflow_summary"]["_projection"]["truncated_fields"])
        self.assertIn("artifact_ref", result["state"]["recipe_summary"]["_projection"]["truncated_fields"])

    def test_invalid_workflow_record_shapes_do_not_silently_look_empty(self):
        for field, bad in (("workflow", []), ("workflow_history", {}), ("runs", {}),
                           ("input_bindings", []), ("active_outputs", []),
                           ("current_deliverable_set", []), ("recipe_binding", [])):
            original = workflow_fixture()
            original["state"][field] = bad
            with self.subTest(field=field), self.assertRaises(EngineError) as caught:
                compact_context(original)
            self.assertEqual(caught.exception.code, "invalid_state")
        original = workflow_fixture()
        original["state"]["workflow"]["definition"]["steps"] = []
        with self.assertRaises(EngineError):
            record_page(original, "steps")

    def test_unknown_run_status_cannot_become_a_dynamic_summary_key(self):
        original = workflow_fixture()
        original["state"]["runs"].extend([
            {"status": "arbitrary-status" * 1000}, {"status": {"unexpected": "object"}},
            {"status": "interrupted"}, {"status": "unresolved"},
        ])
        statuses = compact_context(original)["state"]["run_summary"]["statuses"]
        self.assertEqual(statuses["unknown"], 2)
        self.assertEqual(statuses["interrupted"], 1)
        self.assertEqual(statuses["unresolved"], 1)


class PlanProjectionTests(unittest.TestCase):
    def plan(self, count=128):
        from retulo.planner import plan_rebuild
        definition = {
            "workflow_schema_version": 1, "inputs": {}, "parameters": {}, "environment": {},
            "steps": {f"step-{n:03}": {
                "action": {"kind": "agent", "instruction": "Create a synthetic output"},
                "inputs": {}, "parameters": [], "environment": [],
                "outputs": {"result": {"logical_name": f"result-{n:03}.json", "role": "deliverable", "format": "json"}},
                "reuse_policy": "if_unchanged", "required_checks": [],
            } for n in range(count)},
            "targets": {"all": {"outputs": [{"step": f"step-{n:03}", "output": "result"} for n in range(count)]}},
        }
        plan = plan_rebuild(definition, {}, {}, {}, [], {}, {}, "2026-01-01T00:00:00Z")
        plan.update(task_id="d512516e-83da-4c15-b30c-3efebbb773fe", workspace_path="/synthetic/task",
                    revision=7, observed_revision=7)
        return plan

    def test_valid_128_step_plan_is_bounded_and_pages_all_exact_cards(self):
        full = self.plan()
        before = encoded(full)
        self.assertGreater(len(before), MAX_RESPONSE_BYTES)
        all_ids = []
        offset = 0
        while True:
            page = plan_page(full, offset=offset, limit=19, expected_plan_digest=full["plan_digest"])
            self.assertLessEqual(len(encoded(page)), MAX_RESPONSE_BYTES)
            self.assertEqual(page["projection"]["response_bytes"], len(encoded(page)))
            self.assertEqual(page["plan_digest"], full["plan_digest"])
            self.assertEqual(page["counts"], full["counts"])
            self.assertEqual(page["total_steps"], 128)
            self.assertEqual(page["order"], list(page["steps"]))
            self.assertFalse(page["complete"])
            self.assertEqual(page["omitted"]["steps"], 128 - len(page["steps"]))
            for step_id, card in page["steps"].items():
                self.assertEqual(card, full["steps"][step_id])
            all_ids.extend(page["order"])
            if page["next_offset"] is None:
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        self.assertEqual(all_ids, full["order"])
        self.assertEqual(encoded(full), before)

    def test_small_plan_is_complete_and_full_read_is_explicitly_unbounded(self):
        small = self.plan(4)
        page = plan_page(small)
        self.assertTrue(page["complete"])
        self.assertEqual(page["steps"], small["steps"])
        self.assertEqual(page["ready"], small["ready"])
        self.assertEqual(page["order"], small["order"])
        large = self.plan()
        result = plan_page(large, detail="full", offset=100, limit=1)
        self.assertEqual(result["steps"], large["steps"])
        self.assertEqual(result["order"], large["order"])
        self.assertTrue(result["complete"])
        self.assertFalse(result["projection"]["bounded"])
        self.assertIsNone(result["projection"]["budget_bytes"])
        self.assertGreater(len(encoded(result)), MAX_RESPONSE_BYTES)
        result["steps"]["step-000"]["state"] = "modified"
        self.assertEqual(large["steps"]["step-000"]["state"], "needs_run")

    def test_digest_conflict_is_checked_before_slicing_even_at_same_revision(self):
        full = self.plan(2)
        old_digest = full["plan_digest"]
        full["plan_digest"] = "e" * 64
        for detail in ("compact", "full"):
            with self.subTest(detail=detail), self.assertRaises(EngineError) as caught:
                plan_page(full, detail=detail, offset=9999, expected_plan_digest=old_digest)
            self.assertEqual(caught.exception.code, "plan_conflict")
            self.assertEqual(caught.exception.details["current_plan_digest"], full["plan_digest"])
        self.assertEqual(full["revision"], 7)

    def test_global_ready_preview_is_not_lost_on_a_different_order_page(self):
        full = self.plan(5)
        full["ready"] = ["step-000"]
        for name in full["order"][1:]:
            full["steps"][name].update(state="blocked", signature=None, waiting_on=["step-000"])
        full["counts"] = {"needs_run": 1, "blocked": 4, "unknown": 0, "reusable": 0}
        page = plan_page(full, offset=3, limit=1)
        self.assertEqual(page["order"], ["step-003"])
        self.assertEqual(page["ready"], ["step-000"])
        self.assertEqual(page["projection"]["ready_total"], 1)
        self.assertEqual(page["counts"], full["counts"])

    def test_large_step_reasons_and_inputs_have_explicit_nonactionable_preview(self):
        full = self.plan(1)
        card = full["steps"]["step-000"]
        card.update(
            state="blocked", waiting_on=[f"parent-{n:03}-" + "x" * 80 for n in range(128)],
            reasons=[{"code": "input_changed", "ref": "source:" + "x" * 5000,
                      "description": "\x01한글" * 3000} for _ in range(128)],
            resolved_inputs={f"input-{n}": {"ref": f"source:{n}", "body": "비공개" * 3000} for n in range(128)},
            run_id="run-" + "x" * 4000,
        )
        full["ready"] = []
        full["counts"] = {"needs_run": 0, "blocked": 1, "unknown": 0, "reusable": 0}
        before = encoded(full)
        page = plan_page(full)
        self.assertLessEqual(len(encoded(page)), MAX_RESPONSE_BYTES)
        self.assertFalse(page["complete"])
        preview = page["steps"]["step-000"]
        self.assertEqual(preview["step_id"], "step-000")
        self.assertFalse(preview["_projection"]["complete"])
        self.assertFalse(preview["_projection"]["actionable"])
        self.assertNotIn("waiting_on", preview)
        self.assertEqual(preview["waiting_on_count"], 128)
        self.assertNotIn("run_id", preview)
        self.assertIn("run_id", preview["_projection"]["omitted_fields"])
        self.assertNotIn("resolved_inputs", preview)
        self.assertEqual(page["projection"]["incomplete_steps"], ["step-000"])
        recovery = page["projection"]["read"]["full"]
        self.assertEqual(recovery["tool"], "plan_rebuild")
        self.assertEqual(recovery["arguments"]["detail"], "full")
        self.assertEqual(recovery["arguments"]["expected_plan_digest"], full["plan_digest"])
        self.assertEqual(encoded(full), before)

    def test_plan_pagination_arguments_are_strict_and_projection_never_reads_files(self):
        full = self.plan(2)
        for kwargs in ({"detail": "summary"}, {"detail": []}, {"offset": True}, {"offset": -1},
                       {"limit": False}, {"limit": 0}, {"limit": 51},
                       {"expected_plan_digest": True}, {"expected_plan_digest": "bad"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(EngineError) as caught:
                plan_page(full, **kwargs)
            self.assertEqual(caught.exception.code, "invalid_input")
        with patch("pathlib.Path.open", side_effect=AssertionError("No file reads")), \
             patch("pathlib.Path.stat", side_effect=AssertionError("No file checks")):
            result = plan_page(full, offset=9999)
        self.assertEqual(result["steps"], {})
        self.assertIsNone(result["next_offset"])
        self.assertEqual(result["ready"], full["ready"])


class RecipeCheckProjectionTests(unittest.TestCase):
    def check(self, count=128):
        return {
            "recipe_task_id": "d512516e-83da-4c15-b30c-3efebbb773fe",
            "recipe_artifact_id": "74f92138-0ae3-4b09-a58b-34fc4bec96d7", "recipe_artifact_revision": 3,
            "recipe_id": "2173f161-7662-4eb5-ae66-178338008c37", "revision": 2,
            "sha256": "a" * 64, "semantic_digest": "b" * 64, "schema_valid": True,
            "required_inputs": {f"source-{n:03}": {"required": n % 2 == 0, "format": "csv",
                "description": "private-input-description " + "입력 설명" * 40} for n in range(count)},
            "parameters": {f"parameter-{n:03}": {"type": "string", "required": False,
                "default": "private-default-value", "enum": ["private-default-value", "private-enum-value"],
                "description": "private-parameter-description " + "매개 변수" * 40} for n in range(count)},
            "requirements": {"features": [f"feature_{n}" for n in range(20)]}, "supported": False,
            "qualification": {"status": "unverified", "cases": [
                {"kind": "qualification", "case_id": f"case-{n:03}",
                 "task_id": "54cb033f-c859-4b6c-8fba-b17737d9f2ef",
                 "status": "example_verified" if n % 2 == 0 else "unverified", "reason": "evidence_unavailable"}
                for n in range(32)], "meaning": "Historical case integrity only"},
            "execution_authorized": False,
        }

    def test_large_recipe_check_is_bounded_with_exact_identity_and_global_counts(self):
        full = self.check()
        before = encoded(full)
        self.assertGreater(len(before), MAX_RESPONSE_BYTES)
        result = compact_recipe_check(full)
        self.assertLessEqual(len(encoded(result)), MAX_RESPONSE_BYTES)
        self.assertEqual(result["projection"]["response_bytes"], len(encoded(result)))
        for field in ("recipe_id", "revision", "sha256", "semantic_digest", "schema_valid", "supported",
                      "recipe_task_id", "recipe_artifact_id", "recipe_artifact_revision", "execution_authorized"):
            self.assertEqual(result[field], full[field])
        self.assertEqual(result["totals"], {"required_inputs": 128, "parameters": 128,
                                           "requirements": 20, "qualification_cases": 32})
        self.assertEqual(result["qualification"]["case_statuses"], {"example_verified": 16, "unverified": 16})
        self.assertEqual(result["qualification"]["status"], "unverified")
        self.assertEqual(result["omitted"]["required_inputs"], 128 - len(result["required_inputs"]))
        self.assertEqual(result["omitted"]["qualification_cases"], 32 - len(result["qualification"]["cases"]))
        self.assertTrue(result["parameters"]["parameter-000"]["has_default"])
        self.assertEqual(result["parameters"]["parameter-000"]["enum_count"], 2)
        for private in ("private-input-description", "private-parameter-description", "private-default-value", "private-enum-value"):
            self.assertNotIn(private.encode(), encoded(result))
        self.assertFalse(result["projection"]["complete"])
        self.assertEqual(before, encoded(full))

    def test_recipe_full_recovery_is_pinned_and_preserves_exact_definitions(self):
        full = self.check()
        preview = compact_recipe_check(full)
        recovery = preview["projection"]["read"]["full"]
        self.assertEqual(recovery, {"tool": "check_recipe", "arguments": {
            "recipe_task_id": full["recipe_task_id"], "recipe_artifact_id": full["recipe_artifact_id"],
            "recipe_artifact_revision": 3, "detail": "full"}})
        result = compact_recipe_check(full, detail="full")
        self.assertEqual(result["required_inputs"], full["required_inputs"])
        self.assertEqual(result["parameters"], full["parameters"])
        self.assertEqual(result["qualification"], full["qualification"])
        self.assertGreater(len(encoded(result)), MAX_RESPONSE_BYTES)
        self.assertFalse(result["projection"]["bounded"])
        self.assertTrue(result["projection"]["complete"])
        result["parameters"]["parameter-000"]["default"] = "different"
        self.assertEqual(full["parameters"]["parameter-000"]["default"], "private-default-value")

    def test_output_integrity_case_does_not_become_verified_example_or_unknown(self):
        full = self.check(1)
        full["qualification"]["cases"] = [
            {"kind": "origin", "status": "outputs_verified", "task_id": full["recipe_task_id"]},
            {"kind": "qualification", "status": "example_verified", "task_id": full["recipe_task_id"]},
        ]
        result = compact_recipe_check(full)
        self.assertEqual(result["qualification"]["status"], "unverified")
        self.assertEqual(result["qualification"]["case_statuses"], {"outputs_verified": 1, "example_verified": 1})
        self.assertEqual(result["qualification"]["cases"][0]["status"], "outputs_verified")

    def test_recipe_previews_never_shorten_identifiers_or_imply_empty_values(self):
        full = self.check(1)
        long_id = "case-" + "x" * 5000
        full["qualification"]["cases"] = [{"kind": "qualification", "case_id": long_id,
            "task_id": "54cb033f-c859-4b6c-8fba-b17737d9f2ef", "status": "unverified", "reason": "\x01한글" * 5000}]
        result = compact_recipe_check(full)
        card = result["qualification"]["cases"][0]
        self.assertNotIn("case_id", card)
        self.assertIn("case_id", card["_projection"]["omitted_fields"])
        self.assertNotIn("reason", card)
        self.assertEqual(card["task_id"], full["qualification"]["cases"][0]["task_id"])
        parameter = result["parameters"]["parameter-000"]
        self.assertNotIn("default", parameter)
        self.assertNotIn("enum", parameter)
        self.assertTrue(parameter["has_default"])
        self.assertFalse(parameter["_projection"]["complete"])

    def test_recipe_projection_is_pure_and_rejects_invalid_detail_or_reference(self):
        full = self.check(1)
        with patch("pathlib.Path.open", side_effect=AssertionError("No file reads")), \
             patch("pathlib.Path.stat", side_effect=AssertionError("No file inspection")):
            self.assertTrue(compact_recipe_check(full)["schema_valid"])
        for detail in ("brief", None, []):
            with self.subTest(detail=detail), self.assertRaises(EngineError) as caught:
                compact_recipe_check(full, detail)
            self.assertEqual(caught.exception.code, "invalid_input")
        for field, bad in (("recipe_task_id", None), ("recipe_artifact_revision", True),
                           ("required_inputs", []), ("parameters", []), ("requirements", [])):
            invalid = copy.deepcopy(full)
            invalid[field] = bad
            with self.subTest(field=field), self.assertRaises(EngineError) as caught:
                compact_recipe_check(invalid)
            self.assertEqual(caught.exception.code, "invalid_state")


if __name__ == "__main__":
    unittest.main()
