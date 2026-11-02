"""Recipe integration tests use real temporary files and durable receipts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from retulo.engine import Engine, EngineError
from retulo import engine as engine_module
from retulo.workflow import workflow_digest


def report_workflow():
    return {
        "workflow_schema_version": 1,
        "inputs": {"sales": {"format": "csv"}},
        "parameters": {"period": {"type": "string"},
                       "copies": {"type": "integer", "default": 1}},
        "environment": {"renderer": {}},
        "steps": {
            "report": {
                "action": {"kind": "agent", "instruction": "Write the supplied sales and period"},
                "inputs": {"sales": {"input": "sales"}},
                "parameters": ["period", "copies"], "environment": ["renderer"],
                "outputs": {"report": {"logical_name": "report.txt", "role": "deliverable", "format": "text"}},
                "reuse_policy": "if_unchanged", "required_checks": ["report-content"],
            },
        },
        "targets": {"report": {"outputs": [{"step": "report", "output": "report"}],
                               "required_criteria": ["report-valid"]}},
    }


class RecipeEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.sources = self.base / "sources"
        self.sources.mkdir()
        self.input = self.sources / "sales.csv"
        self.input.write_text("region,total\nNorth,120\nSouth,80\n")
        self.engine = Engine(self.base / "workspaces", allowed_source_roots=[self.sources])
        self.counter = 0
        self.owner = self.engine.begin_task("recipe-authoring", "Recipe authoring", "Keep a reusable recipe")
        self.manifest = {
            "recipe_schema_version": 1, "recipe_id": str(uuid.uuid4()), "revision": 1,
            "title": "Sales report", "workflow": report_workflow(),
            "completion_criteria": [{"id": "report-valid", "text": "The report contains the supplied sales and period",
                                     "requires_approval": False}],
        }
        self.artifact = self.save_manifest(self.manifest)

    def tearDown(self):
        self.tmp.cleanup()

    def op(self, stem):
        self.counter += 1
        return f"{stem}-{self.counter}"

    def context(self, task):
        task_id = task if isinstance(task, str) else task["task_id"]
        return self.engine.get_context(task_id, detail="full")

    def mutate(self, method, task, **arguments):
        task_id = task if isinstance(task, str) else task["task_id"]
        return getattr(self.engine, method)(task_id, self.op(method), self.context(task_id)["revision"], **arguments)

    def store(self, task, name, body, role="recipe", **arguments):
        reserved = self.mutate("reserve_output", task, name=name, role=role, **arguments)
        Path(reserved["write_path"]).write_text(body)
        result = self.mutate("commit_artifact", task, reservation_id=reserved["reservation_id"], token=reserved["token"])
        return result["artifact"]

    def save_manifest(self, manifest):
        return self.store(self.owner, "sales-recipe.json", json.dumps(manifest))

    def instantiate(self, request="new-month", **changes):
        arguments = {
            "start_request_id": request,
            "recipe_task_id": self.owner["task_id"],
            "recipe_artifact_id": self.artifact["artifact_id"],
            "recipe_artifact_revision": self.artifact["revision"],
            "input_bindings": {"sales": {"path": str(self.input)}},
            "parameters": {"period": "2026-11"},
            "title": "November report", "goal": "Create the new report",
            "environment": {"renderer": "fixture-v1"},
        }
        arguments.update(changes)
        return self.engine.begin_from_recipe(**arguments)

    def manifest_path(self, artifact=None):
        return Path(self.owner["workspace_path"]) / (artifact or self.artifact)["path"]

    def assertError(self, code, function, *args, **kwargs):
        with self.assertRaises(EngineError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def run_report(self, task):
        run = self.mutate("begin_step_run", task, step_id="report", dependency_coverage="declared_complete")
        body = f"Period: {run['parameters']['period']}\n" + Path(run["inputs"]["sales"]["path"]).read_text()
        artifact = self.store(task, "report.txt", body, role="deliverable", format="text",
                              run_id=run["run_id"], output_slot="report")
        ref = f"artifact:{artifact['artifact_id']}:{artifact['revision']}"
        finished = self.mutate("finish_step_run", task, run_id=run["run_id"],
                              checks={"report-content": {"status": "passed", "evidence_ref": ref}},
                              evidence_ref="fixture-executor:actual-report-content-check")
        self.assertTrue(finished["activated"])
        self.mutate("checkpoint", task, changes={"criteria_results": [
            {"criterion": "report-valid", "status": "passed", "evidence_ref": ref,
             "note": "Actual report text matched the captured CSV and period"}]})
        return run, artifact

    def test_recipe_check_is_read_only_and_does_not_certify_unrun_examples(self):
        root = Path(self.owner["workspace_path"])
        before = (root / ".workspace/task.json").read_bytes()
        checked = self.engine.check_recipe(self.owner["task_id"], self.artifact["artifact_id"], self.artifact["revision"])
        self.assertTrue(checked["schema_valid"])
        self.assertFalse(checked["execution_authorized"])
        self.assertEqual("unverified", checked["qualification"]["status"])
        self.assertEqual(self.artifact["sha256"], checked["sha256"])
        self.assertEqual(before, (root / ".workspace/task.json").read_bytes())
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_new_task_has_pinned_recipe_and_fresh_execution_state(self):
        task = self.instantiate()
        state = self.context(task)["state"]
        self.assertNotEqual(task["task_id"], self.owner["task_id"])
        self.assertEqual(2, state["schema_version"])
        self.assertEqual("active", state["status"])
        for key in ("approvals", "criteria_results", "criteria_history", "runs", "decisions"):
            self.assertEqual([], state[key])
        self.assertEqual({}, state["active_outputs"])
        self.assertIsNone(state["current_deliverable_set"])
        self.assertEqual(self.artifact["sha256"], state["recipe_binding"]["sha256"])
        self.assertEqual(self.manifest["recipe_id"], state["recipe_binding"]["recipe_id"])
        self.assertEqual("2026-11", state["workflow"]["parameters"]["period"])
        self.assertEqual(1, state["workflow"]["parameters"]["copies"])

    def test_completed_source_approvals_and_decisions_are_not_inherited(self):
        artifact = self.store(self.owner, "owner.txt", "Old result", role="deliverable")
        self.mutate("record_approval", self.owner, artifact_id=artifact["artifact_id"],
                    artifact_revision=artifact["revision"],
                    evidence={"actor": "user", "reference": "synthetic:approval", "quote": "Approved old result", "scope": "old customer"})
        self.mutate("checkpoint", self.owner, changes={"decisions": [
            {"id": "old-customer", "text": "Use the old customer's terminology", "status": "confirmed", "evidence_ref": "synthetic:decision"}]})
        self.mutate("complete_task", self.owner)
        before = self.context(self.owner)["state"]
        task = self.instantiate()
        state = self.context(task)["state"]
        self.assertEqual([], state["approvals"])
        self.assertEqual([], state["decisions"])
        self.assertEqual([], state["criteria_results"])
        self.assertEqual(before, self.context(self.owner)["state"])

    def test_real_file_workflow_from_recipe_can_complete_and_reuse(self):
        task = self.instantiate()
        run, artifact = self.run_report(task)
        result = self.mutate("complete_task", task)
        self.assertEqual("completed", result["status"])
        state = self.context(task)["state"]
        self.assertEqual([run["run_id"]], state["current_deliverable_set"]["run_ids"])
        text = (Path(task["workspace_path"]) / artifact["path"]).read_text()
        self.assertIn("2026-11", text)
        self.assertIn("North,120", text)
        self.assertEqual("reusable", self.engine.plan_rebuild(task["task_id"])["steps"]["report"]["state"])

    def test_exact_creation_retry_preserves_uuid_and_seed(self):
        first = self.instantiate()
        original = self.context(first)["state"]
        second = self.instantiate()
        self.assertTrue(second["replayed"])
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(original, self.context(second)["state"])
        self.assertEqual(2, len(self.engine.list_tasks()["tasks"]))

    def test_receipt_retry_does_not_reread_tampered_manifest_or_moved_source(self):
        first = self.instantiate()
        original_seed = self.context(first)["state"]
        self.manifest_path().write_text("not valid JSON any more")
        self.input.rename(self.sources / "moved-sales.csv")
        second = self.instantiate()
        self.assertTrue(second["replayed"])
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(original_seed, self.context(second)["state"])
        self.assertNotEqual("reusable", self.engine.plan_rebuild(first["task_id"])["steps"]["report"]["state"])

    def test_reusing_request_with_different_inputs_or_parameters_is_conflict(self):
        self.instantiate()
        other = self.sources / "other.csv"
        other.write_text("region,total\nElsewhere,5\n")
        self.assertError("idempotency_conflict", self.instantiate, input_bindings={"sales": {"path": str(other)}})
        self.assertError("idempotency_conflict", self.instantiate, parameters={"period": "2026-12"})

    def test_new_manifest_revision_does_not_change_existing_task(self):
        first = self.instantiate()
        before = self.context(first)["state"]
        manifest = copy.deepcopy(self.manifest)
        manifest["revision"] = 2
        manifest["workflow"]["steps"]["report"]["action"]["instruction"] = "Use the updated report procedure"
        second_artifact = self.save_manifest(manifest)
        self.assertEqual(2, second_artifact["revision"])
        self.assertEqual(before, self.context(first)["state"])
        second = self.instantiate("new-month-v2", recipe_artifact_revision=second_artifact["revision"])
        self.assertEqual(2, self.context(second)["state"]["recipe_binding"]["revision"])

    def test_recipe_claims_with_unknown_run_are_unverified(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["qualification"] = [{"case_id": "unobserved", "task_id": str(uuid.uuid4()),
            "workflow_digest": workflow_digest(manifest["workflow"]), "run_ids": [str(uuid.uuid4())]}]
        artifact = self.save_manifest(manifest)
        checked = self.engine.check_recipe(self.owner["task_id"], artifact["artifact_id"], artifact["revision"])
        self.assertTrue(checked["schema_valid"])
        self.assertEqual("unverified", checked["qualification"]["status"])
        self.assertEqual("unverified", checked["qualification"]["cases"][0]["status"])

    def test_historical_example_outputs_are_verified_then_tamper_is_detected(self):
        task = self.instantiate()
        run, output = self.run_report(task)
        self.mutate("complete_task", task)
        manifest = copy.deepcopy(self.manifest)
        manifest["origin"] = {"task_id": task["task_id"], "workflow_digest": workflow_digest(manifest["workflow"]),
                              "run_ids": [run["run_id"]]}
        artifact = self.save_manifest(manifest)
        checked = self.engine.check_recipe(self.owner["task_id"], artifact["artifact_id"], artifact["revision"])
        self.assertEqual("example_tested", checked["qualification"]["status"])
        (Path(task["workspace_path"]) / output["path"]).write_text("changed after the recorded run")
        changed = self.engine.check_recipe(self.owner["task_id"], artifact["artifact_id"], artifact["revision"])
        self.assertEqual("unverified", changed["qualification"]["status"])

    def test_qualification_cannot_claim_an_unrecorded_workflow_revision(self):
        task = self.instantiate()
        run, _ = self.run_report(task)
        self.mutate("complete_task", task)
        actual = self.context(task)["state"]["workflow"]
        self.assertEqual(1, actual["revision"])
        manifest = copy.deepcopy(self.manifest)
        manifest["origin"] = {
            "task_id": task["task_id"], "workflow_digest": actual["digest"],
            "workflow_revision": 999, "run_ids": [run["run_id"]],
        }
        artifact = self.save_manifest(manifest)
        checked = self.engine.check_recipe(self.owner["task_id"], artifact["artifact_id"], artifact["revision"])
        self.assertTrue(checked["schema_valid"])
        self.assertEqual("unverified", checked["qualification"]["status"])
        self.assertEqual("qualification_mismatch", checked["qualification"]["cases"][0]["reason"])

    def test_successful_outputs_without_completed_criteria_do_not_qualify_recipe(self):
        task = self.instantiate()
        run, _ = self.run_report(task)
        # run_report records a check, but only complete_task finalizes an exact
        # coherent deliverable set for a qualified example.
        manifest = copy.deepcopy(self.manifest)
        manifest["origin"] = {"task_id": task["task_id"], "workflow_digest": workflow_digest(manifest["workflow"]),
                              "run_ids": [run["run_id"]]}
        artifact = self.save_manifest(manifest)
        checked = self.engine.check_recipe(self.owner["task_id"], artifact["artifact_id"], artifact["revision"])
        self.assertEqual("unverified", checked["qualification"]["status"])

    def test_qualification_cannot_mix_successful_runs_from_different_input_versions(self):
        workflow = {
            "workflow_schema_version": 1, "inputs": {"sales": {}},
            "steps": {
                "calculate": {"action": {"kind": "agent", "instruction": "Calculate the supplied total"},
                    "inputs": {"data": {"input": "sales"}},
                    "outputs": {"total": {"logical_name": "total.txt", "role": "intermediate", "format": "text"}},
                    "reuse_policy": "if_unchanged"},
                "report": {"action": {"kind": "agent", "instruction": "Report the supplied total"},
                    "inputs": {"total": {"step": "calculate", "output": "total"}},
                    "outputs": {"report": {"logical_name": "report.txt", "role": "deliverable", "format": "text"}},
                    "reuse_policy": "if_unchanged"},
            },
            "targets": {"report": {"outputs": [{"step": "report", "output": "report"}]}},
        }
        self.mutate("define_workflow", self.owner, definition=workflow)

        def bind_current():
            source = self.mutate("add_source", self.owner, source_path=str(self.input))
            self.mutate("bind_input", self.owner, slot="sales", source_id=source["source"]["source_id"])

        def execute(step, slot, name, role, body):
            run = self.mutate("begin_step_run", self.owner, step_id=step, dependency_coverage="declared_complete")
            self.store(self.owner, name, body, role=role, format="text", run_id=run["run_id"], output_slot=slot)
            self.mutate("finish_step_run", self.owner, run_id=run["run_id"])
            return run["run_id"]

        bind_current()
        execute("calculate", "total", "total.txt", "intermediate", "200")
        old_report = execute("report", "report", "report.txt", "deliverable", "Report: 200")
        self.mutate("complete_task", self.owner)
        self.mutate("resume_task", self.owner, reopen=True, reason="Create the next input version")
        self.input.write_text("region,total\nChanged,400\n")
        bind_current()
        new_calculation = execute("calculate", "total", "total.txt", "intermediate", "400")
        execute("report", "report", "report.txt", "deliverable", "Report: 400")
        self.mutate("complete_task", self.owner)
        manifest = {"recipe_schema_version": 1, "recipe_id": str(uuid.uuid4()), "revision": 1,
                    "title": "Two-stage report", "workflow": workflow,
                    "origin": {"task_id": self.owner["task_id"], "workflow_digest": workflow_digest(workflow),
                               "run_ids": [new_calculation, old_report]}}
        author = self.engine.begin_task("mixed-case-author", "Mixed case manifest", "Inspect claimed historical runs")
        artifact = self.store(author, "mixed-recipe.json", json.dumps(manifest))
        checked = self.engine.check_recipe(author["task_id"], artifact["artifact_id"], artifact["revision"])
        self.assertTrue(checked["schema_valid"])
        self.assertEqual("unverified", checked["qualification"]["status"])

    def test_creation_recovers_after_durable_receipt_before_task_state(self):
        original_write = engine_module.atomic_json
        receipts = []

        def interrupted(path, value):
            path = Path(path)
            if path.parent.name == "starts":
                receipts.append(copy.deepcopy(value))
            if path.name == "task.json":
                raise OSError("injected before task state")
            return original_write(path, value)

        with patch.object(engine_module, "atomic_json", interrupted):
            with self.assertRaises(OSError):
                self.instantiate()
        self.assertEqual(1, len(receipts))
        self.input.write_text("region,total\nChanged,99\n")
        recovered = self.instantiate()
        self.assertTrue(recovered["replayed"])
        self.assertEqual(receipts[0]["initial_state"]["task_id"], recovered["task_id"])
        state = self.context(recovered)["state"]
        self.assertEqual(receipts[0]["initial_state"]["sources"], state["sources"])
        self.assertNotEqual("reusable", self.engine.plan_rebuild(recovered["task_id"])["steps"]["report"]["state"])

    def test_creation_recovers_when_registry_write_is_interrupted(self):
        original_write = engine_module.atomic_json
        captured = []

        def interrupted(path, value):
            if Path(path).name == "index.json":
                captured.append(copy.deepcopy(value))
                raise OSError("injected before registry")
            return original_write(path, value)

        with patch.object(engine_module, "atomic_json", interrupted):
            with self.assertRaises(OSError):
                self.instantiate()
        recovered = self.instantiate()
        self.assertTrue(recovered["replayed"])
        self.assertIn(recovered["task_id"], captured[0])
        self.assertEqual(2, len(self.engine.list_tasks()["tasks"]))

    def test_lost_receipt_write_response_recovers_same_identity(self):
        original_write = engine_module.atomic_json
        receipts = []

        def interrupted(path, value):
            original_write(path, value)
            if Path(path).parent.name == "starts":
                receipts.append(copy.deepcopy(value))
                raise OSError("receipt persisted but response lost")

        with patch.object(engine_module, "atomic_json", interrupted):
            with self.assertRaises(OSError):
                self.instantiate()
        recovered = self.instantiate()
        self.assertTrue(recovered["replayed"])
        self.assertEqual(receipts[0]["initial_state"]["task_id"], recovered["task_id"])

    def test_parameter_type_and_finite_value_errors_create_no_task(self):
        for index, values in enumerate(({"period": "x", "copies": True},
                                        {"period": "x", "copies": 1.5},
                                        {"period": "x", "copies": float("nan")},
                                        {"period": "x", "copies": float("inf")},
                                        {"period": "x", "surprise": "value"}, {})):
            with self.subTest(values=values), self.assertRaises(EngineError):
                self.instantiate(f"invalid-{index}", parameters=values)
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_falsy_non_object_environment_bindings_create_no_task(self):
        for index, value in enumerate((False, [], "", 0)):
            with self.subTest(environment=value), self.assertRaises(EngineError):
                self.instantiate(f"bad-environment-{index}", environment=value)
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_missing_or_extra_file_slots_create_no_task(self):
        for index, value in enumerate(({}, {"sales": {"path": str(self.input)}, "extra": {"path": str(self.input)}})):
            with self.subTest(bindings=value):
                self.assertError("recipe_bindings", self.instantiate, f"bad-slots-{index}", input_bindings=value)
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_sources_outside_scope_relative_paths_and_symlinks_are_rejected(self):
        outside = self.base / "outside.csv"
        outside.write_text("private data")
        linked = self.sources / "linked.csv"
        linked.symlink_to(outside)
        values = [str(outside), "sales.csv", str(linked), str(self.sources)]
        for index, value in enumerate(values):
            with self.subTest(path=value), self.assertRaises(EngineError):
                self.instantiate(f"bad-path-{index}", input_bindings={"sales": {"path": value}})
        self.assertEqual("private data", outside.read_text())
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_manifest_revision_is_exact_and_tampered_bytes_are_rejected(self):
        self.assertError("unknown_artifact", self.instantiate, recipe_artifact_revision=999)
        self.manifest_path().write_text(json.dumps({**self.manifest, "title": "unregistered replacement"}))
        self.assertError("artifact_changed", self.instantiate)
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_compound_artifact_reference_cannot_override_explicit_recipe_revision(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["revision"] = 2
        second = self.save_manifest(manifest)
        compound = f"artifact:{second['artifact_id']}:{second['revision']}"
        with self.assertRaises(EngineError):
            self.engine.check_recipe(self.owner["task_id"], compound, self.artifact["revision"])
        with self.assertRaises(EngineError):
            self.instantiate(recipe_artifact_id=compound, recipe_artifact_revision=self.artifact["revision"])
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_recipe_cannot_request_root_expansion_or_inherit_approval_fields(self):
        for field, value in (("allowed_source_roots", [str(self.base)]), ("approvals", []), ("criteria_results", []), ("status", "completed")):
            manifest = copy.deepcopy(self.manifest)
            manifest[field] = value
            artifact = self.save_manifest(manifest)
            with self.subTest(field=field):
                self.assertError("invalid_recipe", self.engine.check_recipe,
                                 self.owner["task_id"], artifact["artifact_id"], artifact["revision"])


if __name__ == "__main__":
    unittest.main()
