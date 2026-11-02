import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from retulo.engine import Engine
from retulo.fs import EngineError
from retulo.recipes import (
    bind_recipe_parameters, check_recipe_bindings, normalize_recipe,
    recipe_digest, recipe_seed,
)


def uid(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"urn:retulo:recipe-test:{label}"))


def sample_recipe():
    return {
        "recipe_schema_version": 1,
        "recipe_id": uid("recipe"),
        "revision": 1,
        "title": "Monthly report",
        "workflow": {
            "workflow_schema_version": 1,
            "inputs": {"sales": {"required": True, "format": "csv"}},
            "parameters": {
                "period": {"type": "string", "required": True},
                "include_summary": {"type": "boolean", "default": True},
                "precision": {"type": "integer", "default": 0},
                "scale": {"type": "number", "default": 1.0},
                "caption": {"type": "string", "default": ""},
            },
            "environment": {},
            "steps": {
                "calculate": {
                    "action": {"kind": "agent", "instruction": "Calculate totals from the supplied CSV"},
                    "inputs": {"data": {"input": "sales"}},
                    "parameters": ["precision", "scale"],
                    "environment": [],
                    "outputs": {"totals": {"logical_name": "totals.json", "role": "intermediate", "format": "json"}},
                    "reuse_policy": "if_unchanged",
                    "required_checks": ["sum-valid"],
                },
                "render": {
                    "action": {"kind": "agent", "instruction": "Customer A is literal reference text; read parameters separately"},
                    "inputs": {"totals": {"step": "calculate", "output": "totals"}},
                    "parameters": ["period", "include_summary", "caption"],
                    "environment": [],
                    "outputs": {"report": {"logical_name": "report.html", "role": "deliverable", "format": "html"}},
                    "reuse_policy": "if_unchanged",
                    "required_checks": [],
                },
            },
            "targets": {"report": {"outputs": [{"step": "render", "output": "report"}],
                                      "required_criteria": ["report-valid"]}},
        },
        "completion_criteria": [{"id": "report-valid", "text": "Report agrees with calculated totals",
                                 "human_review": False}],
    }


def run_reference(label="source"):
    return {"task_id": uid(f"task-{label}"), "workflow_digest": "a" * 64,
            "workflow_revision": 2, "run_ids": [uid(f"run-{label}")]}


def manifest_reference():
    return {"task_id": uid("manifest-task"), "artifact_id": uid("manifest-artifact"),
            "artifact_revision": 2, "sha256": "b" * 64}


def recipe_validator():
    folder = Path(__file__).parents[1] / "src/retulo/schemas"
    recipe_schema = json.loads((folder / "recipe.schema.json").read_text())
    workflow_schema = json.loads((folder / "workflow.schema.json").read_text())
    Draft202012Validator.check_schema(recipe_schema)
    registry = Registry().with_resources([
        (recipe_schema["$id"], Resource.from_contents(recipe_schema)),
        (workflow_schema["$id"], Resource.from_contents(workflow_schema)),
    ])
    return Draft202012Validator(recipe_schema, registry=registry)


class RecipeTests(unittest.TestCase):
    def assertRecipeError(self, operation, *args, path=None, **kwargs):
        with self.assertRaises(EngineError) as caught:
            operation(*args, **kwargs)
        self.assertIn(caught.exception.code, {"invalid_recipe", "invalid_recipe_bindings"})
        self.assertIn("path", caught.exception.details)
        if path is not None:
            self.assertEqual(path, caught.exception.details["path"])
        return caught.exception

    def test_normalize_is_pure_and_does_not_read_files_or_inherit_verdicts(self):
        supplied = sample_recipe()
        before = copy.deepcopy(supplied)
        with patch("builtins.open", side_effect=AssertionError("Normalizer attempted file access")):
            normalized = normalize_recipe(supplied)
        self.assertEqual(before, supplied)
        self.assertNotIn("human_review", normalized["completion_criteria"][0])
        self.assertFalse(normalized["completion_criteria"][0]["requires_approval"])
        self.assertEqual([], normalized["qualification"])
        self.assertEqual({"features": []}, normalized["requirements"])
        self.assertEqual(normalized, normalize_recipe(normalized))
        normalized["workflow"]["steps"]["render"]["action"]["instruction"] = "Changed copy"
        self.assertEqual(before, supplied)

    def test_semantic_digest_is_stable_and_changes_with_actual_contract(self):
        recipe = sample_recipe()
        normalized = normalize_recipe(recipe)
        reordered = dict(reversed(list(recipe.items())))
        self.assertEqual(recipe_digest(recipe), recipe_digest(reordered))
        self.assertEqual(recipe_digest(recipe), recipe_digest(normalized))
        changed = copy.deepcopy(recipe)
        changed["workflow"]["steps"]["render"]["action"]["instruction"] += " New requirement"
        self.assertNotEqual(recipe_digest(recipe), recipe_digest(changed))

    def test_root_fields_cannot_carry_approval_verdict_or_execution_authority(self):
        for field, value in (
            ("approvals", []), ("criteria_results", []), ("status", "completed"),
            ("allowed_source_roots", ["/"]), ("publish", True), ("models", ["other"]),
        ):
            with self.subTest(field=field):
                recipe = sample_recipe()
                recipe[field] = value
                self.assertRecipeError(normalize_recipe, recipe, path=f"recipe.{field}")

    def test_unknown_nested_fields_and_missing_fields_have_paths(self):
        recipe = sample_recipe()
        recipe["requirements"] = {"features": [], "install": "anything"}
        self.assertRecipeError(normalize_recipe, recipe, path="recipe.requirements.install")
        recipe = sample_recipe()
        del recipe["workflow"]
        self.assertRecipeError(normalize_recipe, recipe, path="recipe.workflow")

    def test_identity_and_revision_are_strict(self):
        for field, value in (("recipe_schema_version", True), ("recipe_schema_version", 2),
                             ("revision", True), ("revision", 0), ("revision", 1.5),
                             ("recipe_id", uid("recipe").upper()), ("recipe_id", "unknown")):
            with self.subTest(field=field, value=value):
                recipe = sample_recipe()
                recipe[field] = value
                self.assertRecipeError(normalize_recipe, recipe, path=f"recipe.{field}")

    def test_non_json_nonfinite_and_cyclic_values_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), ("tuple",), {"set"}):
            with self.subTest(value=repr(value)):
                recipe = sample_recipe()
                recipe["description"] = value
                self.assertRecipeError(normalize_recipe, recipe)
        recipe = sample_recipe()
        recipe["description"] = recipe
        self.assertRecipeError(normalize_recipe, recipe)

    def test_invalid_utf8_deep_tree_and_oversized_text_are_rejected(self):
        for value in ("\ud800", "가" * 3000):
            recipe = sample_recipe()
            recipe["title"] = value
            self.assertRecipeError(normalize_recipe, recipe)
        recipe = sample_recipe()
        nested = {}
        recipe["description"] = nested
        for _ in range(30):
            nested["nested"] = {}
            nested = nested["nested"]
        self.assertRecipeError(normalize_recipe, recipe)

    def test_target_completion_criterion_must_exist(self):
        recipe = sample_recipe()
        recipe["completion_criteria"] = []
        self.assertRecipeError(normalize_recipe, recipe,
                               path="recipe.workflow.targets.report.required_criteria")

    def test_workflow_structural_error_retains_cause(self):
        recipe = sample_recipe()
        recipe["workflow"]["steps"]["render"]["inputs"]["totals"]["step"] = "missing"
        error = self.assertRecipeError(normalize_recipe, recipe)
        self.assertEqual("invalid_workflow", error.details["cause_code"])
        self.assertTrue(error.details["path"].startswith("recipe.workflow"))

    def test_completion_criteria_parity_with_engine(self):
        valid = [
            None, [], ["Legacy criterion"], [{"text": "Automatic", "human_review": False}],
            [{"id": "c", "text": "Review", "basis": "Source", "human_review": "Compare visually"}],
            [{"id": "c", "text": "Review", "human_review": False, "requires_approval": True,
              "approval_scope": "visual", "requires_independent_review": False}],
            [{"text": "Check", "approval_scope": None, "requires_independent_review": True}],
        ]
        for criteria in valid:
            with self.subTest(criteria=criteria):
                recipe = sample_recipe()
                recipe["workflow"]["targets"]["report"]["required_criteria"] = []
                recipe["completion_criteria"] = copy.deepcopy(criteria)
                expected = Engine._criteria(None, criteria)
                self.assertEqual(expected, normalize_recipe(recipe)["completion_criteria"])
                self.assertEqual(expected, normalize_recipe(recipe, criteria_normalizer=lambda items: Engine._criteria(None, items))["completion_criteria"])

    def test_invalid_criteria_match_engine_rejections(self):
        invalid = [
            [{"text": "Check", "human_review": None}],
            [{"text": "Check", "human_review": True}],
            [{"text": "Check", "human_review": 0}],
            [{"text": "Check", "human_review": ""}],
            [{"text": "Check", "human_review": "Review", "requires_approval": False}],
            [{"text": "Check", "basis": ""}],
            [{"text": "Check", "requires_independent_review": 1}],
            [{"text": "Check", "approval_scope": "visual"}],
            [{"id": "same", "text": "A"}, {"id": "same", "text": "B"}],
            [{"text": "Check", "status": "passed"}],
        ]
        for criteria in invalid:
            with self.subTest(criteria=criteria):
                with self.assertRaises(EngineError):
                    Engine._criteria(None, criteria)
                recipe = sample_recipe()
                recipe["completion_criteria"] = criteria
                self.assertRecipeError(normalize_recipe, recipe)

    def test_callback_cannot_inject_verdicts(self):
        recipe = sample_recipe()
        self.assertRecipeError(normalize_recipe, recipe,
                               criteria_normalizer=lambda _: [{"id": "report-valid", "text": "Check", "status": "passed"}])

    def test_false_zero_empty_string_and_literal_commands_are_preserved(self):
        recipe = sample_recipe()
        text = "$(echo value); Customer A"
        values = {"period": "2026-11", "include_summary": False, "precision": 0,
                  "scale": 0, "caption": text}
        bound = bind_recipe_parameters(recipe, values)
        self.assertEqual(bound, values)
        self.assertEqual("", bind_recipe_parameters(recipe, {"period": ""})["period"])
        self.assertEqual(sample_recipe()["workflow"]["steps"]["render"]["action"],
                         recipe["workflow"]["steps"]["render"]["action"])

    def test_only_explicit_defaults_are_applied(self):
        recipe = sample_recipe()
        recipe["workflow"]["parameters"]["optional"] = {"type": "string", "required": False}
        values = bind_recipe_parameters(recipe, {"period": "2026-11"})
        self.assertEqual(True, values["include_summary"])
        self.assertEqual(0, values["precision"])
        self.assertEqual("", values["caption"])
        self.assertNotIn("optional", values)

    def test_invalid_parameter_types_unknown_and_missing_are_rejected(self):
        for values in ({}, {"period": "x", "precision": True}, {"period": "x", "scale": False},
                       {"period": "x", "scale": float("nan")}, {"period": "x", "unknown": 1},
                       {"period": None}, {"period": "x", "precision": 1.5}):
            with self.subTest(values=values):
                with self.assertRaises(EngineError) as raised:
                    bind_recipe_parameters(sample_recipe(), values)
                self.assertEqual("invalid_parameters", raised.exception.code)

    def test_enum_validation_uses_typed_values(self):
        recipe = sample_recipe()
        recipe["workflow"]["parameters"]["precision"]["enum"] = [0, 2]
        self.assertEqual(0, bind_recipe_parameters(recipe, {"period": "x", "precision": 0})["precision"])
        for precision in (True, 1):
            with self.assertRaises(EngineError):
                bind_recipe_parameters(recipe, {"period": "x", "precision": precision})

    def test_binding_check_reports_missing_unknown_and_parameter_error(self):
        result = check_recipe_bindings(sample_recipe(), input_slots={"typo"}, parameters={})
        self.assertTrue(result["schema_valid"])
        self.assertFalse(result["bindings_valid"])
        self.assertEqual(["sales"], result["missing_inputs"])
        self.assertEqual(["typo"], result["unknown_inputs"])
        self.assertEqual("invalid_parameters", result["parameter_errors"][0]["code"])
        self.assertEqual("not_checked", result["qualification"]["status"])
        self.assertFalse(result["execution_authorized"])

    def test_optional_file_input_can_be_absent(self):
        recipe = sample_recipe()
        recipe["workflow"]["inputs"]["reference"] = {"required": False, "format": "txt"}
        result = check_recipe_bindings(recipe, input_slots={"sales"}, parameters={"period": "x"})
        self.assertTrue(result["bindings_valid"])
        self.assertTrue(result["supported"])

    def test_capabilities_are_explicit_not_assumed(self):
        recipe = sample_recipe()
        recipe["requirements"] = {"features": ["workflow_v2", "partial_regeneration"]}
        arguments = {"input_slots": {"sales"}, "parameters": {"period": "x"}}
        result = check_recipe_bindings(recipe, **arguments)
        self.assertIsNone(result["supported"])
        self.assertEqual(["partial_regeneration", "workflow_v2"], result["unknown_features"])
        result = check_recipe_bindings(recipe, capabilities={"features": {"workflow_v2": True}}, **arguments)
        self.assertFalse(result["supported"])
        self.assertEqual(["partial_regeneration"], result["unsupported_features"])
        result = check_recipe_bindings(recipe, capabilities={"features": {"workflow_v2": True, "partial_regeneration": True}}, **arguments)
        self.assertTrue(result["supported"])
        result = check_recipe_bindings(recipe, capabilities={"features": {"workflow_v2": True, "partial_regeneration": "unverified"}}, **arguments)
        self.assertIsNone(result["supported"])

    def test_malformed_slot_collection_and_capabilities_are_rejected(self):
        for slots in ("sales", ["sales", "sales"], {1}, {""}, {"가" * 81}, {"\ud800"}):
            self.assertRecipeError(check_recipe_bindings, sample_recipe(), input_slots=slots, parameters={"period": "x"})
        self.assertRecipeError(check_recipe_bindings, sample_recipe(), input_slots={"sales"}, parameters={"period": "x"}, capabilities={"features": []})

    def test_origin_and_qualification_remain_unverified_references(self):
        recipe = sample_recipe()
        recipe["origin"] = run_reference()
        recipe["qualification"] = [{"case_id": "alternate-input", **run_reference("qualification"), "note": "Compare actual evidence separately"}]
        normalized = normalize_recipe(recipe)
        self.assertEqual(recipe["origin"], normalized["origin"])
        result = check_recipe_bindings(recipe, input_slots={"sales"}, parameters={"period": "x"})
        self.assertEqual({"status": "not_checked", "case_count": 1,
                          "claim_origin": "caller_supplied_reference"}, result["qualification"])

    def test_qualification_cannot_claim_success_and_references_are_strict(self):
        recipe = sample_recipe()
        recipe["qualification"] = [{"case_id": "test", **run_reference(), "status": "passed"}]
        self.assertRecipeError(normalize_recipe, recipe, path="recipe.qualification[0].status")
        recipe["qualification"] = [{"case_id": "same", **run_reference()}, {"case_id": "same", **run_reference("other")}]
        self.assertRecipeError(normalize_recipe, recipe, path="recipe.qualification")
        recipe = sample_recipe()
        recipe["origin"] = run_reference()
        recipe["origin"]["run_ids"] *= 2
        self.assertRecipeError(normalize_recipe, recipe, path="recipe.origin.run_ids")

    def test_requirement_names_are_bounded_unique_and_non_executable(self):
        for features in (["workflow_v2", "workflow_v2"], ["../install"], ["run shell"], [1], "workflow_v2"):
            recipe = sample_recipe()
            recipe["requirements"] = {"features": features}
            self.assertRecipeError(normalize_recipe, recipe)

    def test_seed_pins_manifest_and_does_not_copy_authority(self):
        recipe = sample_recipe()
        recipe["origin"] = run_reference()
        before = copy.deepcopy(recipe)
        bindings = {"sales": {"ref": f"source:{uid('source')}", "mode": "follow", "binding_revision": 1}}
        seed = recipe_seed(recipe, recipe_reference=manifest_reference(), source_bindings=bindings,
                           parameters={"period": "2026-11"})
        self.assertEqual(before, recipe)
        self.assertEqual(manifest_reference(), seed["recipe_binding"]["manifest_reference"])
        self.assertEqual("b" * 64, seed["recipe_binding"]["manifest_sha256"])
        self.assertEqual(recipe_digest(recipe), seed["recipe_binding"]["semantic_digest"])
        self.assertEqual(bindings, seed["input_bindings"])
        self.assertEqual({"definition", "input_bindings", "parameters", "environment", "completion_criteria", "recipe_binding"}, set(seed))
        seed["input_bindings"]["sales"]["binding_revision"] = 2
        self.assertEqual(1, bindings["sales"]["binding_revision"])

    def test_seed_rejects_unregistered_path_or_mutable_manifest_reference(self):
        recipe = sample_recipe()
        valid_binding = {"sales": {"ref": f"source:{uid('source')}", "mode": "follow", "binding_revision": 1}}
        for bindings in ({"sales": {"path": "/private/file"}}, {},
                         {"sales": {"ref": "source:not-a-uuid", "mode": "follow", "binding_revision": 1}}):
            self.assertRecipeError(recipe_seed, recipe, recipe_reference=manifest_reference(),
                                   source_bindings=bindings, parameters={"period": "x"})
        reference = manifest_reference()
        reference.pop("artifact_revision")
        self.assertRecipeError(recipe_seed, recipe, recipe_reference=reference,
                               source_bindings=valid_binding, parameters={"period": "x"})

    def test_seed_does_not_invent_required_environment(self):
        recipe = sample_recipe()
        recipe["workflow"]["environment"] = {"renderer": {"required": True}}
        recipe["workflow"]["steps"]["render"]["environment"] = ["renderer"]
        arguments = {"recipe_reference": manifest_reference(),
                     "source_bindings": {"sales": {"ref": f"source:{uid('source')}", "mode": "pinned", "binding_revision": 1}},
                     "parameters": {"period": "x"}}
        with self.assertRaises(EngineError) as raised:
            recipe_seed(recipe, **arguments)
        self.assertEqual("invalid_environment", raised.exception.code)
        seed = recipe_seed(recipe, environment={"renderer": "fixture-renderer-1"}, **arguments)
        self.assertEqual({"renderer": "fixture-renderer-1"}, seed["environment"])

    def test_published_schema_validates_normalized_manifest_offline(self):
        validator = recipe_validator()
        recipe = sample_recipe()
        recipe["origin"] = run_reference()
        recipe["qualification"] = [{"case_id": "test", **run_reference("case")}]
        validator.validate(normalize_recipe(recipe))
        corrupted = normalize_recipe(recipe)
        corrupted["criteria_results"] = []
        with self.assertRaises(ValidationError):
            validator.validate(corrupted)
        corrupted = normalize_recipe(recipe)
        corrupted["workflow"]["steps"]["render"]["outputs"]["report"]["unexpected"] = True
        with self.assertRaises(ValidationError):
            validator.validate(corrupted)

    def test_schema_and_runtime_reject_suffix_characters_in_recipe_identifiers(self):
        validator = recipe_validator()
        for suffix in ("\n", "\r", "\r\n", "\u2028"):
            for field in ("recipe_id", "origin_task", "origin_run", "origin_digest", "feature"):
                with self.subTest(suffix=repr(suffix), field=field):
                    recipe = sample_recipe()
                    recipe["origin"] = run_reference()
                    recipe["requirements"] = {"features": ["incremental_workflows"]}
                    if field == "recipe_id":
                        recipe["recipe_id"] += suffix
                    elif field == "origin_task":
                        recipe["origin"]["task_id"] += suffix
                    elif field == "origin_run":
                        recipe["origin"]["run_ids"][0] += suffix
                    elif field == "origin_digest":
                        recipe["origin"]["workflow_digest"] += suffix
                    else:
                        recipe["requirements"]["features"][0] += suffix
                    with self.assertRaises(ValidationError):
                        validator.validate(recipe)
                    self.assertRecipeError(normalize_recipe, recipe)

    def test_schema_and_runtime_reject_suffix_characters_in_workflow_identifiers(self):
        validator = recipe_validator()
        for suffix in ("\n", "\r", "\r\n", "\u2028"):
            for field in ("input", "step", "output", "target", "parameter", "check"):
                with self.subTest(suffix=repr(suffix), field=field):
                    recipe = sample_recipe()
                    workflow = recipe["workflow"]
                    # Keep references consistent so runtime rejection tests the
                    # identifier itself, not a deliberately dangling edge.
                    if field == "input":
                        workflow["inputs"]["sales" + suffix] = workflow["inputs"].pop("sales")
                        workflow["steps"]["calculate"]["inputs"]["data"]["input"] += suffix
                    elif field == "step":
                        workflow["steps"]["calculate" + suffix] = workflow["steps"].pop("calculate")
                        workflow["steps"]["render"]["inputs"]["totals"]["step"] += suffix
                    elif field == "output":
                        outputs = workflow["steps"]["calculate"]["outputs"]
                        outputs["totals" + suffix] = outputs.pop("totals")
                        workflow["steps"]["render"]["inputs"]["totals"]["output"] += suffix
                    elif field == "target":
                        workflow["targets"]["report" + suffix] = workflow["targets"].pop("report")
                    elif field == "parameter":
                        workflow["parameters"]["period" + suffix] = workflow["parameters"].pop("period")
                        workflow["steps"]["render"]["parameters"][0] += suffix
                    else:
                        workflow["steps"]["calculate"]["required_checks"][0] += suffix
                    with self.assertRaises(ValidationError):
                        validator.validate(recipe)
                    self.assertRecipeError(normalize_recipe, recipe)


if __name__ == "__main__":
    unittest.main()
