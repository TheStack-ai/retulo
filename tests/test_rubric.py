import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retulo.engine import Engine, EngineError
from retulo import engine as engine_module


class RubricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name) / "workspaces")

    def tearDown(self):
        self.tmp.cleanup()

    def begin(self, criteria=None):
        return self.engine.begin_task("rubric-start", "Review", "Produce and review a document", criteria)

    def context(self, task):
        return self.engine.get_context(task["task_id"], detail="full")

    def checkpoint(self, task, op, changes):
        return self.engine.checkpoint(task["task_id"], op, self.context(task)["revision"], changes)

    def artifact(self, task):
        reserved = self.engine.reserve_output(task["task_id"], "reserve", self.context(task)["revision"], "result.txt")
        Path(reserved["write_path"]).write_text("The reviewed content")
        return self.engine.commit_artifact(task["task_id"], "commit", reserved["revision"], reserved["reservation_id"], reserved["token"])

    def assertError(self, code, method, *args, **kwargs):
        with self.assertRaises(EngineError) as raised:
            method(*args, **kwargs)
        self.assertEqual(code, raised.exception.code)

    def test_human_review_implies_approval_and_preserves_requirement_basis(self):
        task = self.begin([{"id": "visual", "text": "Visual matches the reference", "basis": "Supplied reference A", "human_review": "User inspects the rendered document"}])
        criterion = self.context(task)["state"]["completion_criteria"][0]
        self.assertTrue(criterion["requires_approval"])
        self.assertEqual("Supplied reference A", criterion["basis"])
        committed = self.artifact(task)
        checked = self.checkpoint(task, "check", {"criteria_results": [{"criterion": "visual", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"]}]})
        self.assertError("criteria_pending", self.engine.complete_task, task["task_id"], "finish", checked["revision"])
        approved = self.engine.record_approval(task["task_id"], "approval", checked["revision"], committed["artifact"]["artifact_id"], 1,
                                               {"actor": "user", "reference": "user-message:synthetic", "quote": "The rendered document is approved", "scope": "visual"})
        self.assertEqual("completed", self.engine.complete_task(task["task_id"], "finish", approved["revision"])["status"])

    def test_false_human_review_normalizes_without_mutation_and_allows_verified_completion(self):
        supplied = [{"id": "saved", "text": "Verified artifact saved", "basis": "Synthetic request",
                     "human_review": False}]
        before = copy.deepcopy(supplied)
        task = self.begin(supplied)
        criterion = self.context(task)["state"]["completion_criteria"][0]
        self.assertEqual(supplied, before)
        self.assertNotIn("human_review", criterion)
        self.assertFalse(criterion["requires_approval"])
        committed = self.artifact(task)
        checked = self.checkpoint(task, "check", {"criteria_results": [{"criterion": "saved", "status": "passed",
                                  "evidence_ref": committed["artifact"]["artifact_id"]}]})
        self.assertEqual("completed", self.engine.complete_task(task["task_id"], "finish", checked["revision"])["status"])

    def test_false_human_review_preserves_explicit_approval_gate(self):
        task = self.begin([{"id": "approved", "text": "Artifact approved", "human_review": False,
                           "requires_approval": True, "approval_scope": "content"}])
        criterion = self.context(task)["state"]["completion_criteria"][0]
        self.assertTrue(criterion["requires_approval"])
        self.assertNotIn("human_review", criterion)
        committed = self.artifact(task)
        checked = self.checkpoint(task, "check", {"criteria_results": [{"criterion": "approved", "status": "passed",
                                  "evidence_ref": committed["artifact"]["artifact_id"]}]})
        self.assertError("criteria_pending", self.engine.complete_task, task["task_id"], "finish", checked["revision"])
        approved = self.engine.record_approval(task["task_id"], "approval", checked["revision"],
                    committed["artifact"]["artifact_id"], 1, {"actor": "user", "reference": "user-message:synthetic",
                    "quote": "The synthetic content is approved", "scope": "content"})
        self.assertEqual("completed", self.engine.complete_task(task["task_id"], "finish", approved["revision"])["status"])

    def test_false_and_omitted_human_review_replay_without_rewriting_records(self):
        task = self.begin([{"text": "Check result", "human_review": False}])
        before = {p: p.read_bytes() for p in self.engine.workspace_home.rglob("*") if p.is_file()}
        for equivalent in ({"text": "Check result"}, {"text": "Check result", "human_review": False},
                           {"text": "Check result", "requires_approval": False}):
            replayed = self.begin([equivalent])
            self.assertTrue(replayed["replayed"])
            self.assertEqual(task["task_id"], replayed["task_id"])
            self.assertEqual(task["revision"], replayed["revision"])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        for changed in ({"text": "Check result", "human_review": "User evaluates the result"},
                        {"text": "Check result", "human_review": False, "requires_approval": True}):
            self.assertError("idempotency_conflict", self.begin, [changed])
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_false_human_review_replays_prior_omitted_input(self):
        task = self.begin([{"text": "Check result"}])
        replayed = self.begin([{"text": "Check result", "human_review": False}])
        self.assertTrue(replayed["replayed"])
        self.assertEqual(task["task_id"], replayed["task_id"])

    def test_criterion_fields_reject_empty_invalid_and_contradictory_values(self):
        for field in ({"human_review": "User judgment", "requires_approval": False},
                      {"basis": ""}, {"human_review": None}, {"human_review": True},
                      {"human_review": 0}, {"human_review": ""}, {"requires_independent_review": 1}):
            with self.subTest(field=field):
                self.assertError("invalid_input", self.begin, [{"text": "Review", **field}])
        task = self.begin(["Legacy criterion"])
        self.assertEqual({"id": "criterion-1", "text": "Legacy criterion", "requires_approval": False}, self.context(task)["state"]["completion_criteria"][0])

    def test_independent_pass_requires_distinct_review_and_execution_references(self):
        task = self.begin([{"id": "quality", "text": "Independent quality check", "requires_independent_review": True}])
        committed = self.artifact(task)
        base = {"criterion": "quality", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"]}
        for review in (None, {"mode": "self"}, {"mode": "automated"}, {"mode": "independent"},
                       {"mode": "independent", "reviewer_ref": "run:one", "execution_ref": "run:one"}):
            value = {**base, **({"review": review} if review is not None else {})}
            with self.subTest(review=review):
                self.assertError("independent_review_required", self.checkpoint, task, "rejected", {"criteria_results": [value]})
        checked = self.checkpoint(task, "independent-check", {"criteria_results": [{**base, "review": {"mode": "independent", "reviewer_ref": "review:two", "execution_ref": "run:one"}}]})
        value = self.context(task)["state"]["criteria_results"][0]
        self.assertEqual("caller_reported", value["claim_origin"])
        self.assertEqual("review:two", value["review"]["reviewer_ref"])
        self.assertEqual("completed", self.engine.complete_task(task["task_id"], "finish", checked["revision"])["status"])

    def test_completion_rechecks_required_independent_review(self):
        task = self.begin([{"id": "quality", "text": "Independent check", "requires_independent_review": True}])
        committed = self.artifact(task)
        checked = self.checkpoint(task, "check", {"criteria_results": [{"criterion": "quality", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"], "review": {"mode": "independent", "reviewer_ref": "review:two", "execution_ref": "run:one"}}]})
        state_path = Path(task["workspace_path"]) / ".workspace/task.json"
        state = json.loads(state_path.read_text())
        state["criteria_results"][0]["review"] = {"mode": "self"}
        state_path.write_text(json.dumps(state))
        self.assertError("independent_review_required", self.engine.complete_task, task["task_id"], "finish", checked["revision"])

    def test_unknown_and_pending_do_not_satisfy_completion(self):
        task = self.begin([{"id": "quality", "text": "Independent check", "requires_independent_review": True}])
        self.artifact(task)
        for status in ("unknown", "pending"):
            checked = self.checkpoint(task, status, {"criteria_results": [{"criterion": "quality", "status": status, "note": "No verified check yet"}]})
            self.assertError("criteria_pending", self.engine.complete_task, task["task_id"], "finish", checked["revision"])

    def test_passed_review_still_requires_unchanged_registered_artifact(self):
        task = self.begin(["Check bytes"])
        committed = self.artifact(task)
        result = {"criterion": "Check bytes", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"], "review": {"mode": "automated", "execution_ref": "test:one"}}
        Path(task["workspace_path"], committed["artifact"]["path"]).write_text("Changed after registration")
        self.assertError("artifact_changed", self.checkpoint, task, "check", {"criteria_results": [result]})
        state = self.context(task)["state"]
        self.assertEqual(committed["revision"], state["revision"])
        self.assertEqual([], state["criteria_history"])

    def test_history_tracks_changes_not_identical_results_or_operation_replay(self):
        task = self.begin(["Check content"])
        committed = self.artifact(task)
        result = {"criterion": "Check content", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"], "note": "Original exact finding", "review": {"mode": "self"}}
        changes = {"criteria_results": [result]}
        checked = self.checkpoint(task, "check", changes)
        replayed = self.engine.checkpoint(task["task_id"], "check", committed["revision"], changes)
        self.assertTrue(replayed["replayed"])
        self.checkpoint(task, "same-result", changes)
        state = self.context(task)["state"]
        self.assertEqual(1, len(state["criteria_history"]))
        first = copy.deepcopy(state["criteria_history"][0])
        self.assertEqual(checked["revision"], first["revision"])
        self.assertTrue(first["recorded_at"])
        self.assertEqual("Original exact finding", first["result"]["note"])
        self.checkpoint(task, "new-result", {"criteria_results": [{**result, "status": "failed", "note": "Review found a defect"}]})
        state = self.context(task)["state"]
        self.assertEqual(2, len(state["criteria_history"]))
        self.assertEqual(first, state["criteria_history"][0])
        self.assertEqual("failed", state["criteria_results"][0]["status"])

    def test_history_is_not_duplicated_by_transaction_recovery(self):
        task = self.begin(["Check content"])
        changes = {"criteria_results": [{"criterion": "Check content", "status": "unknown", "note": "Check interrupted"}]}
        real = engine_module.atomic_json
        def interrupted(path, payload):
            if Path(path).name == "task.json" and payload["revision"] == 2:
                raise OSError("Synthetic interruption before state commit")
            return real(path, payload)
        with patch.object(engine_module, "atomic_json", interrupted):
            with self.assertRaises(OSError):
                self.engine.checkpoint(task["task_id"], "check", 1, changes)
        recovered = self.engine.checkpoint(task["task_id"], "check", 1, changes)
        self.assertTrue(recovered["replayed"])
        self.assertEqual(1, len(self.context(task)["state"]["criteria_history"]))

    def test_attention_persists_until_explicit_clear_and_blocks_completion(self):
        task = self.begin()
        self.artifact(task)
        attention = {"kind": "review", "owner": "user", "reason": "Visual judgment remains", "requested_action": "Inspect the rendered document", "evidence_ref": "preview:one"}
        self.checkpoint(task, "attention", {"attention": attention, "mode": "expanded"})
        unchanged = self.checkpoint(task, "next", {"next_action": "Continue independent checks"})
        context = self.context(task)
        self.assertEqual("active", context["state"]["status"])
        self.assertEqual(attention, context["state"]["attention"])
        self.assertIn("Visual judgment remains", context["guide"])
        self.assertIn("Inspect the rendered document", context["guide"])
        self.assertError("attention_pending", self.engine.complete_task, task["task_id"], "finish", unchanged["revision"])
        cleared = self.checkpoint(task, "resolved", {"attention": None})
        self.assertEqual("completed", self.engine.complete_task(task["task_id"], "finish", cleared["revision"])["status"])

    def test_attention_validation_is_atomic_and_does_not_change_task_status(self):
        task = self.begin()
        for bad in ({"kind": "question", "owner": "someone", "reason": "Reason", "requested_action": "Action"},
                    {"kind": "question", "owner": "agent", "reason": "", "requested_action": "Action"},
                    {"kind": "question", "owner": "agent", "reason": "Reason"},
                    {"kind": "question", "owner": "agent", "reason": "Reason", "requested_action": "Action", "status": "paused"}):
            with self.subTest(bad=bad):
                self.assertError("invalid_input", self.checkpoint, task, "bad", {"status": "paused", "attention": bad})
        self.assertEqual("active", self.context(task)["state"]["status"])
        attention = {"kind": "blocked", "owner": "external", "reason": "Awaiting source", "requested_action": "Provide reference"}
        self.checkpoint(task, "blocked-note", {"attention": attention})
        self.assertEqual("active", self.context(task)["state"]["status"])

    def test_old_v1_record_without_new_fields_still_reads_and_mutates(self):
        task = self.begin(["Legacy check"])
        state_path = Path(task["workspace_path"]) / ".workspace/task.json"
        state = json.loads(state_path.read_text())
        state.pop("attention")
        state.pop("criteria_history")
        state_path.write_text(json.dumps(state))
        before = state_path.read_bytes()
        compact = self.engine.get_context(task["task_id"])
        self.assertEqual("unknown", compact["state"]["attention_status"])
        self.assertEqual(0, compact["state"]["counts"]["criteria_history"])
        self.assertEqual(before, state_path.read_bytes())
        self.checkpoint(task, "legacy-result", {"criteria_results": [{"criterion": "Legacy check", "status": "unknown"}]})
        self.assertEqual(1, len(self.context(task)["state"]["criteria_history"]))

    def test_explicit_reopen_and_adopted_edits_record_pending_history(self):
        task = self.begin(["Check content"])
        committed = self.artifact(task)
        checked = self.checkpoint(task, "check", {"criteria_results": [{"criterion": "Check content", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"]}]})
        completed = self.engine.complete_task(task["task_id"], "finish", checked["revision"])
        reopened = self.engine.resume_task(task["task_id"], "reopen", completed["revision"], reopen=True, reason="Revise the document")
        self.assertEqual(["passed", "pending"], [h["result"]["status"] for h in self.context(task)["state"]["criteria_history"]])
        checked = self.checkpoint(task, "check-again", {"criteria_results": [{"criterion": "Check content", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"]}]})
        Path(task["workspace_path"], committed["artifact"]["path"]).write_text("Edited explicitly outside the engine")
        self.engine.resume_task(task["task_id"], "adopt", checked["revision"], adopt_changes=True)
        state = self.context(task)["state"]
        self.assertEqual(["passed", "pending", "passed", "pending"], [h["result"]["status"] for h in state["criteria_history"]])
        self.assertEqual("passed", state["completion_history"][0]["criteria_results"][0]["status"])

    def test_compact_and_paged_reads_preserve_ledger_and_require_revision(self):
        task = self.begin(["Check content"])
        for index, status in enumerate(("unknown", "failed", "pending")):
            self.checkpoint(task, f"check-{index}", {"criteria_results": [{"criterion": "Check content", "status": status}]})
        state_path = Path(task["workspace_path"]) / ".workspace/task.json"
        before = state_path.read_bytes()
        full = self.context(task)
        compact = self.engine.get_context(task["task_id"])
        self.assertEqual("compact", compact["projection"]["detail"])
        self.assertNotIn("operations", compact["state"])
        self.assertEqual(3, compact["state"]["counts"]["operations"])
        first = self.engine.read_records(task["task_id"], "criteria_history", limit=2, expected_revision=full["revision"])
        second = self.engine.read_records(task["task_id"], "criteria_history", offset=first["next_offset"], limit=2, expected_revision=full["revision"])
        self.assertEqual(full["state"]["criteria_history"], [x["record"] for x in first["records"] + second["records"]])
        self.assertEqual(before, state_path.read_bytes())
        self.assertError("revision_conflict", self.engine.read_records, task["task_id"], "criteria_history", expected_revision=1)
        self.assertError("invalid_input", self.engine.get_context, task["task_id"], detail="summary")

    def test_legacy_verdict_is_preserved_before_first_changed_result(self):
        for history_value in (None, []):
            with self.subTest(history=history_value):
                engine = Engine(Path(self.tmp.name) / ("missing-history" if history_value is None else "empty-history"))
                task = engine.begin_task("legacy", "Legacy", "Preserve verdict", ["Check"])
                reserved = engine.reserve_output(task["task_id"], "reserve", 1, "result.txt")
                Path(reserved["write_path"]).write_text("Rechecked content")
                committed = engine.commit_artifact(task["task_id"], "commit", reserved["revision"], reserved["reservation_id"], reserved["token"])
                path = Path(task["workspace_path"]) / ".workspace/task.json"
                state = json.loads(path.read_text())
                old_result = {"criterion": "criterion-1", "status": "failed", "note": "Original finding with exact wording", "evidence_ref": "legacy-report:one", "claim_origin": "caller_reported"}
                state["criteria_results"] = [copy.deepcopy(old_result)]
                if history_value is None:
                    state.pop("criteria_history")
                else:
                    state["criteria_history"] = history_value
                path.write_text(json.dumps(state))
                old_revision = state["revision"]
                change = {"criteria_results": [{"criterion": "Check", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"]}]}
                checked = engine.checkpoint(task["task_id"], "recheck", old_revision, change)
                replayed = engine.checkpoint(task["task_id"], "recheck", old_revision, change)
                self.assertTrue(replayed["replayed"])
                history = engine.get_context(task["task_id"], detail="full")["state"]["criteria_history"]
                self.assertEqual(2, len(history))
                self.assertEqual(old_result, history[0]["result"])
                self.assertEqual(old_revision, history[0]["revision"])
                self.assertEqual("legacy_snapshot", history[0]["kind"])
                self.assertIsNone(history[0]["recorded_at"])
                self.assertEqual("unknown", history[0]["recorded_at_status"])
                self.assertEqual(history[1]["recorded_at"], history[0]["observed_at"])
                self.assertEqual(checked["revision"], history[1]["revision"])
                self.assertEqual("passed", history[1]["result"]["status"])

    def test_large_resume_and_begin_replay_are_bounded_without_rewriting_state(self):
        criteria = [{"id": f"c-{index}", "text": "검토 항목 " * 100, "basis": "원본 근거 " * 100,
                     "human_review": "사람의 판단 " * 100} for index in range(100)]
        args = ("large", "Large task", "A large record", criteria)
        task = self.engine.begin_task(*args)
        self.assertLessEqual(len(json.dumps(task, ensure_ascii=False, separators=(",", ":")).encode()), 24576)
        committed = self.artifact(task)
        path = Path(task["workspace_path"]) / ".workspace/task.json"
        state = json.loads(path.read_text())
        # A synthetic long-lived record with repeated historical artifact
        # versions and operation receipts tests response growth independently
        # of expensive filesystem setup or task mutation loops.
        original = state["artifacts"][0]
        state["artifacts"] = [{**original, "revision": index + 1} for index in range(400)]
        state["latest"][original["artifact_id"]] = 400
        state["operations"].update({f"historical-{index}": {"payload_digest": str(index), "result": {"note": "old result " * 200}}
                                    for index in range(200)})
        state["decisions"] = [{"id": f"d-{index}", "text": "A source-grounded decision " * 100, "status": "confirmed", "evidence_ref": f"source:{index}"}
                              for index in range(100)]
        path.write_text(json.dumps(state))
        before = path.read_bytes()
        receipt = next((self.engine.state_home / "starts").glob("*.json"))
        receipt_before = receipt.read_bytes()
        resumed = self.engine.resume_task(task["task_id"])
        repeated = self.engine.begin_task(*args)
        existing = self.engine.begin_task("existing", "Existing", "Continue", task_id=task["task_id"])
        for response in (resumed, repeated, existing):
            self.assertEqual("compact", response["projection"]["detail"])
            self.assertLessEqual(len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()), 24576)
        self.assertTrue(repeated["replayed"])
        self.assertTrue(existing["replayed"])
        self.assertEqual(400, resumed["verification"]["artifact_count"])
        self.assertNotIn("artifacts", resumed["verification"])
        full = self.engine.resume_task(task["task_id"], detail="full")
        self.assertEqual(state, full["state"])
        self.assertEqual(400, len(full["verification"]["artifacts"]))
        self.assertEqual(self.engine.verify(task["task_id"]), full["verification"])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(receipt_before, receipt.read_bytes())
        self.assertError("invalid_input", self.engine.resume_task, task["task_id"], detail="summary")


if __name__ == "__main__":
    unittest.main()
