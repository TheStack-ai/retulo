import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from retulo.fs import EngineError
from retulo.workflow import (
    WORKFLOW_SCHEMA, dependency_closure, normalize_environment,
    normalize_parameters, normalize_workflow, step_digest, topological_steps,
    workflow_digest,
)


def workflow():
    return {
        "workflow_schema_version": 1,
        "inputs": {"sales": {"format": "csv"}, "logo": {"format": "svg"}},
        "parameters": {"period": {"type": "string"},
                       "detail": {"type": "boolean", "default": False}},
        "environment": {"calculator": {}, "renderer": {}},
        "steps": {
            "calculate": {
                "action": {"kind": "command", "argv": ["python", "calculate.py"]},
                "inputs": {"source": {"input": "sales"}},
                "parameters": ["period"], "environment": ["calculator"],
                "outputs": {"totals": {"logical_name": "totals.json"}},
                "reuse_policy": "if_unchanged", "required_checks": ["sum-valid"],
            },
            "branding": {
                "action": {"kind": "manual", "instruction": "Prepare the supplied logo"},
                "inputs": {"source": {"input": "logo"}},
                "outputs": {"logo": {"logical_name": "brand.svg"}},
            },
            "report": {
                "action": {"kind": "agent", "instruction": "Create a grounded report"},
                "inputs": {"totals": {"step": "calculate", "output": "totals"}},
                "parameters": ["period", "detail"], "environment": ["renderer"],
                "outputs": {"report": {"logical_name": "reports/report.html", "role": "deliverable"}},
            },
        },
        "targets": {
            "report": {"outputs": [{"step": "report", "output": "report"}],
                       "required_criteria": ["report-valid"]},
            "brand": {"outputs": [{"step": "branding", "output": "logo"}]},
        },
    }


class WorkflowTests(unittest.TestCase):
    def assertInvalid(self, raw, path=None):
        with self.assertRaises(EngineError) as caught:
            normalize_workflow(raw)
        self.assertEqual("invalid_workflow", caught.exception.code)
        if path is not None:
            self.assertIn(path, caught.exception.details["path"])

    def test_normalization_is_pure_and_idempotent(self):
        raw = workflow()
        before = copy.deepcopy(raw)
        with patch("builtins.open", side_effect=AssertionError("No filesystem access")):
            normalized = normalize_workflow(raw)
            repeated = normalize_workflow(normalized)
        self.assertEqual(before, raw)
        self.assertEqual(normalized, repeated)
        self.assertTrue(normalized["inputs"]["sales"]["required"])
        self.assertEqual("manual", normalized["steps"]["branding"]["reuse_policy"])
        normalized["steps"]["calculate"]["action"]["argv"].append("--changed")
        self.assertEqual(before, raw)

    def test_packaged_schema_matches_runtime_contract(self):
        path = Path(__file__).parents[1] / "src/retulo/schemas/workflow.schema.json"
        packaged = json.loads(path.read_text())
        self.assertEqual(WORKFLOW_SCHEMA, packaged)
        Draft202012Validator.check_schema(packaged)
        Draft202012Validator(packaged).validate(normalize_workflow(workflow()))

    def test_defaults_can_omit_empty_global_sections(self):
        raw = {"workflow_schema_version": 1, "steps": {
            "create": {"action": {"kind": "agent", "instruction": "Write a draft"},
                       "outputs": {"draft": {"logical_name": "draft.md"}}}},
            "targets": {"draft": {"outputs": [{"step": "create", "output": "draft"}]}}}
        normalized = normalize_workflow(raw)
        self.assertEqual({}, normalized["inputs"])
        self.assertEqual([], normalized["steps"]["create"]["required_checks"])

    def test_dependency_closure_excludes_unrelated_work(self):
        self.assertEqual({"calculate", "report"}, dependency_closure(workflow(), ["report"]))
        self.assertEqual({"calculate", "report", "branding"}, dependency_closure(workflow()))
        self.assertEqual(["calculate", "report"], topological_steps(workflow(), {"calculate", "report"}))

    def test_topological_order_is_stable_across_input_order(self):
        raw = workflow()
        reordered = copy.deepcopy(raw)
        reordered["steps"] = dict(reversed(list(reordered["steps"].items())))
        self.assertEqual(["branding", "calculate", "report"], topological_steps(raw))
        self.assertEqual(topological_steps(raw), topological_steps(reordered))

    def test_cycle_is_rejected(self):
        raw = workflow()
        raw["steps"]["calculate"]["inputs"]["report"] = {"step": "report", "output": "report"}
        self.assertInvalid(raw, "steps")

    def test_self_dependency_is_rejected(self):
        raw = workflow()
        raw["steps"]["calculate"]["inputs"]["old"] = {"step": "calculate", "output": "totals"}
        self.assertInvalid(raw)

    def test_unknown_input_output_and_target_references_are_rejected(self):
        for section in ("input", "upstream", "target"):
            with self.subTest(section=section):
                raw = workflow()
                if section == "input":
                    raw["steps"]["calculate"]["inputs"]["source"] = {"input": "missing"}
                elif section == "upstream":
                    raw["steps"]["report"]["inputs"]["totals"] = {"step": "calculate", "output": "absent"}
                else:
                    raw["targets"]["report"]["outputs"] = [{"step": "missing", "output": "x"}]
                self.assertInvalid(raw)

    def test_unknown_fields_and_binding_values_are_not_definitions(self):
        for where, key, value in (("top", "approvals", []), ("input", "path", "/private/data.csv"),
                                  ("step", "status", "succeeded"), ("action", "shell", True)):
            raw = workflow()
            target = {"top": raw, "input": raw["inputs"]["sales"],
                      "step": raw["steps"]["calculate"],
                      "action": raw["steps"]["calculate"]["action"]}[where]
            target[key] = value
            with self.subTest(where=where):
                self.assertInvalid(raw)

    def test_unknown_parameter_and_environment_keys_are_rejected(self):
        for field in ("parameters", "environment"):
            raw = workflow()
            raw["steps"]["calculate"][field] = ["missing"]
            self.assertInvalid(raw)

    def test_output_ownership_is_exclusive_even_with_different_roles(self):
        raw = workflow()
        raw["steps"]["branding"]["outputs"]["logo"] = {"logical_name": "TOTALS.JSON", "role": "different"}
        self.assertInvalid(raw, "logical_name")

    def test_output_unicode_names_are_normalized_before_collision_detection(self):
        raw = workflow()
        raw["steps"]["calculate"]["outputs"]["totals"]["logical_name"] = "café.json"
        raw["steps"]["branding"]["outputs"]["logo"]["logical_name"] = "cafe\u0301.json"
        self.assertInvalid(raw)

    def test_reserved_or_traversal_output_paths_are_rejected(self):
        for name in ("/tmp/report.md", "../report.md", "a/../report.md", "a//b.md", ".workspace/x",
                     "WORKSPACE.md", "con.txt", "a\\b.txt", "a:report", "a/hidden. "):
            with self.subTest(name=name):
                raw = workflow()
                raw["steps"]["report"]["outputs"]["report"]["logical_name"] = name
                self.assertInvalid(raw)

    def test_nonfinite_or_nonjson_values_are_rejected(self):
        for value in (float("nan"), float("inf"), {"not-a-scalar"}, b"bytes"):
            raw = workflow()
            raw["parameters"]["amount"] = {"type": "number", "default": value}
            self.assertInvalid(raw)

    def test_nonstring_json_keys_are_not_silently_coerced(self):
        raw = workflow()
        raw["inputs"][1] = {}
        self.assertInvalid(raw)
        with self.assertRaises(EngineError) as caught:
            normalize_parameters(workflow(), {1: "value"})
        self.assertEqual("invalid_parameters", caught.exception.code)

    def test_schema_version_boolean_does_not_pass_integer_constant(self):
        for version in (True, 1.0, "1", 2):
            raw = workflow()
            raw["workflow_schema_version"] = version
            self.assertInvalid(raw)

    def test_rejects_empty_executable_but_preserves_empty_argument(self):
        raw = workflow()
        raw["steps"]["calculate"]["action"]["argv"] = ["python", ""]
        self.assertEqual(["python", ""], normalize_workflow(raw)["steps"]["calculate"]["action"]["argv"])
        raw["steps"]["calculate"]["action"]["argv"] = ["  "]
        self.assertInvalid(raw)

    def test_action_is_declaration_and_shell_like_argument_is_not_expanded(self):
        raw = workflow()
        raw["steps"]["calculate"]["action"]["argv"].append("$(touch should-not-run)")
        self.assertEqual("$(touch should-not-run)", normalize_workflow(raw)["steps"]["calculate"]["action"]["argv"][-1])

    def test_parameter_defaults_are_typed_and_in_enum(self):
        for definition in ({"type": "integer", "default": True},
                           {"type": "integer", "enum": [1, True]},
                           {"type": "string", "enum": ["a"], "default": "b"}):
            raw = workflow()
            raw["parameters"]["bad"] = definition
            self.assertInvalid(raw)

    def test_parameter_binding_preserves_false_zero_and_empty(self):
        raw = workflow()
        raw["parameters"].update({"count": {"type": "integer"}, "ratio": {"type": "number"},
                                  "optional": {"type": "string", "required": False}})
        self.assertEqual({"period": "", "detail": False, "count": 0, "ratio": 0.0},
                         normalize_parameters(raw, {"period": "", "count": 0, "ratio": 0.0}))

    def test_parameter_binding_missing_unknown_and_wrong_type_are_explicit(self):
        for values in ({}, {"period": "2026-11", "missing": "x"}, {"period": 7}):
            with self.subTest(values=values), self.assertRaises(EngineError) as caught:
                normalize_parameters(workflow(), values)
            self.assertEqual("invalid_parameters", caught.exception.code)
        self.assertEqual({"detail": False}, normalize_parameters(workflow(), {}, require_all=False))

    def test_large_integer_number_remains_finite_without_float_conversion(self):
        raw = workflow()
        raw["parameters"]["large"] = {"type": "number", "default": 10 ** 400}
        self.assertEqual(10 ** 400, normalize_parameters(raw, {"period": "x"})["large"])

    def test_environment_binding_is_bounded_explicit_and_pure(self):
        raw = workflow()
        values = {"calculator": "python-3.11:script-sha", "renderer": "renderer-v2"}
        self.assertEqual(values, normalize_environment(raw, values))
        self.assertEqual({}, normalize_environment(raw, {}, require_all=False))
        for bad in ({}, {**values, "unknown": "x"}, {**values, "renderer": None},
                    {**values, "renderer": "  "}, {**values, "renderer": float("nan")}):
            with self.subTest(bad=bad), self.assertRaises(EngineError) as caught:
                normalize_environment(raw, bad)
            self.assertEqual("invalid_environment", caught.exception.code)

    def test_scoped_digest_ignores_unrelated_step_or_parameter_changes(self):
        raw = workflow()
        changed = copy.deepcopy(raw)
        changed["steps"]["branding"]["action"]["instruction"] = "Use another branding process"
        changed["parameters"]["unused"] = {"type": "integer", "default": 4}
        self.assertNotEqual(workflow_digest(raw), workflow_digest(changed))
        self.assertEqual(step_digest(raw, "calculate"), step_digest(changed, "calculate"))

    def test_scoped_digest_changes_with_consumed_contract(self):
        raw = workflow()
        for mutate in (
            lambda x: x["steps"]["calculate"]["required_checks"].append("second-check"),
            lambda x: x["steps"]["calculate"]["action"]["argv"].append("--round"),
            lambda x: x["parameters"]["period"].update(enum=["2026-11"]),
            lambda x: x["inputs"]["sales"].update(format="json"),
        ):
            changed = copy.deepcopy(raw)
            mutate(changed)
            self.assertNotEqual(step_digest(raw, "calculate"), step_digest(changed, "calculate"))

    def test_cosmetic_description_does_not_force_step_reexecution(self):
        raw = workflow()
        changed = copy.deepcopy(raw)
        changed["steps"]["calculate"]["description"] = "Explanation only"
        changed["inputs"]["sales"]["description"] = "Data description"
        self.assertEqual(step_digest(raw, "calculate"), step_digest(changed, "calculate"))

    def test_target_and_step_selection_cannot_silently_ignore_mistakes(self):
        for targets in ([], ["absent"], ["report", "report"]):
            with self.assertRaises(EngineError):
                dependency_closure(workflow(), targets)
        with self.assertRaises(EngineError):
            topological_steps(workflow(), {"absent"})

    def test_json_key_order_does_not_change_definition_digest(self):
        raw = workflow()
        reordered = json.loads(json.dumps(raw, sort_keys=True))
        self.assertEqual(workflow_digest(raw), workflow_digest(reordered))

    def test_size_limit_and_identifier_bounds_are_enforced(self):
        raw = workflow()
        raw["steps"]["calculate"]["action"]["instruction"] = "한" * 100000
        self.assertInvalid(raw)
        raw = workflow()
        raw["inputs"]["invalid:slot"] = {}
        self.assertInvalid(raw)

    def test_identifiers_reject_trailing_newlines_in_both_schema_and_normalizer(self):
        for bad in ("slot\n", "slot\r", "slot\r\n", "slot\u2028", "slot\x00"):
            with self.subTest(identifier=repr(bad)):
                raw = workflow()
                raw["inputs"][bad] = {}
                self.assertTrue(list(Draft202012Validator(WORKFLOW_SCHEMA).iter_errors(raw)))
                self.assertInvalid(raw)
                raw = workflow()
                raw["steps"]["calculate"]["required_checks"] = [bad]
                self.assertTrue(list(Draft202012Validator(WORKFLOW_SCHEMA).iter_errors(raw)))
                self.assertInvalid(raw)


if __name__ == "__main__":
    unittest.main()
