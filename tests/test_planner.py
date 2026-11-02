import copy
import hashlib
import unittest
from unittest.mock import patch

from retulo.fs import EngineError
from retulo.planner import (assess_run, execution_signature, plan_rebuild,
                            resolve_step_inputs)
from retulo.workflow import normalize_workflow, step_digest, topological_steps


CHECKED = "2026-11-02T08:00:00+00:00"


def checksum(text):
    return hashlib.sha256(text.encode()).hexdigest()


def observation(content, **changes):
    return {"status": "available", "sha256": checksum(content), "size": len(content),
            "observed_at": CHECKED, "provenance": "engine_observed", **changes}


def workflow():
    return normalize_workflow({
        "workflow_schema_version": 1,
        "inputs": {"data": {"required": True, "format": "csv"},
                   "brand": {"required": True, "format": "markdown"}},
        "parameters": {"label": {"type": "string", "required": True}},
        "environment": {"calculator": {"required": True}, "renderer": {"required": True}},
        "steps": {
            "calculate": {
                "inputs": {"data": {"input": "data"}}, "parameters": [],
                "environment": ["calculator"],
                "outputs": {"totals": {"logical_name": "totals.json", "role": "intermediate", "format": "json"}},
                "action": {"kind": "command", "argv": ["python", "calculate.py"]},
                "reuse_policy": "if_unchanged", "required_checks": ["totals-valid"]},
            "intro": {
                "inputs": {"brand": {"input": "brand"}}, "parameters": [], "environment": [],
                "outputs": {"intro": {"logical_name": "intro.md", "role": "intermediate", "format": "markdown"}},
                "action": {"kind": "agent", "instruction": "Write the introduction from the supplied brand facts"},
                "reuse_policy": "if_unchanged", "required_checks": []},
            "report": {
                "inputs": {"totals": {"step": "calculate", "output": "totals"},
                           "intro": {"step": "intro", "output": "intro"}},
                "parameters": ["label"], "environment": ["renderer"],
                "outputs": {"report": {"logical_name": "report.html", "role": "deliverable", "format": "html"}},
                "action": {"kind": "command", "argv": ["python", "render.py"]},
                "reuse_policy": "if_unchanged", "required_checks": []}},
        "targets": {"report": {"outputs": [{"step": "report", "output": "report"}],
                               "required_criteria": ["report-valid"]},
                    "introduction": {"outputs": [{"step": "intro", "output": "intro"}],
                                     "required_criteria": []}}})


def completed_fixture(definition=None):
    definition = definition or workflow()
    bindings = {name: {"ref": f"source:{name}-v1", "mode": "follow", "binding_revision": 1}
                for name in definition["inputs"]}
    observations = {binding["ref"]: observation(f"{name} original") for name, binding in bindings.items()}
    parameters = {"label": "November"}
    environment = {"calculator": "calc-v1", "renderer": "render-v1"}
    runs, active = [], {}
    for step_id in topological_steps(definition):
        step = definition["steps"][step_id]
        resolved = resolve_step_inputs(definition, step_id, bindings, active, observations.get)
        outputs = {name: f"artifact:{step_id}-{name}:1" for name in step["outputs"]}
        checks = {}
        for name, ref in outputs.items():
            observations[ref] = observation(f"{step_id}:{name} output")
        for check_id in step["required_checks"]:
            ref = f"artifact:check-{step_id}-{check_id}:1"
            observations[ref] = observation(f"{check_id} validated")
            checks[check_id] = {"status": "passed", "evidence_ref": ref}
        run = {"run_id": f"run-{step_id}-1", "step_id": step_id,
               "step_digest": step_digest(definition, step_id),
               "signature": execution_signature(definition, step_id, resolved, parameters, environment),
               "resolved_inputs": resolved, "parameters": parameters, "environment": environment,
               "outputs": outputs, "status": "succeeded", "checks": checks,
               "dependency_coverage": "declared_complete", "claim_origin": "caller_reported", "generation": 1}
        runs.append(run)
        active[step_id] = {"run_id": run["run_id"], "outputs": outputs}
    return definition, bindings, parameters, environment, runs, active, observations


class PlannerTests(unittest.TestCase):
    def setUp(self):
        (self.definition, self.bindings, self.parameters, self.environment,
         self.runs, self.active, self.observations) = completed_fixture()

    def plan(self, **changes):
        args = {"workflow": self.definition, "input_bindings": self.bindings,
                "parameters": self.parameters, "environment": self.environment,
                "runs": self.runs, "active_outputs": self.active,
                "observations": self.observations, "checked_at": CHECKED}
        args.update(changes)
        return plan_rebuild(**args)

    def get_run(self, step_id):
        return next(run for run in self.runs if run["step_id"] == step_id)

    def assess(self, step_id="calculate", **changes):
        resolved = resolve_step_inputs(self.definition, step_id, self.bindings, self.active, self.observations.get)
        args = {"workflow": self.definition, "step_id": step_id, "run": self.get_run(step_id),
                "resolved_inputs": resolved, "parameters": self.parameters, "environment": self.environment,
                "observations": self.observations, "checked_at": CHECKED}
        args.update(changes)
        return assess_run(**args)

    def codes(self, card):
        return {reason["code"] for reason in card["reasons"]}

    def test_valid_current_runs_reuse_exact_active_versions_without_mutation(self):
        before = copy.deepcopy((self.definition, self.bindings, self.parameters, self.environment,
                                self.runs, self.active, self.observations))
        result = self.plan()
        self.assertEqual({"reusable": 3, "needs_run": 0, "blocked": 0, "unknown": 0}, result["counts"])
        self.assertEqual([], result["ready"])
        self.assertEqual("run-calculate-1", result["steps"]["calculate"]["run_id"])
        self.assertEqual("source:data-v1", result["steps"]["calculate"]["resolved_inputs"]["data"]["ref"])
        self.assertEqual(before, (self.definition, self.bindings, self.parameters, self.environment,
                                  self.runs, self.active, self.observations))

    def test_first_plan_orders_unbuilt_dependencies_without_false_missing_source_blocker(self):
        result = self.plan(runs=[], active_outputs={})
        self.assertEqual(["calculate", "intro"], result["ready"])
        self.assertEqual("needs_run", result["steps"]["report"]["state"])
        self.assertEqual(["calculate", "intro"], result["steps"]["report"]["waiting_on"])
        self.assertIsNone(result["steps"]["report"]["signature"])

    def test_rebinding_input_marks_only_its_downstream_branch(self):
        self.bindings["data"] = {"ref": "source:data-v2", "mode": "follow", "binding_revision": 2}
        self.observations["source:data-v2"] = observation("new sales")
        result = self.plan()
        self.assertEqual("reusable", result["steps"]["intro"]["state"])
        self.assertEqual("needs_run", result["steps"]["calculate"]["state"])
        self.assertEqual("needs_run", result["steps"]["report"]["state"])
        self.assertEqual(["calculate"], result["ready"])
        self.assertEqual(["calculate"], result["steps"]["report"]["waiting_on"])
        self.assertEqual("succeeded", self.get_run("calculate")["status"])

    def test_changed_follow_input_requires_binding_reconciliation(self):
        self.observations["source:data-v1"]["status"] = "changed"
        result = self.plan()
        self.assertEqual("blocked", result["steps"]["calculate"]["state"])
        self.assertIn("input_changed", self.codes(result["steps"]["calculate"]))
        self.assertEqual("blocked", result["steps"]["report"]["state"])
        self.assertEqual("reusable", result["steps"]["intro"]["state"])

    def test_scoped_conditions_ignore_unrelated_changes(self):
        original = self.get_run("calculate")["signature"]
        self.parameters["label"] = "December"
        self.environment["renderer"] = "render-v2"
        card = self.assess()
        self.assertEqual(original, card["signature"])
        self.assertEqual("reusable", card["state"])
        self.assertEqual("needs_run", self.assess("report")["state"])

    def test_related_tool_change_invalidates_step(self):
        self.environment["calculator"] = "calc-v2"
        card = self.assess()
        self.assertEqual("needs_run", card["state"])
        self.assertIn("execution_conditions_changed", self.codes(card))

    def test_action_change_invalidates_definition_without_changing_history(self):
        self.definition["steps"]["calculate"]["action"]["argv"] = ["python", "calculate-v2.py"]
        card = self.assess()
        self.assertEqual("needs_run", card["state"])
        self.assertIn("step_definition_changed", self.codes(card))

    def test_output_contract_add_remove_and_rename_schedule_rebuild(self):
        for change in ("add", "remove", "rename"):
            with self.subTest(change=change):
                definition = workflow()
                extra = {"logical_name": "audit.json", "role": "intermediate", "format": "json"}
                if change == "remove":
                    definition["steps"]["calculate"]["outputs"]["audit"] = extra
                (self.definition, self.bindings, self.parameters, self.environment,
                 self.runs, self.active, self.observations) = completed_fixture(definition)
                outputs = self.definition["steps"]["calculate"]["outputs"]
                if change == "add":
                    outputs["audit"] = extra
                elif change == "remove":
                    del outputs["audit"]
                else:
                    outputs["summary"] = outputs.pop("totals")
                    self.definition["steps"]["report"]["inputs"]["totals"]["output"] = "summary"
                result = self.plan()
                card = result["steps"]["calculate"]
                self.assertEqual("needs_run", card["state"])
                self.assertIn("step_definition_changed", self.codes(card))
                self.assertEqual(["calculate"], result["ready"])
                self.assertEqual("reusable", result["steps"]["intro"]["state"])
                self.assertEqual("needs_run", result["steps"]["report"]["state"])
                self.assertEqual(["calculate"], result["steps"]["report"]["waiting_on"])

    def test_new_required_checks_apply_to_new_execution_not_historical_run(self):
        self.definition["steps"]["calculate"]["required_checks"].append("new-audit")
        card = self.assess()
        self.assertEqual("needs_run", card["state"])
        self.assertIn("step_definition_changed", self.codes(card))
        self.assertNotIn("check_not_passed", self.codes(card))

    def test_removed_output_manual_edit_still_blocks_changed_contract(self):
        definition = workflow()
        definition["steps"]["calculate"]["outputs"]["audit"] = {
            "logical_name": "audit.json", "role": "intermediate", "format": "json"}
        (self.definition, self.bindings, self.parameters, self.environment,
         self.runs, self.active, self.observations) = completed_fixture(definition)
        del self.definition["steps"]["calculate"]["outputs"]["audit"]
        self.observations[self.get_run("calculate")["outputs"]["audit"]]["status"] = "changed"
        card = self.assess()
        self.assertEqual("blocked", card["state"])
        self.assertIn("output_changed", self.codes(card))

    def test_removed_passed_check_evidence_stays_protected_after_definition_change(self):
        self.definition["steps"]["calculate"]["required_checks"] = []
        ref = self.get_run("calculate")["checks"]["totals-valid"]["evidence_ref"]
        self.observations[ref]["status"] = "changed"
        card = self.assess()
        self.assertEqual("blocked", card["state"])
        self.assertIn("check_evidence_changed", self.codes(card))

    def test_optional_failed_old_check_does_not_block_legitimate_definition_change(self):
        self.definition["steps"]["calculate"]["required_checks"] = []
        self.get_run("calculate")["checks"]["optional-quality"] = {"status": "failed"}
        self.assertEqual("needs_run", self.assess()["state"])

    def test_malformed_historical_outputs_and_same_definition_mismatch_stay_blocked(self):
        for outputs, changed in (({}, True), ({"totals": None}, True), ({"extra": "artifact:extra:1"}, False)):
            with self.subTest(outputs=outputs, changed=changed):
                definition, bindings, params, env, runs, active, observations = completed_fixture()
                run = next(value for value in runs if value["step_id"] == "calculate")
                run["outputs"] = outputs
                active["calculate"]["outputs"] = outputs
                if changed:
                    definition["steps"]["calculate"]["action"]["argv"].append("--new")
                result = plan_rebuild(definition, bindings, params, env, runs, active, observations, CHECKED)
                card = result["steps"]["calculate"]
                self.assertEqual("blocked", card["state"])
                self.assertIn("run_outputs_mismatch", self.codes(card))

    def test_missing_environment_is_unknown_only_on_relevant_branch(self):
        del self.environment["calculator"]
        result = self.plan()
        self.assertEqual("unknown", result["steps"]["calculate"]["state"])
        self.assertIn("environment_missing", self.codes(result["steps"]["calculate"]))
        self.assertIsNone(result["steps"]["calculate"]["signature"])
        self.assertEqual("reusable", result["steps"]["intro"]["state"])
        self.assertEqual("unknown", result["steps"]["report"]["state"])

    def test_unknown_or_partial_dependency_coverage_is_not_reusable(self):
        for coverage in ("partial", "unknown", None):
            with self.subTest(coverage=coverage):
                self.get_run("calculate")["dependency_coverage"] = coverage
                card = self.assess()
                self.assertEqual("unknown", card["state"])
                self.assertIn("dependency_coverage_unknown", self.codes(card))

    def test_output_missing_can_regenerate_but_output_edit_is_preserved_as_conflict(self):
        ref = self.get_run("calculate")["outputs"]["totals"]
        self.observations[ref]["status"] = "missing"
        self.assertEqual("needs_run", self.assess()["state"])
        self.observations[ref]["status"] = "changed"
        card = self.assess()
        self.assertEqual("blocked", card["state"])
        self.assertIn("output_changed", self.codes(card))

    def test_missing_changed_or_unsafe_execution_snapshot_requires_new_run(self):
        run = self.get_run("calculate")
        for code in ("snapshot_missing", "snapshot_changed", "snapshot_unsafe"):
            with self.subTest(code=code):
                observed_run = copy.deepcopy(run)
                observed_run["snapshot_issues"] = [{"port": "data", "code": code}]
                card = self.assess(run=observed_run)
                self.assertEqual("needs_run", card["state"])
                self.assertEqual("stale", card["freshness"])
                self.assertIn("execution_snapshot_unavailable", self.codes(card))
                self.assertIsNotNone(card["signature"])
                self.assertEqual("succeeded", observed_run["status"])
        self.assertNotIn("snapshot_issues", run)
        intact = copy.deepcopy(run)
        intact["snapshot_issues"] = []
        self.assertEqual("reusable", self.assess(run=intact)["state"])

    def test_snapshot_observation_does_not_hide_output_conflict_and_is_bounded(self):
        run = copy.deepcopy(self.get_run("calculate"))
        run["snapshot_issues"] = [{"port": f"input-{index}", "code": "snapshot_missing"} for index in range(20)]
        card = self.assess(run=run)
        reason = card["reasons"][0]
        self.assertEqual(20, reason["issues_count"])
        self.assertEqual(8, len(reason["issues"]))
        self.assertEqual(12, reason["issues_omitted"])
        self.observations[run["outputs"]["totals"]]["status"] = "changed"
        card = self.assess(run=run)
        self.assertEqual("blocked", card["state"])
        self.assertIn("output_changed", self.codes(card))

    def test_required_check_result_and_actual_evidence_are_both_needed(self):
        run = self.get_run("calculate")
        run["checks"]["totals-valid"]["status"] = "failed"
        self.assertIn("check_not_passed", self.codes(self.assess()))
        run["checks"]["totals-valid"]["status"] = "passed"
        ref = run["checks"]["totals-valid"]["evidence_ref"]
        self.observations[ref]["status"] = "changed"
        card = self.assess()
        self.assertEqual("blocked", card["state"])
        self.assertIn("check_evidence_changed", self.codes(card))

    def test_unobserved_required_check_evidence_is_unknown(self):
        ref = self.get_run("calculate")["checks"]["totals-valid"]["evidence_ref"]
        del self.observations[ref]
        card = self.assess()
        self.assertEqual("unknown", card["state"])
        self.assertIn("check_evidence_unavailable", self.codes(card))

    def test_never_and_manual_policy_apply_between_update_generations(self):
        for policy, expected in (("never", "needs_run"), ("manual", "unknown")):
            with self.subTest(policy=policy):
                definition = workflow()
                definition["steps"]["calculate"]["reuse_policy"] = policy
                definition, bindings, params, env, runs, active, observations = completed_fixture(definition)
                args = dict(workflow=definition, input_bindings=bindings, parameters=params,
                            environment=env, runs=runs, active_outputs=active,
                            observations=observations, checked_at=CHECKED)
                self.assertEqual(expected, plan_rebuild(**args)["steps"]["calculate"]["state"])
                same = plan_rebuild(**args, generation=1)
                self.assertEqual("reusable", same["steps"]["calculate"]["state"])
                self.assertEqual("reusable", same["steps"]["report"]["state"])
                self.assertEqual(expected, plan_rebuild(**args, generation=2)["steps"]["calculate"]["state"])
                current = plan_rebuild(**args, respect_reuse_policy=False)
                self.assertEqual("reusable", current["steps"]["calculate"]["state"])

    def test_same_generation_does_not_override_changed_input_or_partial_dependencies(self):
        self.get_run("calculate")["dependency_coverage"] = "partial"
        self.assertEqual("unknown", self.plan(generation=1)["steps"]["calculate"]["state"])
        self.get_run("calculate")["dependency_coverage"] = "declared_complete"
        self.observations["source:data-v1"]["status"] = "changed"
        self.assertEqual("blocked", self.plan(generation=1)["steps"]["calculate"]["state"])

    def test_boolean_and_missing_generation_cannot_satisfy_reuse_policy(self):
        with self.assertRaises(EngineError):
            self.plan(generation=True)
        definition = workflow()
        definition["steps"]["calculate"]["reuse_policy"] = "never"
        definition, bindings, params, env, runs, active, observations = completed_fixture(definition)
        runs[0]["generation"] = None
        result = plan_rebuild(definition, bindings, params, env, runs, active, observations, CHECKED)
        self.assertEqual("needs_run", result["steps"]["calculate"]["state"])

    def test_unresolved_attempt_blocks_even_with_a_previous_active_success(self):
        self.runs.append({"run_id": "running-calculate", "step_id": "calculate", "status": "started"})
        result = self.plan()
        self.assertEqual("blocked", result["steps"]["calculate"]["state"])
        self.assertEqual("running-calculate", result["steps"]["calculate"]["run_id"])
        self.assertIn("run_unresolved", self.codes(result["steps"]["calculate"]))

    def test_historical_success_without_active_binding_is_not_adopted(self):
        del self.active["calculate"]
        result = self.plan()
        self.assertEqual("needs_run", result["steps"]["calculate"]["state"])
        self.assertIsNone(result["steps"]["calculate"]["run_id"])
        self.assertEqual(["calculate"], result["steps"]["report"]["waiting_on"])

    def test_active_binding_must_match_exact_recorded_run_outputs(self):
        self.active["calculate"] = copy.deepcopy(self.active["calculate"])
        self.active["calculate"]["outputs"]["totals"] = "artifact:unrelated:9"
        card = self.plan()["steps"]["calculate"]
        self.assertEqual("blocked", card["state"])
        self.assertIn("active_outputs_mismatch", self.codes(card))

    def test_missing_source_stays_blocked_while_other_upstream_is_scheduled(self):
        self.definition["steps"]["report"]["inputs"]["brand_direct"] = {"input": "brand"}
        del self.bindings["brand"]
        result = self.plan(runs=[], active_outputs={})
        self.assertEqual("blocked", result["steps"]["report"]["state"])
        self.assertIn("input_missing", self.codes(result["steps"]["report"]))

    def test_target_subset_does_not_require_unrelated_source_or_environment(self):
        del self.bindings["data"]
        del self.environment["calculator"]
        result = self.plan(target_ids=["introduction"])
        self.assertEqual(["intro"], result["order"])
        self.assertEqual("reusable", result["steps"]["intro"]["state"])

    def test_expired_source_and_unknown_expiry_do_not_reuse(self):
        self.observations["source:data-v1"]["valid_until"] = "2026-11-02T07:00:00Z"
        self.assertIn("input_expired", self.codes(self.assess()))
        self.assertEqual("blocked", self.assess()["state"])
        self.observations["source:data-v1"]["valid_until"] = "not-a-time"
        self.assertEqual("unknown", self.assess()["state"])

    def test_signature_and_plan_digest_do_not_change_with_observation_time(self):
        first = self.plan()
        for item in self.observations.values():
            item["observed_at"] = "2026-11-03T08:00:00Z"
        later = self.plan(checked_at="2026-11-03T08:00:00Z")
        self.assertEqual(first["plan_digest"], later["plan_digest"])
        self.assertEqual(first["steps"]["calculate"]["signature"], later["steps"]["calculate"]["signature"])

    def test_blocked_input_edit_changes_plan_digest_even_without_a_signature(self):
        source = self.observations["source:data-v1"]
        source.update(status="changed", sha256=checksum("edit B"))
        first = self.plan()
        source["sha256"] = checksum("edit C")
        second = self.plan()
        self.assertIsNone(first["steps"]["calculate"]["signature"])
        self.assertEqual(first["steps"]["calculate"]["reasons"], second["steps"]["calculate"]["reasons"])
        self.assertNotEqual(first["plan_digest"], second["plan_digest"])
        source["observed_at"] = "2026-11-03T08:00:00Z"
        source["path"] = "/another/observation/path"
        later = self.plan(checked_at="2026-11-03T08:00:00Z")
        self.assertEqual(second["plan_digest"], later["plan_digest"])

    def test_output_and_check_evidence_edits_change_blocked_plan_digest(self):
        run = self.get_run("calculate")
        for ref in (run["outputs"]["totals"], run["checks"]["totals-valid"]["evidence_ref"]):
            with self.subTest(ref=ref):
                original = copy.deepcopy(self.observations[ref])
                self.observations[ref].update(status="changed", sha256=checksum("edit B"))
                first = self.plan()
                self.observations[ref]["sha256"] = checksum("edit C")
                second = self.plan()
                self.assertEqual("blocked", first["steps"]["calculate"]["state"])
                self.assertEqual(first["steps"]["calculate"]["reasons"], second["steps"]["calculate"]["reasons"])
                self.assertNotEqual(first["plan_digest"], second["plan_digest"])
                self.observations[ref] = original

    def test_plan_digest_ignores_observations_outside_selected_target_closure(self):
        first = self.plan(target_ids=["introduction"])
        self.observations["source:data-v1"].update(status="changed", sha256=checksum("edit B"))
        self.observations[self.get_run("calculate")["outputs"]["totals"]].update(status="changed", sha256=checksum("other edit"))
        second = self.plan(target_ids=["introduction"])
        self.assertEqual(first["plan_digest"], second["plan_digest"])

    def test_identical_bytes_with_new_reference_are_conservatively_different(self):
        self.observations["source:data-v2"] = copy.deepcopy(self.observations["source:data-v1"])
        self.bindings["data"]["ref"] = "source:data-v2"
        self.assertEqual("needs_run", self.assess()["state"])

    def test_constant_caller_fingerprint_cannot_hide_a_changed_hash(self):
        resolved = resolve_step_inputs(self.definition, "calculate", self.bindings, self.active, self.observations.get)
        resolved["data"]["fingerprint"] = "fixed"
        first = execution_signature(self.definition, "calculate", resolved, self.parameters, self.environment)
        resolved["data"]["sha256"] = checksum("new bytes")
        second = execution_signature(self.definition, "calculate", resolved, self.parameters, self.environment)
        self.assertNotEqual(first, second)

    def test_optional_absent_input_has_a_stable_signature(self):
        self.definition["inputs"]["note"] = {"required": False, "format": "markdown"}
        self.definition["steps"]["intro"]["inputs"]["note"] = {"input": "note"}
        resolved = resolve_step_inputs(self.definition, "intro", self.bindings, self.active, self.observations.get)
        self.assertEqual("absent", resolved["note"]["status"])
        card = assess_run(self.definition, "intro", None, resolved, self.parameters,
                          self.environment, self.observations, CHECKED)
        self.assertEqual("needs_run", card["state"])
        self.assertIsNotNone(card["signature"])

    def test_runtime_optional_flag_cannot_hide_a_required_input_or_output(self):
        resolved = resolve_step_inputs(self.definition, "calculate", self.bindings, self.active, self.observations.get)
        resolved["data"] = {"ref": None, "status": "absent", "optional": True}
        with self.assertRaises(EngineError):
            execution_signature(self.definition, "calculate", resolved, self.parameters, self.environment)
        self.assertEqual("blocked", self.assess(resolved_inputs=resolved)["state"])
        ref = self.get_run("calculate")["outputs"]["totals"]
        self.observations[ref] = {"status": "absent", "optional": True}
        self.assertEqual("unknown", self.assess()["state"])

    def test_optional_conditions_absent_and_explicit_false_remain_distinct(self):
        self.definition["parameters"]["include_note"] = {"type": "boolean", "required": False}
        self.definition["environment"]["optional_font"] = {"required": False}
        self.definition["steps"]["intro"]["parameters"] = ["include_note"]
        self.definition["steps"]["intro"]["environment"] = ["optional_font"]
        resolved = resolve_step_inputs(self.definition, "intro", self.bindings, self.active, self.observations.get)
        absent = execution_signature(self.definition, "intro", resolved, self.parameters, self.environment)
        parameters = {**self.parameters, "include_note": False}
        explicit_false = execution_signature(self.definition, "intro", resolved, parameters, self.environment)
        self.assertNotEqual(absent, explicit_false)
        card = assess_run(self.definition, "intro", None, resolved, self.parameters,
                          self.environment, self.observations, CHECKED)
        self.assertEqual("needs_run", card["state"])
        self.assertIsNotNone(card["signature"])

    def test_unrelated_step_definition_does_not_change_step_signature(self):
        self.definition["steps"]["report"]["action"]["argv"].append("--new-layout")
        self.assertEqual("reusable", self.assess("calculate")["state"])

    def test_malformed_target_and_run_identifiers_have_safe_errors(self):
        for targets in ([{}], ["report", "report"], "report"):
            with self.subTest(targets=targets), self.assertRaises(EngineError):
                self.plan(target_ids=targets)
        runs = copy.deepcopy(self.runs)
        runs[0]["step_id"] = []
        with self.assertRaises(EngineError):
            self.plan(runs=runs)

    def test_rejected_observation_identity_and_duplicate_runs_are_explicit_errors(self):
        with self.assertRaises(EngineError):
            resolve_step_inputs(self.definition, "calculate", self.bindings, self.active,
                                lambda ref: {"ref": "wrong", **observation("data")})
        self.runs.append(copy.deepcopy(self.runs[0]))
        with self.assertRaises(EngineError) as caught:
            self.plan()
        self.assertEqual("invalid_planner_input", caught.exception.code)

    def test_time_requires_explicit_timezone(self):
        with self.assertRaises(EngineError) as caught:
            self.plan(checked_at="2026-11-02T08:00:00")
        self.assertEqual("invalid_planner_input", caught.exception.code)

    def test_maximum_graph_is_normalized_once_per_plan(self):
        definition = {
            "workflow_schema_version": 1,
            "steps": {f"step-{index}": {
                "action": {"kind": "agent", "instruction": "Generate one result"},
                "outputs": {"result": {"logical_name": f"results/{index}.txt"}},
                "reuse_policy": "if_unchanged"} for index in range(128)},
            "targets": {"all": {"outputs": [
                {"step": f"step-{index}", "output": "result"} for index in range(128)]}}}
        # Count validation calls instead of asserting a platform-dependent time.
        # The helper patch also catches accidentally reintroducing public
        # whole-graph validation inside each internal per-step operation.
        with patch("retulo.planner.normalize_workflow", wraps=normalize_workflow) as normalizer, \
                patch("retulo.workflow.normalize_workflow", side_effect=AssertionError("nested graph validation")):
            result = plan_rebuild(definition, {}, {}, {}, [], {}, {}, CHECKED)
        self.assertEqual(1, normalizer.call_count)
        self.assertEqual(128, len(result["order"]))
        self.assertEqual(128, len(result["ready"]))
        self.assertEqual(128, result["counts"]["needs_run"])


if __name__ == "__main__":
    unittest.main()
