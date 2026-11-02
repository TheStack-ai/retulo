"""Filesystem lifecycle checks for incremental work, independent of transports."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retulo.engine import Engine, EngineError
from retulo import engine as engine_module


def report_workflow():
    return {
        "workflow_schema_version": 1,
        "inputs": {"sales": {"format": "csv"}, "logo": {"format": "svg"}},
        "parameters": {"period": {"type": "string", "default": "2026-11"}},
        "environment": {"calculator": {}, "renderer": {}},
        "steps": {
            "aggregate": {
                "action": {"kind": "agent", "instruction": "Sum the supplied sales records"},
                "inputs": {"data": {"input": "sales"}},
                "environment": ["calculator"],
                "outputs": {"totals": {"logical_name": "totals.json", "role": "intermediate", "format": "json"}},
                "reuse_policy": "if_unchanged", "required_checks": ["sum-valid"],
            },
            "branding": {
                "action": {"kind": "agent", "instruction": "Preserve the supplied logo"},
                "inputs": {"logo": {"input": "logo"}},
                "outputs": {"logo": {"logical_name": "brand.svg", "role": "intermediate", "format": "svg"}},
                "reuse_policy": "if_unchanged",
            },
            "report": {
                "action": {"kind": "agent", "instruction": "Write the report using totals and logo"},
                "inputs": {"totals": {"step": "aggregate", "output": "totals"},
                           "logo": {"step": "branding", "output": "logo"}},
                "parameters": ["period"], "environment": ["renderer"],
                "outputs": {"report": {"logical_name": "report.md", "role": "deliverable", "format": "markdown"}},
                "reuse_policy": "if_unchanged", "required_checks": ["report-valid"],
            },
        },
        "targets": {"report": {"outputs": [{"step": "report", "output": "report"}],
                                "required_criteria": ["report-valid"]}},
    }


def split_workflow(policy="if_unchanged"):
    return {
        "workflow_schema_version": 1,
        "inputs": {"source": {}},
        "steps": {"split": {
            "action": {"kind": "agent", "instruction": "Produce both parts from the source"},
            "inputs": {"data": {"input": "source"}},
            "outputs": {"a": {"logical_name": "a.txt", "role": "deliverable", "format": "text"},
                        "b": {"logical_name": "b.txt", "role": "deliverable", "format": "text"}},
            "reuse_policy": policy,
        }},
        "targets": {"pair": {"outputs": [{"step": "split", "output": "a"},
                                             {"step": "split", "output": "b"}]}},
    }


def disjoint_workflow(left_criterion=None):
    definition = {
        "workflow_schema_version": 1,
        "inputs": {"left_source": {}, "right_source": {}},
        "steps": {}, "targets": {},
    }
    for side in ("left", "right"):
        definition["steps"][side] = {
            "action": {"kind": "agent", "instruction": f"Produce the {side} document"},
            "inputs": {"data": {"input": side + "_source"}},
            "outputs": {"result": {"logical_name": side + ".txt", "role": "deliverable", "format": "text"}},
            "reuse_policy": "if_unchanged",
        }
        definition["targets"][side + "_target"] = {"outputs": [{"step": side, "output": "result"}]}
    if left_criterion:
        definition["targets"]["left_target"]["required_criteria"] = [left_criterion]
    return definition


class IncrementalEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.sources = self.base / "sources"
        self.sources.mkdir()
        self.engine = Engine(self.base / "workspaces", allowed_source_roots=[self.sources])
        self.task_id = None
        self.counter = 0

    def tearDown(self):
        self.temporary.cleanup()

    def op(self, label):
        self.counter += 1
        return f"{label}-{self.counter}"

    def state(self):
        return self.engine.get_context(self.task_id, detail="full")["state"]

    def revision(self):
        return self.state()["revision"]

    def mutate(self, method, *args, **kwargs):
        return getattr(self.engine, method)(self.task_id, self.op(method), self.revision(), *args, **kwargs)

    def assertError(self, code, function, *args, **kwargs):
        with self.assertRaises(EngineError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def begin(self, definition=None, criteria=None):
        task = self.engine.begin_task(self.op("begin"), "Incremental report", "Produce useful results", criteria)
        self.task_id = task["task_id"]
        if definition is not None:
            self.mutate("define_workflow", definition)
        return task

    def source(self, name, body, slot=None, *, mode="reference", binding_mode="follow"):
        path = self.sources / name
        path.write_text(body)
        registered = self.mutate("add_source", str(path), mode=mode)
        if slot is not None:
            self.mutate("bind_input", slot, registered["source"]["source_id"], mode=binding_mode)
        return path, registered["source"]

    def update_source(self, path, body, slot):
        path.write_text(body)
        registered = self.mutate("add_source", str(path))
        self.mutate("bind_input", slot, registered["source"]["source_id"])
        return registered["source"]

    def start(self, step_id, *, coverage="declared_complete", plan_digest=None):
        return self.mutate("begin_step_run", step_id,
                           dependency_coverage=coverage, plan_digest=plan_digest)

    def output(self, run, slot, body):
        declaration = run["step"]["outputs"][slot]
        reserved = self.mutate("reserve_output", declaration["logical_name"],
                              role=declaration["role"], format=declaration.get("format"),
                              run_id=run["run_id"], output_slot=slot)
        Path(reserved["write_path"]).write_text(body)
        committed = self.mutate("commit_artifact", reserved["reservation_id"], reserved["token"])
        artifact = committed["artifact"]
        return reserved, artifact, f"artifact:{artifact['artifact_id']}:{artifact['revision']}"

    def finish(self, run, output_refs, *, outcome="succeeded", checks=None):
        if checks is None and outcome == "succeeded":
            checks = {key: {"status": "passed", "evidence_ref": next(iter(output_refs.values()))}
                      for key in run["step"]["required_checks"]}
        return self.mutate("finish_step_run", run["run_id"], outcome=outcome, checks=checks,
                           evidence_ref="test:host-execution")

    def execute(self, step_id):
        run = self.start(step_id)
        data = {port: Path(value["path"]).read_text()
                for port, value in run["inputs"].items() if value.get("path")}
        if step_id == "aggregate":
            total = sum(int(row["amount"]) for row in csv.DictReader(io.StringIO(data["data"])))
            bodies = {"totals": json.dumps({"total": total})}
        elif step_id == "branding":
            bodies = {"logo": data["logo"]}
        elif step_id == "report":
            total = json.loads(data["totals"])["total"]
            bodies = {"report": f"# {run['parameters']['period']}\nTotal: {total}\n{data['logo']}\n"}
        else:
            bodies = {slot: data.get("data", "source") + ":" + slot for slot in run["step"]["outputs"]}
        artifacts, refs = {}, {}
        for slot, body in bodies.items():
            _, artifacts[slot], refs[slot] = self.output(run, slot, body)
        ended = self.finish(run, refs)
        self.assertEqual("succeeded", ended["status"])
        return run, artifacts, refs

    def prepare_report(self):
        self.begin(criteria=[{"id": "report-valid", "text": "The report agrees with its inputs"}])
        self.mutate("define_workflow", report_workflow(), environment={"calculator": "sum-v1", "renderer": "md-v1"})
        sales, _ = self.source("sales.csv", "region,amount\nNorth,10\nSouth,20\n", "sales")
        logo, _ = self.source("logo.svg", '<svg xmlns="http://www.w3.org/2000/svg"><title>Brand</title></svg>', "logo")
        return sales, logo

    def run_report(self):
        results = {step: self.execute(step) for step in ("aggregate", "branding", "report")}
        report_ref = results["report"][2]["report"]
        self.mutate("checkpoint", {"criteria_results": [
            {"criterion": "report-valid", "status": "passed", "evidence_ref": report_ref}]})
        return results

    def test_opt_in_preserves_task_identity_sources_artifacts_and_receipts(self):
        task = self.begin()
        source, registered = self.source("legacy.txt", "original facts")
        reserved = self.mutate("reserve_output", "legacy.md")
        Path(reserved["write_path"]).write_text("Existing result")
        committed = self.mutate("commit_artifact", reserved["reservation_id"], reserved["token"])
        before = self.state()
        self.assertEqual(1, before["schema_version"])
        self.mutate("define_workflow", split_workflow())
        after = self.state()
        self.assertEqual(2, after["schema_version"])
        self.assertEqual(task["task_id"], after["task_id"])
        self.assertEqual(before["sources"], after["sources"])
        self.assertEqual(before["artifacts"], after["artifacts"])
        for key, receipt in before["operations"].items():
            self.assertEqual(receipt, after["operations"][key])
        self.assertEqual("original facts", source.read_text())
        self.assertEqual([], after["runs"])

    def test_workflow_only_calls_reject_legacy_tasks_without_mutation(self):
        self.begin()
        before = self.state()
        self.assertError("workflow_required", self.engine.plan_rebuild, self.task_id)
        self.assertEqual(before, self.state())

    def test_falsy_non_object_runtime_bindings_are_rejected_without_upgrading_task(self):
        for key in ("parameters", "environment"):
            for invalid in (False, [], "", 0):
                with self.subTest(field=key, value=invalid):
                    self.begin()
                    before = self.state()
                    with self.assertRaises(EngineError):
                        self.mutate("define_workflow", split_workflow(), **{key: invalid})
                    self.assertEqual(before, self.state())

    def test_input_snapshot_is_stable_and_original_is_not_written(self):
        self.begin(split_workflow())
        path, _ = self.source("data.txt", "input-one", "source")
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        run = self.start("split")
        snapshot = Path(run["inputs"]["data"]["path"])
        self.assertNotEqual(path, snapshot)
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertEqual("input-one", snapshot.read_text())
        path.write_text("input-two")
        self.assertEqual("input-one", snapshot.read_text())
        self.assertEqual(hashlib.sha256(b"input-one").hexdigest(), run["inputs"]["data"]["sha256"])

    def test_missing_or_changed_historical_snapshot_requires_a_fresh_generation(self):
        for damage in ("remove", "change"):
            with self.subTest(damage=damage):
                self.begin(split_workflow())
                original, _ = self.source(f"snapshot-{damage}.txt", "valid original input", "source")
                first, _, _ = self.execute("split")
                root = Path(self.engine.get_context(self.task_id)["workspace_path"])
                recorded = next(item for item in self.state()["runs"] if item["run_id"] == first["run_id"])
                snapshot = root / recorded["resolved_inputs"]["data"]["snapshot_path"]
                if damage == "remove":
                    snapshot.unlink()
                else:
                    snapshot.chmod(0o600)
                    snapshot.write_text("corrupted historical snapshot")
                self.assertEqual("valid original input", original.read_text())
                plan = self.engine.plan_rebuild(self.task_id)
                self.assertEqual("needs_run", plan["steps"]["split"]["state"])
                self.assertError("workflow_stale", self.mutate, "complete_task")
                historical = next(item for item in self.state()["runs"] if item["run_id"] == first["run_id"])
                self.assertEqual("succeeded", historical["status"])
                second, _, _ = self.execute("split")
                self.assertNotEqual(first["run_id"], second["run_id"])
                self.assertEqual("reusable", self.engine.plan_rebuild(self.task_id)["steps"]["split"]["state"])
                self.mutate("complete_task")
                self.assertEqual("completed", self.state()["status"])

    def test_large_step_start_is_bounded_with_exact_full_state_recovery(self):
        command = "c" * 15000
        parameter = "p" * 16300
        definition = {
            "workflow_schema_version": 1,
            "inputs": {"source": {}},
            "parameters": {"payload": {"type": "string"}},
            "steps": {"large": {
                "action": {"kind": "command", "argv": ["python", "-c", command]},
                "inputs": {"data": {"input": "source"}},
                "parameters": ["payload"],
                "outputs": {"result": {"logical_name": "large.txt", "role": "deliverable", "format": "text"}},
                "reuse_policy": "if_unchanged",
            }},
            "targets": {"result": {"outputs": [{"step": "large", "output": "result"}]}},
        }
        self.begin()
        self.mutate("define_workflow", definition, parameters={"payload": parameter})
        self.source("large-input.txt", "small immutable input", "source")
        run = self.start("large")
        wire = json.dumps(run, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        self.assertLessEqual(len(wire), 24576)
        projection = run["projection"]
        self.assertFalse(projection["complete"])
        self.assertEqual(24576, projection["budget_bytes"])
        self.assertGreater(projection["original_bytes"], len(wire))
        self.assertTrue(any(projection["omitted"].values()))
        recovery = projection["read"]
        self.assertEqual("get_context", recovery["tool"])
        self.assertEqual("full", recovery["arguments"]["detail"])
        self.assertEqual(self.task_id, recovery["arguments"]["task_id"])
        self.assertEqual(run["run_id"], recovery["run_id"])
        full = self.engine.get_context(**recovery["arguments"])["state"]
        persisted = next(item for item in full["runs"] if item["run_id"] == run["run_id"])
        self.assertEqual(parameter, persisted["parameters"]["payload"])
        self.assertEqual(command, full["workflow"]["definition"]["steps"]["large"]["action"]["argv"][2])

    def test_selective_rebuild_reuses_independent_branding_and_preserves_old_report(self):
        sales, _ = self.prepare_report()
        old = self.run_report()
        self.mutate("complete_task")
        old_state = self.state()
        old_report = self.engine._artifact_path(Path(self.engine.get_context(self.task_id)["workspace_path"]),
                                                old_state, old["report"][1]["report"])
        old_bytes = old_report.read_bytes()
        self.mutate("resume_task", reopen=True, reason="Update sales data")
        self.update_source(sales, "region,amount\nNorth,15\nSouth,30\n", "sales")
        self.assertEqual(old_state["current_deliverable_set"], self.state()["current_deliverable_set"])
        plan = self.engine.plan_rebuild(self.task_id)
        self.assertEqual("reusable", plan["steps"]["branding"]["state"])
        self.assertEqual("needs_run", plan["steps"]["aggregate"]["state"])
        self.assertIn("aggregate", plan["steps"]["report"]["waiting_on"])
        self.execute("aggregate")
        _, report_artifacts, report_refs = self.execute("report")
        self.mutate("checkpoint", {"criteria_results": [{"criterion": "report-valid", "status": "passed",
                                                         "evidence_ref": report_refs["report"]}]})
        self.mutate("complete_task")
        self.assertEqual(old_bytes, old_report.read_bytes())
        final = self.state()
        self.assertEqual(1, sum(run["step_id"] == "branding" for run in final["runs"]))
        self.assertEqual(2, sum(run["step_id"] == "aggregate" for run in final["runs"]))
        self.assertEqual(2, sum(run["step_id"] == "report" for run in final["runs"]))
        new_path = Path(self.engine.get_context(self.task_id)["workspace_path"]) / report_artifacts["report"]["path"]
        self.assertIn("Total: 45", new_path.read_text())
        self.assertTrue(self.engine.verify(self.task_id)["ok"])
        self.assertNotEqual(old_state["current_deliverable_set"]["outputs"], final["current_deliverable_set"]["outputs"])

    def test_parameter_change_invalidates_only_steps_that_read_it(self):
        self.prepare_report()
        self.run_report()
        self.mutate("define_workflow", report_workflow(), parameters={"period": "2026-12"})
        plan = self.engine.plan_rebuild(self.task_id)
        self.assertEqual("reusable", plan["steps"]["aggregate"]["state"])
        self.assertEqual("reusable", plan["steps"]["branding"]["state"])
        self.assertEqual("needs_run", plan["steps"]["report"]["state"])

    def test_environment_change_invalidates_only_readers_and_dependants(self):
        self.prepare_report()
        self.run_report()
        self.mutate("define_workflow", report_workflow(), environment={"calculator": "sum-v2", "renderer": "md-v1"})
        plan = self.engine.plan_rebuild(self.task_id)
        self.assertEqual("needs_run", plan["steps"]["aggregate"]["state"])
        self.assertEqual("reusable", plan["steps"]["branding"]["state"])
        self.assertIn("aggregate", plan["steps"]["report"]["waiting_on"])

    def test_source_change_after_plan_rejects_plan_without_starting_run(self):
        self.begin(split_workflow())
        path, _ = self.source("data.txt", "first", "source")
        plan = self.engine.plan_rebuild(self.task_id)
        path.write_text("changed")
        self.assertError("plan_changed", self.start, "split", plan_digest=plan["plan_digest"])
        self.assertEqual([], self.state()["runs"])

    def test_plan_survives_unrelated_checkpoint_with_current_mutation_revision(self):
        self.begin(split_workflow())
        self.source("data.txt", "first", "source")
        plan = self.engine.plan_rebuild(self.task_id)
        self.mutate("checkpoint", {"next_action": "Run the inspected transformation"})
        run = self.start("split", plan_digest=plan["plan_digest"])
        self.assertEqual("started", run["run"]["status"])
        self.assertEqual("first", Path(run["inputs"]["data"]["path"]).read_text())

    def test_target_scoped_plan_can_start_its_step_with_the_same_scope(self):
        self.begin(disjoint_workflow())
        self.source("left.txt", "left input", "left_source")
        self.source("right.txt", "right input", "right_source")
        selected = ["left_target"]
        plan = self.engine.plan_rebuild(self.task_id, target_ids=selected)
        self.assertEqual(["left"], plan["order"])
        self.assertEqual(selected, plan["target_ids"])
        run = self.mutate("begin_step_run", "left", plan_digest=plan["plan_digest"],
                          target_ids=selected, dependency_coverage="declared_complete")
        self.assertEqual("left", run["run"]["step_id"])
        self.assertEqual("left input", Path(run["inputs"]["data"]["path"]).read_text())
        self.assertEqual(1, len(self.state()["runs"]))

    def test_source_change_during_run_preserves_outputs_but_does_not_activate(self):
        self.begin(split_workflow())
        path, _ = self.source("data.txt", "first", "source")
        run = self.start("split")
        paths, refs = [], {}
        for slot in ("a", "b"):
            reserved, _, refs[slot] = self.output(run, slot, "original-input:" + slot)
            paths.append(Path(reserved["final_path"]))
        path.write_text("changed during generation")
        ended = self.finish(run, refs)
        self.assertEqual("stale", ended["status"])
        self.assertFalse(ended["activated"])
        self.assertEqual({}, self.state()["active_outputs"])
        self.assertTrue(all(p.is_file() for p in paths))
        self.assertError("run_closed", self.finish, run, refs)

    def test_source_rebinding_retains_history_without_permanent_mismatch(self):
        self.begin(split_workflow())
        path, first = self.source("data.txt", "first", "source")
        self.execute("split")
        second = self.update_source(path, "second", "source")
        self.assertNotEqual(first["source_id"], second["source_id"])
        self.assertEqual(2, len(self.state()["sources"]))
        self.execute("split")
        self.assertTrue(self.engine.verify(self.task_id)["ok"])
        self.mutate("complete_task")
        self.assertEqual("completed", self.state()["status"])

    def test_pinned_copy_ignores_original_changes_while_follow_does_not(self):
        self.begin(split_workflow())
        path, copied = self.source("data.txt", "pinned", "source", mode="copy", binding_mode="pinned")
        self.execute("split")
        path.write_text("new original")
        self.assertEqual("reusable", self.engine.plan_rebuild(self.task_id)["steps"]["split"]["state"])
        self.assertTrue(self.engine.verify(self.task_id)["ok"])
        referenced = self.mutate("add_source", str(path))["source"]
        self.mutate("bind_input", "source", referenced["source_id"], mode="follow")
        self.assertEqual("needs_run", self.engine.plan_rebuild(self.task_id)["steps"]["split"]["state"])

    def test_pinned_binding_requires_a_preserved_copy(self):
        self.begin(split_workflow())
        _, source = self.source("data.txt", "reference only")
        self.assertError("snapshot_required", self.mutate, "bind_input", "source", source["source_id"], mode="pinned")

    def test_partial_output_never_replaces_the_previous_complete_bundle(self):
        self.begin(split_workflow())
        path, _ = self.source("data.txt", "before", "source")
        self.execute("split")
        old_active = copy.deepcopy(self.state()["active_outputs"])
        self.update_source(path, "after", "source")
        run = self.start("split")
        committed, _, ref = self.output(run, "a", "new-a")
        definition = run["step"]["outputs"]["b"]
        partial = self.mutate("reserve_output", definition["logical_name"], role=definition["role"],
                              format=definition["format"], run_id=run["run_id"], output_slot="b")
        Path(partial["write_path"]).write_text("unfinished b")
        self.assertError("outputs_pending", self.finish, run, {"a": ref})
        self.assertEqual(old_active, self.state()["active_outputs"])
        ended = self.finish(run, {}, outcome="interrupted")
        self.assertEqual("interrupted", ended["status"])
        self.assertEqual("new-a", Path(committed["final_path"]).read_text())
        self.assertEqual("unfinished b", Path(partial["write_path"]).read_text())
        self.assertError("reservation_closed", self.mutate, "commit_artifact", partial["reservation_id"], partial["token"])
        self.assertEqual(old_active, self.state()["active_outputs"])

    def test_required_check_needs_registered_unchanged_evidence(self):
        self.prepare_report()
        run = self.start("aggregate")
        _, _, ref = self.output(run, "totals", '{"total":30}')
        self.assertError("checks_pending", self.finish, run, {"totals": ref}, checks={})
        self.assertError("unknown_artifact", self.finish, run, {"totals": ref},
                         checks={"sum-valid": {"status": "passed", "evidence_ref": "artifact:missing:1"}})
        self.assertEqual({}, self.state()["active_outputs"])
        ended = self.finish(run, {"totals": ref})
        self.assertTrue(ended["activated"])

    def test_changed_output_is_a_conflict_and_is_not_overwritten(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        _, artifacts, _ = self.execute("split")
        root = Path(self.engine.get_context(self.task_id)["workspace_path"])
        path = root / artifacts["a"]["path"]
        path.write_text("human edit")
        plan = self.engine.plan_rebuild(self.task_id)
        self.assertEqual("blocked", plan["steps"]["split"]["state"])
        self.assertError("step_not_ready", self.start, "split")
        self.assertEqual("human edit", path.read_text())

    def test_explicit_adoption_preserves_manual_result_and_allows_a_new_generation(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        _, artifacts, _ = self.execute("split")
        root = Path(self.engine.get_context(self.task_id)["workspace_path"])
        path = root / artifacts["a"]["path"]
        path.write_text("human edit to preserve")
        adopted = self.mutate("resume_task", adopt_changes=True)
        self.assertEqual(1, len(adopted["adopted"]))
        self.assertEqual("human edit to preserve", path.read_text())
        current = self.state()
        adopted_revision = adopted["adopted"][0]["revision"]
        adopted_record = next(item for item in current["artifacts"]
                              if item["artifact_id"] == artifacts["a"]["artifact_id"]
                              and item["revision"] == adopted_revision)
        preserved = root / adopted_record["path"]
        self.assertEqual("human edit to preserve", preserved.read_text())
        plan = self.engine.plan_rebuild(self.task_id)
        self.assertEqual("needs_run", plan["steps"]["split"]["state"])
        self.execute("split")
        self.assertEqual("human edit to preserve", preserved.read_text())

    def test_new_output_version_does_not_inherit_prior_approval(self):
        definition = split_workflow()
        definition["targets"]["pair"]["required_criteria"] = ["accepted"]
        self.begin(criteria=[{"id": "accepted", "text": "User accepted current output",
                              "requires_approval": True, "approval_scope": "deliverable"}])
        self.mutate("define_workflow", definition)
        source, _ = self.source("data.txt", "before", "source")
        _, artifacts, refs = self.execute("split")
        self.mutate("checkpoint", {"criteria_results": [{"criterion": "accepted", "status": "passed",
                                                         "evidence_ref": refs["a"]}]})
        self.mutate("record_approval", artifacts["a"]["artifact_id"], artifacts["a"]["revision"],
                    {"actor": "user", "reference": "test:explicit-user", "quote": "Approve this version",
                     "scope": "deliverable"})
        self.mutate("complete_task")
        original_approval = copy.deepcopy(self.state()["approvals"][0])
        self.mutate("resume_task", reopen=True, reason="New data supplied")
        self.update_source(source, "after", "source")
        _, new_artifacts, new_refs = self.execute("split")
        self.assertNotEqual(artifacts["a"]["revision"], new_artifacts["a"]["revision"])
        self.mutate("checkpoint", {"criteria_results": [{"criterion": "accepted", "status": "passed",
                                                         "evidence_ref": new_refs["a"]}]})
        self.assertError("criteria_pending", self.mutate, "complete_task")
        self.assertEqual([original_approval], self.state()["approvals"])
        snapshot = Path(self.engine.get_context(self.task_id)["workspace_path"]) / original_approval["snapshot_path"]
        self.assertEqual(original_approval["sha256"], hashlib.sha256(snapshot.read_bytes()).hexdigest())

    def test_disjoint_target_cannot_use_another_targets_output_or_approval(self):
        self.begin(criteria=[{"id": "left-approved", "text": "Accept the left target",
                              "requires_approval": True, "approval_scope": "left-final"}])
        self.mutate("define_workflow", disjoint_workflow("left-approved"))
        self.source("left.txt", "left content", "left_source")
        self.source("right.txt", "right content", "right_source")
        self.execute("left")
        _, right_artifacts, right_refs = self.execute("right")
        right = right_artifacts["result"]
        self.mutate("record_approval", right["artifact_id"], right["revision"],
                    {"actor": "user", "reference": "test:right-approval", "quote": "I approve the right file",
                     "scope": "left-final"})
        try:
            self.mutate("checkpoint", {"criteria_results": [{"criterion": "left-approved", "status": "passed",
                                                             "evidence_ref": right_refs["result"]}]})
        except EngineError as exc:
            self.assertEqual("invalid_evidence", exc.code)
        else:
            self.assertError("invalid_evidence", self.mutate, "complete_task")
        self.assertEqual("active", self.state()["status"])
        self.assertIsNone(self.state()["current_deliverable_set"])

    def test_unknown_dependency_coverage_is_not_automatically_reused_or_completed(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        run = self.start("split", coverage="unknown")
        refs = {slot: self.output(run, slot, slot)[2] for slot in ("a", "b")}
        self.finish(run, refs)
        self.assertEqual("unknown", self.engine.plan_rebuild(self.task_id)["steps"]["split"]["state"])
        self.assertError("workflow_stale", self.mutate, "complete_task")

    def test_never_policy_allows_consumption_in_current_generation_but_reruns_after_reopen(self):
        self.begin(split_workflow("never"))
        self.source("data.txt", "original", "source")
        self.execute("split")
        self.assertEqual("reusable", self.engine.plan_rebuild(self.task_id)["steps"]["split"]["state"])
        self.mutate("complete_task")
        old_generation = self.state()["workflow"]["generation"]
        self.mutate("resume_task", reopen=True, reason="New explicitly requested rebuild")
        self.assertGreater(self.state()["workflow"]["generation"], old_generation)
        self.assertEqual("needs_run", self.engine.plan_rebuild(self.task_id)["steps"]["split"]["state"])
        self.assertError("workflow_stale", self.mutate, "complete_task")

    def test_manual_reuse_policy_can_be_resolved_by_an_explicit_new_run(self):
        self.begin(split_workflow("manual"))
        self.source("data.txt", "original", "source")
        self.execute("split")
        self.mutate("complete_task")
        self.mutate("resume_task", reopen=True, reason="Run this workflow again")
        self.assertEqual("unknown", self.engine.plan_rebuild(self.task_id)["steps"]["split"]["state"])
        self.assertError("workflow_stale", self.mutate, "complete_task")
        self.execute("split")
        self.assertEqual("reusable", self.engine.plan_rebuild(self.task_id)["steps"]["split"]["state"])

    def test_failed_generation_preserves_files_and_does_not_publish_a_bundle(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        run = self.start("split")
        paths, refs = [], {}
        for slot in ("a", "b"):
            reservation, _, refs[slot] = self.output(run, slot, "failed-result:" + slot)
            paths.append(Path(reservation["final_path"]))
        failed = self.finish(run, refs, outcome="failed")
        self.assertEqual("failed", failed["status"])
        self.assertFalse(failed["activated"])
        self.assertEqual({}, self.state()["active_outputs"])
        self.assertTrue(all(path.is_file() for path in paths))
        self.execute("split")
        self.assertEqual(["failed", "succeeded"], [item["status"] for item in self.state()["runs"]])
        self.assertTrue(all(path.read_text().startswith("failed-result:") for path in paths))

    def test_duplicate_begin_and_finish_replay_without_extra_run_or_output(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        revision = self.revision()
        run = self.engine.begin_step_run(self.task_id, "stable-begin", revision, "split", dependency_coverage="declared_complete")
        replay = self.engine.begin_step_run(self.task_id, "stable-begin", revision, "split", dependency_coverage="declared_complete")
        self.assertTrue(replay["replayed"])
        self.assertEqual(run["run_id"], replay["run_id"])
        refs = {slot: self.output(run, slot, slot)[2] for slot in ("a", "b")}
        revision = self.revision()
        ended = self.engine.finish_step_run(self.task_id, "stable-finish", revision, run["run_id"], checks={})
        replay = self.engine.finish_step_run(self.task_id, "stable-finish", revision, run["run_id"], checks={})
        self.assertTrue(replay["replayed"])
        self.assertEqual(ended["revision"], replay["revision"])
        self.assertEqual(1, len(self.state()["runs"]))
        self.assertEqual(2, len(self.state()["artifacts"]))

    def test_unresolved_run_must_end_before_a_second_attempt(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        first = self.start("split")
        self.assertError("step_running", self.start, "split")
        self.finish(first, {}, outcome="interrupted")
        second = self.start("split")
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertError("run_closed", self.finish, first, {}, outcome="failed")
        self.assertEqual("started", self.state()["runs"][-1]["status"])

    def test_definition_change_during_run_keeps_result_inactive(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        run = self.start("split")
        refs = {slot: self.output(run, slot, slot)[2] for slot in ("a", "b")}
        updated = split_workflow()
        updated["steps"]["split"]["action"]["instruction"] = "Use a different transformation"
        self.mutate("define_workflow", updated)
        result = self.finish(run, refs)
        self.assertEqual("stale", result["status"])
        self.assertFalse(result["activated"])

    def test_wrong_output_slot_or_path_cannot_be_attached_to_a_run(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        run = self.start("split")
        self.assertError("unknown_output", self.mutate, "reserve_output", "wrong.txt",
                         run_id=run["run_id"], output_slot="missing")
        self.assertError("output_contract", self.mutate, "reserve_output", "wrong.txt",
                         role="deliverable", format="text", run_id=run["run_id"], output_slot="a")
        self.output(run, "a", "first")
        self.assertError("output_reserved", self.output, run, "a", "second")

    def test_completion_requires_current_target_criterion_and_preserves_old_success(self):
        sales, _ = self.prepare_report()
        self.run_report()
        before = self.state()
        passed = copy.deepcopy(before["criteria_results"])
        self.update_source(sales, "region,amount\nNorth,70\n", "sales")
        self.assertEqual("pending", self.state()["criteria_results"][0]["status"])
        self.assertTrue(any(entry["result"]["status"] == "passed" for entry in self.state()["criteria_history"]))
        self.assertError("criteria_pending", self.mutate, "complete_task")
        self.assertTrue(all(run["status"] == "succeeded" for run in self.state()["runs"]))
        self.assertEqual("passed", passed[0]["status"])

    def test_snapshot_publication_recovers_without_rereading_changed_original(self):
        self.begin(split_workflow())
        source, _ = self.source("data.txt", "before crash", "source")
        revision = self.revision()
        original_save = engine_module.atomic_json

        def fail_state_save(path, value):
            if Path(path).name == "task.json":
                raise OSError("injected crash after snapshot publication")
            return original_save(path, value)

        with patch.object(engine_module, "atomic_json", fail_state_save):
            with self.assertRaises(OSError):
                self.engine.begin_step_run(self.task_id, "begin-crash", revision, "split",
                                           dependency_coverage="declared_complete")
        source.write_text("changed after crash")
        recovered = self.engine.begin_step_run(self.task_id, "begin-crash", revision, "split",
                                              dependency_coverage="declared_complete")
        self.assertTrue(recovered["replayed"])
        self.assertEqual("before crash", Path(recovered["inputs"]["data"]["path"]).read_text())
        self.assertEqual(1, len(self.state()["runs"]))

    def test_output_publication_recovers_without_duplicate_run_output(self):
        self.begin(split_workflow())
        self.source("data.txt", "original", "source")
        run = self.start("split")
        declaration = run["step"]["outputs"]["a"]
        reserved = self.mutate("reserve_output", declaration["logical_name"], role=declaration["role"],
                              format=declaration["format"], run_id=run["run_id"], output_slot="a")
        Path(reserved["write_path"]).write_text("complete a")
        original_save = engine_module.atomic_json

        def fail_state_save(path, value):
            if Path(path).name == "task.json":
                raise OSError("injected commit interruption")
            return original_save(path, value)

        with patch.object(engine_module, "atomic_json", fail_state_save):
            with self.assertRaises(OSError):
                self.engine.commit_artifact(self.task_id, "commit-crash", reserved["revision"],
                                            reserved["reservation_id"], reserved["token"])
        recovered = self.engine.commit_artifact(self.task_id, "commit-crash", reserved["revision"],
                                                reserved["reservation_id"], reserved["token"])
        self.assertTrue(recovered["replayed"])
        self.assertEqual("complete a", Path(reserved["final_path"]).read_text())
        self.assertEqual(1, len(self.state()["artifacts"]))
        self.assertEqual(1, len(self.state()["runs"][0]["outputs"]))


if __name__ == "__main__":
    unittest.main()
