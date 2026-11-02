"""Pure recipe contracts and typed bindings for reusable local work.

A recipe describes a workflow; it is not an execution receipt or permission.
This module reads no files, invokes no tools and does not authenticate origin or
qualification claims. The engine pins managed manifest bytes and verifies any
referenced tasks, runs and artifacts separately.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid

from .fs import EngineError
from .workflow import normalize_environment, normalize_parameters, normalize_workflow


MAX_RECIPE_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 65536
MAX_QUALIFICATION_CASES = 100
_FEATURE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_FIELDS = {
    "recipe_schema_version", "recipe_id", "revision", "title", "description",
    "workflow", "completion_criteria", "origin", "qualification", "requirements",
}


def _error(message, path, *, code="invalid_recipe", **details):
    raise EngineError(code, message, {"path": path, **details})


def _json_copy(value, *, path="recipe", maximum=MAX_RECIPE_BYTES):
    """Reject non-JSON Python values, cycles, non-finite values and large trees."""
    nodes = 0
    ancestors = set()

    def visit(item, location, depth):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _error("Recipe JSON is too deeply nested or has too many values", location)
        if type(item) in (dict, list):
            identity = id(item)
            if identity in ancestors:
                _error("Recipe JSON cannot contain a cycle", location)
            ancestors.add(identity)
            try:
                if type(item) is dict:
                    result = {}
                    for key, child in item.items():
                        if type(key) is not str or not key or len(key.encode("utf-8")) > 240:
                            _error("JSON object keys must be bounded nonempty strings", location)
                        result[key] = visit(child, f"{location}.{key}", depth + 1)
                    return result
                return [visit(child, f"{location}[{index}]", depth + 1)
                        for index, child in enumerate(item)]
            finally:
                ancestors.remove(identity)
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        _error("Recipe values must be finite, native JSON values", location)

    try:
        result = visit(value, path, 0)
        size = len(json.dumps(result, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8"))
    except (UnicodeError, ValueError, OverflowError, RecursionError) as exc:
        if isinstance(exc, EngineError):
            raise
        _error("Recipe must be valid bounded UTF-8 JSON", path)
    if size > maximum:
        _error("Recipe exceeds the size limit", path, maximum_bytes=maximum)
    return result


def _object(value, allowed, required, path):
    if type(value) is not dict:
        _error("Expected an object", path)
    unknown = set(value) - allowed
    if unknown:
        _error("Unsupported recipe field", f"{path}.{sorted(unknown)[0]}")
    missing = required - set(value)
    if missing:
        _error("Required recipe field is missing", f"{path}.{sorted(missing)[0]}")


def _text(value, path, maximum, *, allow_empty=False):
    if type(value) is not str:
        _error("Expected a bounded string", path)
    if len(value.encode("utf-8")) > maximum or (not allow_empty and not value.strip()):
        _error("Expected a bounded nonempty string", path)
    return value if allow_empty else value.strip()


def _uuid(value, path):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        _error("Expected a canonical UUID", path)
    return value


def _positive_integer(value, path):
    if type(value) is not int or value < 1:
        _error("Expected a positive integer", path)
    return value


def _sha(value, path):
    if type(value) is not str or not _SHA256.fullmatch(value):
        _error("Expected a lowercase SHA-256 digest", path)
    return value


def _criteria(values):
    """Match Engine._criteria without importing Engine or adding an approval gate.

    Engine revalidates these definitions during task creation. The parity tests
    cover normalization, false human_review and contradictory approval fields.
    """
    if values is None:
        values = []
    if type(values) is not list or len(values) > 100:
        _error("completion_criteria must contain at most 100 items", "recipe.completion_criteria")
    result, ids = [], set()
    allowed = {"id", "text", "requires_approval", "approval_scope", "basis",
               "human_review", "requires_independent_review"}
    for index, value in enumerate(values):
        path = f"recipe.completion_criteria[{index}]"
        if type(value) is str:
            item = {"id": f"criterion-{index + 1}",
                    "text": _text(value, path, 2000), "requires_approval": False}
        elif type(value) is dict:
            _object(value, allowed, {"text"}, path)
            human_review = "human_review" in value and value["human_review"] is not False
            item = {"id": _text(value.get("id", f"criterion-{index + 1}"), f"{path}.id", 240),
                    "text": _text(value["text"], f"{path}.text", 2000),
                    "requires_approval": value.get("requires_approval", human_review)}
            if type(item["requires_approval"]) is not bool:
                _error("requires_approval must be boolean", f"{path}.requires_approval")
            for field in ("basis", "human_review"):
                if field in value and not (field == "human_review" and not human_review):
                    item[field] = _text(value[field], f"{path}.{field}", 4000)
            if human_review and not item["requires_approval"]:
                _error("human_review requires approval", f"{path}.requires_approval")
            if "requires_independent_review" in value:
                if type(value["requires_independent_review"]) is not bool:
                    _error("requires_independent_review must be boolean",
                           f"{path}.requires_independent_review")
                item["requires_independent_review"] = value["requires_independent_review"]
            if value.get("approval_scope") is not None:
                if not item["requires_approval"]:
                    _error("approval_scope requires approval", f"{path}.approval_scope")
                item["approval_scope"] = _text(value["approval_scope"], f"{path}.approval_scope", 100)
        else:
            _error("Invalid completion criterion", path)
        if item["id"] in ids:
            _error("Completion criterion IDs must be unique", f"{path}.id")
        ids.add(item["id"])
        result.append(item)
    return result


def _run_reference(value, path, *, qualification=False):
    allowed = {"task_id", "workflow_revision", "workflow_digest", "run_ids"}
    required = {"task_id", "workflow_digest", "run_ids"}
    if qualification:
        allowed |= {"case_id", "note"}
        required |= {"case_id"}
    _object(value, allowed, required, path)
    result = {"task_id": _uuid(value["task_id"], f"{path}.task_id"),
              "workflow_digest": _sha(value["workflow_digest"], f"{path}.workflow_digest")}
    if "workflow_revision" in value:
        result["workflow_revision"] = _positive_integer(value["workflow_revision"], f"{path}.workflow_revision")
    run_ids = value["run_ids"]
    if type(run_ids) is not list or not 1 <= len(run_ids) <= 256:
        _error("run_ids must contain between 1 and 256 exact run references", f"{path}.run_ids")
    result["run_ids"] = [_uuid(item, f"{path}.run_ids[{index}]") for index, item in enumerate(run_ids)]
    if len(set(result["run_ids"])) != len(result["run_ids"]):
        _error("Run references must be unique", f"{path}.run_ids")
    if qualification:
        result["case_id"] = _text(value["case_id"], f"{path}.case_id", 240)
        if "note" in value:
            result["note"] = _text(value["note"], f"{path}.note", 2000)
    return result


def normalize_recipe(value, *, criteria_normalizer=None):
    """Validate a manifest and return an independent normalized JSON object.

    ``criteria_normalizer`` may be Engine._criteria. It is a pure normalization
    callback, never an authority or an executor. Origin and qualification remain
    unverified references even when the manifest is structurally valid.
    """
    raw = _json_copy(value)
    _object(raw, _ROOT_FIELDS,
            {"recipe_schema_version", "recipe_id", "revision", "title", "workflow"}, "recipe")
    if type(raw["recipe_schema_version"]) is not int or raw["recipe_schema_version"] != 1:
        _error("Unsupported recipe schema version", "recipe.recipe_schema_version")
    result = {
        "recipe_schema_version": 1,
        "recipe_id": _uuid(raw["recipe_id"], "recipe.recipe_id"),
        "revision": _positive_integer(raw["revision"], "recipe.revision"),
        "title": _text(raw["title"], "recipe.title", 300),
        "description": _text(raw.get("description", ""), "recipe.description", 8000, allow_empty=True),
    }
    try:
        result["workflow"] = normalize_workflow(raw["workflow"])
    except EngineError as exc:
        path = str(exc.details.get("path", ""))
        if not path:
            path = "recipe.workflow"
        elif not path.startswith("recipe."):
            path = f"recipe.{path}" if path.startswith("workflow") else f"recipe.workflow.{path}"
        _error(exc.message, path, cause_code=exc.code)
    try:
        result["completion_criteria"] = (criteria_normalizer or _criteria)(raw.get("completion_criteria"))
    except EngineError as exc:
        if exc.code == "invalid_recipe":
            raise
        _error(exc.message, "recipe.completion_criteria", cause_code=exc.code)
    # A callback cannot inject verdict/authority fields into the manifest.
    result["completion_criteria"] = _criteria(result["completion_criteria"])
    criteria_ids = {item["id"] for item in result["completion_criteria"]}
    for name, target in result["workflow"]["targets"].items():
        for criterion in target["required_criteria"]:
            if criterion not in criteria_ids:
                _error("Workflow target refers to an undefined completion criterion",
                       f"recipe.workflow.targets.{name}.required_criteria", criterion=criterion)
    if "origin" in raw:
        result["origin"] = _run_reference(raw["origin"], "recipe.origin")
    qualification = raw.get("qualification", [])
    if type(qualification) is not list or len(qualification) > MAX_QUALIFICATION_CASES:
        _error("qualification must contain at most 100 case references", "recipe.qualification")
    result["qualification"] = [_run_reference(item, f"recipe.qualification[{index}]", qualification=True)
                               for index, item in enumerate(qualification)]
    case_ids = [item["case_id"] for item in result["qualification"]]
    if len(case_ids) != len(set(case_ids)):
        _error("Qualification case IDs must be unique", "recipe.qualification")
    requirements = raw.get("requirements", {"features": []})
    _object(requirements, {"features"}, set(), "recipe.requirements")
    features = requirements.get("features", [])
    if type(features) is not list or len(features) > 32:
        _error("requirements.features must be a bounded list", "recipe.requirements.features")
    for index, feature in enumerate(features):
        if type(feature) is not str or not _FEATURE.fullmatch(feature):
            _error("Feature names must be bounded identifiers", f"recipe.requirements.features[{index}]")
    if len(set(features)) != len(features):
        _error("Feature requirements must be unique", "recipe.requirements.features")
    result["requirements"] = {"features": sorted(features)}
    return _json_copy(result)


def recipe_digest(recipe):
    """Semantic digest; managed artifact byte hashes remain a separate identity."""
    normalized = normalize_recipe(recipe)
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, allow_nan=False,
                                     sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def bind_recipe_parameters(recipe, values):
    """Apply only declared defaults and typed values; never expand text or commands."""
    normalized = normalize_recipe(recipe)
    return normalize_parameters(normalized["workflow"], values, require_all=True)


def check_recipe_bindings(recipe, *, input_slots, parameters, capabilities=None):
    """Read-only contract check; missing bindings are data, not permission to run.

    An absent capability report yields ``supported=None`` when the recipe asks
    for features. ``qualification.status`` intentionally remains not_checked.
    """
    normalized = normalize_recipe(recipe)
    if not isinstance(input_slots, (set, frozenset, list, tuple)) or len(input_slots) > 256:
        _error("input_slots must be a bounded collection of slot names", "input_slots", code="invalid_recipe_bindings")
    if any(type(item) is not str for item in input_slots):
        _error("Input slot names must be strings", "input_slots", code="invalid_recipe_bindings")
    try:
        if any(not item or len(item.encode("utf-8")) > 240 for item in input_slots):
            _error("Input slot names must be bounded nonempty strings", "input_slots", code="invalid_recipe_bindings")
    except UnicodeError:
        _error("Input slot names must be valid UTF-8", "input_slots", code="invalid_recipe_bindings")
    slots = set(input_slots)
    if len(slots) != len(input_slots):
        _error("Input slots must be unique", "input_slots", code="invalid_recipe_bindings")
    definitions = normalized["workflow"]["inputs"]
    missing = sorted(name for name, item in definitions.items() if item["required"] and name not in slots)
    unknown = sorted(slots - set(definitions))
    parameter_errors, values = [], None
    try:
        values = normalize_parameters(normalized["workflow"], parameters, require_all=True)
    except EngineError as exc:
        parameter_errors.append({"code": exc.code, "message": exc.message, "details": exc.details})
    required_features = normalized["requirements"]["features"]
    unsupported, unknown_features = [], []
    if capabilities is None:
        unknown_features = list(required_features)
    else:
        if type(capabilities) is not dict or type(capabilities.get("features", {})) is not dict:
            _error("capabilities must contain a feature object", "capabilities", code="invalid_recipe_bindings")
        features = capabilities.get("features", {})
        for feature in required_features:
            present = features.get(feature, False)
            if present is False:
                unsupported.append(feature)
            elif present is not True:
                unknown_features.append(feature)
    supported = False if unsupported else (None if unknown_features else True)
    return {
        "schema_valid": True,
        "bindings_valid": not missing and not unknown and not parameter_errors,
        "missing_inputs": missing,
        "unknown_inputs": unknown,
        "parameter_errors": parameter_errors,
        "parameters": values,
        "supported": supported,
        "requirements": copy.deepcopy(normalized["requirements"]),
        "unsupported_features": unsupported,
        "unknown_features": unknown_features,
        "qualification": {"status": "not_checked", "case_count": len(normalized["qualification"]),
                          "claim_origin": "caller_supplied_reference"},
        "execution_authorized": False,
    }


def recipe_seed(recipe, *, recipe_reference, source_bindings, parameters, environment=None):
    """Build an authority-free initialization fragment from engine-checked inputs.

    Paths and source references must already have been checked by the engine.
    The returned fragment contains no UUID for a new task, no execution runs,
    no decisions, no approval records and no completion verdicts.
    """
    normalized = normalize_recipe(recipe)
    reference = _json_copy(recipe_reference, path="recipe_reference", maximum=8000)
    _object(reference, {"task_id", "artifact_id", "artifact_revision", "sha256"},
            {"task_id", "artifact_id", "artifact_revision", "sha256"}, "recipe_reference")
    _uuid(reference["task_id"], "recipe_reference.task_id")
    _uuid(reference["artifact_id"], "recipe_reference.artifact_id")
    _positive_integer(reference["artifact_revision"], "recipe_reference.artifact_revision")
    _sha(reference["sha256"], "recipe_reference.sha256")
    sources = _json_copy(source_bindings, path="source_bindings", maximum=128 * 1024)
    if type(sources) is not dict:
        _error("source_bindings must be an object", "source_bindings", code="invalid_recipe_bindings")
    check = check_recipe_bindings(normalized, input_slots=set(sources), parameters=parameters)
    if not check["bindings_valid"]:
        _error("Recipe input bindings are incomplete or invalid", "bindings", code="invalid_recipe_bindings",
               missing_inputs=check["missing_inputs"], unknown_inputs=check["unknown_inputs"],
               parameter_errors=check["parameter_errors"])
    for slot, value in sources.items():
        _object(value, {"ref", "mode", "binding_revision"}, {"ref", "mode", "binding_revision"}, f"source_bindings.{slot}")
        ref = _text(value["ref"], f"source_bindings.{slot}.ref", 240)
        if not ref.startswith("source:"):
            _error("Recipe source binding must reference a registered source", f"source_bindings.{slot}.ref")
        _uuid(ref[len("source:"):], f"source_bindings.{slot}.ref")
        if value["mode"] not in ("follow", "pinned"):
            _error("Source mode must be follow or pinned", f"source_bindings.{slot}.mode")
        _positive_integer(value["binding_revision"], f"source_bindings.{slot}.binding_revision")
    env = normalize_environment(normalized["workflow"], {} if environment is None else environment, require_all=True)
    binding = {"recipe_id": normalized["recipe_id"], "recipe_revision": normalized["revision"],
               "manifest_sha256": reference["sha256"], "manifest_reference": reference,
               "semantic_digest": recipe_digest(normalized)}
    if "origin" in normalized:
        binding["origin"] = copy.deepcopy(normalized["origin"])
    return {
        "definition": copy.deepcopy(normalized["workflow"]),
        "input_bindings": sources,
        "parameters": check["parameters"],
        "environment": env,
        "completion_criteria": copy.deepcopy(normalized["completion_criteria"]),
        "recipe_binding": binding,
    }
