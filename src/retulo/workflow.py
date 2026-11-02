"""Pure, bounded contracts for repeatable file-producing work.

A workflow describes a caller's work. It never runs a command, reads a file,
selects a model, or grants permission. Runtime bindings and historical runs are
kept separately by the engine. The packaged JSON schema is an export of
``WORKFLOW_SCHEMA``; semantic reference/path/DAG validation lives here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import unicodedata

from jsonschema import Draft202012Validator

from .fs import EngineError


MAX_WORKFLOW_BYTES = 262144
MAX_VALUE_BYTES = 16384
# A bare ``$`` also matches before a final newline in Python and ECMAScript.
# This portable end assertion requires the identifier to consume every character.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}(?![\s\S])"


def _object(properties, required=()):
    return {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}


def _mapping(value, maximum=128, minimum=0):
    return {"type": "object", "propertyNames": {"pattern": ID_PATTERN},
            "additionalProperties": value, "minProperties": minimum,
            "maxProperties": maximum}


_ID = {"type": "string", "pattern": ID_PATTERN}
_IDS = {"type": "array", "items": _ID, "uniqueItems": True, "maxItems": 128}
_DESCRIPTION = {"type": "string", "maxLength": 2000}
_SCALAR = {"type": ["string", "integer", "number", "boolean"], "maxLength": MAX_VALUE_BYTES}
_OUTPUT_REF = _object({"step": _ID, "output": _ID}, ("step", "output"))

WORKFLOW_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:retulo:workflow:1",
    "title": "Retulo workflow definition v1",
    **_object({
        "workflow_schema_version": {"const": 1, "type": "integer"},
        "description": _DESCRIPTION,
        "inputs": _mapping({"$ref": "#/$defs/input"}),
        "parameters": _mapping({"$ref": "#/$defs/parameter"}),
        "environment": _mapping({"$ref": "#/$defs/environment"}, 64),
        "steps": _mapping({"$ref": "#/$defs/step"}, 128, 1),
        "targets": _mapping({"$ref": "#/$defs/target"}, 64, 1),
    }, ("workflow_schema_version", "steps", "targets")),
    "$defs": {
        "input": _object({
            "required": {"type": "boolean"},
            "format": {"type": "string", "minLength": 1, "maxLength": 80},
            "description": _DESCRIPTION,
        }),
        "parameter": _object({
            "type": {"enum": ["string", "integer", "number", "boolean"]},
            "required": {"type": "boolean"},
            "default": _SCALAR,
            "enum": {"type": "array", "items": _SCALAR, "minItems": 1,
                     "maxItems": 128, "uniqueItems": True},
            "description": _DESCRIPTION,
        }, ("type",)),
        "environment": _object({
            "required": {"type": "boolean"}, "description": _DESCRIPTION,
        }),
        "input_ref": {"oneOf": [
            _object({"input": _ID}, ("input",)), _OUTPUT_REF,
        ]},
        "action": {"oneOf": [
            _object({"kind": {"enum": ["agent", "manual"]},
                     "instruction": {"type": "string", "minLength": 1,
                                     "maxLength": 16000}}, ("kind", "instruction")),
            _object({"kind": {"const": "command"},
                     "argv": {"type": "array", "minItems": 1, "maxItems": 128,
                              "items": {"type": "string", "maxLength": 16000}},
                     "instruction": {"type": "string", "minLength": 1,
                                     "maxLength": 16000}}, ("kind", "argv")),
        ]},
        "output": _object({
            "logical_name": {"type": "string", "minLength": 1, "maxLength": 500},
            "role": {"type": "string", "minLength": 1, "maxLength": 80},
            "format": {"type": ["string", "null"], "minLength": 1, "maxLength": 80},
        }, ("logical_name",)),
        "step": _object({
            "description": _DESCRIPTION,
            "action": {"$ref": "#/$defs/action"},
            "inputs": _mapping({"$ref": "#/$defs/input_ref"}),
            "parameters": _IDS,
            "environment": _IDS,
            "outputs": _mapping({"$ref": "#/$defs/output"}, 64, 1),
            "reuse_policy": {"enum": ["if_unchanged", "never", "manual"]},
            "required_checks": _IDS,
        }, ("action", "outputs")),
        "target": _object({
            "outputs": {"type": "array", "items": _OUTPUT_REF,
                        "minItems": 1, "maxItems": 128, "uniqueItems": True},
            "required_criteria": _IDS,
        }, ("outputs",)),
    },
}

_VALIDATOR = Draft202012Validator(WORKFLOW_SCHEMA)


def _fail(message, path="", *, code="invalid_workflow", **details):
    raise EngineError(code, message, {"path": path, **details})


def _json_bytes(value, *, maximum, code="invalid_workflow", path=""):
    def validate_tree(node, depth):
        if depth > 64:
            _fail("JSON nesting exceeds the supported depth", path, code=code)
        if isinstance(node, dict):
            if any(not isinstance(key, str) for key in node):
                _fail("JSON object keys must be strings", path, code=code)
            for child in node.values():
                validate_tree(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                validate_tree(child, depth + 1)
        elif node is not None and type(node) not in (str, bool, int, float):
            _fail("Value must use JSON object, array, or scalar types", path, code=code)
    validate_tree(value, 0)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("Value must be finite UTF-8 JSON", path, code=code)
    if len(encoded) > maximum:
        _fail("Value exceeds the bounded JSON size", path, code=code,
              maximum_bytes=maximum)
    return encoded


def _sha(value):
    return hashlib.sha256(_json_bytes(value, maximum=MAX_WORKFLOW_BYTES * 4)).hexdigest()


def _portable_name(value, path):
    """Match the engine's logical-name boundary without importing Engine."""
    value = unicodedata.normalize("NFC", value.strip())
    if not value or len(value.encode("utf-8")) > 500:
        _fail("Output logical name must be a nonempty bounded string", path)
    parts = value.split("/")
    if PurePosixPath(value).is_absolute() or any(p in ("", ".", "..") for p in parts):
        _fail("Output logical name must be relative without traversal", path)
    if any(p.startswith(".") or p.rstrip(" .") != p for p in parts):
        _fail("Hidden or reserved output paths are not allowed", path)
    if any(re.search(r'[<>:"\\|?*\x00-\x1f]', p) for p in parts):
        _fail("Output logical name contains unsupported characters", path)
    if parts[0].casefold() == "workspace.md":
        _fail("WORKSPACE.md is reserved for the generated task view", path)
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}
    if any(PurePosixPath(p).stem.casefold() in reserved for p in parts):
        _fail("Output logical name is a reserved filename", path)
    return value


def _typed(value, definition, path, *, code):
    expected = definition["type"]
    valid = ((expected == "string" and isinstance(value, str)) or
             (expected == "boolean" and type(value) is bool) or
             (expected == "integer" and type(value) is int) or
             (expected == "number" and (type(value) is int or
                                       type(value) is float and math.isfinite(value))))
    if not valid:
        _fail(f"Value must have type {expected}", path, code=code)
    _json_bytes(value, maximum=MAX_VALUE_BYTES, code=code, path=path)
    if "enum" in definition and not any(type(value) is type(item) and value == item
                                        or expected == "number" and type(value) in (int, float)
                                        and type(item) in (int, float) and value == item
                                        for item in definition["enum"]):
        _fail("Value is outside the declared enum", path, code=code)
    return copy.deepcopy(value)


def _dependencies(workflow):
    return {name: {ref["step"] for ref in step["inputs"].values() if "step" in ref}
            for name, step in workflow["steps"].items()}


def _topological(workflow, subset=None):
    dependencies = _dependencies(workflow)
    chosen = set(dependencies) if subset is None else set(subset)
    remaining = {name: dependencies[name] & chosen for name in chosen}
    result = []
    while remaining:
        ready = sorted(name for name, required in remaining.items() if not required)
        if not ready:
            _fail("Workflow contains a dependency cycle", "steps", cycle=sorted(remaining))
        result.extend(ready)
        for name in ready:
            del remaining[name]
        for required in remaining.values():
            required.difference_update(ready)
    return result


def normalize_workflow(raw):
    """Validate and copy a workflow, supplying only documented defaults.

    No runtime paths, values, verdicts, or approvals belong in a definition.
    IDs are stable ASCII keys; user-facing descriptions and filenames support
    UTF-8. The caller's original value is never modified.
    """
    _json_bytes(raw, maximum=MAX_WORKFLOW_BYTES)
    if not isinstance(raw, dict) or type(raw.get("workflow_schema_version")) is not int or raw["workflow_schema_version"] != 1:
        _fail("workflow_schema_version must be integer 1", "workflow_schema_version")
    errors = sorted(_VALIDATOR.iter_errors(raw), key=lambda e: "/".join(map(str, e.absolute_path)))
    if errors:
        error = errors[0]
        _fail(error.message, "/".join(map(str, error.absolute_path)))
    workflow = copy.deepcopy(raw)
    for section in ("inputs", "parameters", "environment"):
        workflow.setdefault(section, {})
    for slot, declaration in workflow["inputs"].items():
        declaration.setdefault("required", True)
        if "format" in declaration:
            declaration["format"] = declaration["format"].strip()
            if not declaration["format"] or len(declaration["format"].encode()) > 80:
                _fail("Input format must be a bounded nonempty string", f"inputs/{slot}/format")
    for key, definition in workflow["parameters"].items():
        definition.setdefault("required", True)
        if "enum" in definition:
            # Check enum members against the type without testing membership in itself.
            unbounded = {"type": definition["type"]}
            for index, item in enumerate(definition["enum"]):
                _typed(item, unbounded, f"parameters/{key}/enum/{index}", code="invalid_workflow")
        if "default" in definition:
            _typed(definition["default"], definition, f"parameters/{key}/default", code="invalid_workflow")
    for definition in workflow["environment"].values():
        definition.setdefault("required", True)
    output_owners = {}
    for step_id, step in workflow["steps"].items():
        step.setdefault("inputs", {})
        step.setdefault("parameters", [])
        step.setdefault("environment", [])
        step.setdefault("required_checks", [])
        step.setdefault("reuse_policy", "manual")
        action = step["action"]
        if "instruction" in action and not action["instruction"].strip():
            _fail("Action instruction must be nonempty", f"steps/{step_id}/action/instruction")
        if action["kind"] == "command" and not action["argv"][0].strip():
            _fail("Command argv must start with a nonempty executable", f"steps/{step_id}/action/argv/0")
        for index, argument in enumerate(action.get("argv", [])):
            if "\x00" in argument or len(argument.encode()) > 16000:
                _fail("Command argument must be bounded and contain no NUL", f"steps/{step_id}/action/argv/{index}")
        for section in ("parameters", "environment"):
            for key in step[section]:
                if key not in workflow[section]:
                    _fail(f"Step refers to an undeclared {section} key", f"steps/{step_id}/{section}", key=key)
        for port, reference in step["inputs"].items():
            if "input" in reference:
                if reference["input"] not in workflow["inputs"]:
                    _fail("Step refers to an undeclared input slot", f"steps/{step_id}/inputs/{port}")
            else:
                upstream = workflow["steps"].get(reference["step"])
                if upstream is None or reference["output"] not in upstream["outputs"]:
                    _fail("Step refers to an unknown upstream output", f"steps/{step_id}/inputs/{port}")
        for output_id, output in step["outputs"].items():
            path = f"steps/{step_id}/outputs/{output_id}"
            output["logical_name"] = _portable_name(output["logical_name"], f"{path}/logical_name")
            output.setdefault("role", "intermediate")
            output.setdefault("format", None)
            output["role"] = output["role"].strip()
            if not output["role"] or len(output["role"].encode()) > 80:
                _fail("Output role must be a bounded nonempty string", f"{path}/role")
            if output["format"] is not None:
                output["format"] = output["format"].strip()
                if not output["format"] or len(output["format"].encode()) > 80:
                    _fail("Output format must be a bounded nonempty string", f"{path}/format")
            collision_key = output["logical_name"].casefold()
            if collision_key in output_owners:
                _fail("Two output slots cannot own the same logical path", f"{path}/logical_name",
                      previous_owner=output_owners[collision_key])
            output_owners[collision_key] = f"{step_id}/{output_id}"
    for target_id, target in workflow["targets"].items():
        target.setdefault("required_criteria", [])
        for index, reference in enumerate(target["outputs"]):
            step = workflow["steps"].get(reference["step"])
            if step is None or reference["output"] not in step["outputs"]:
                _fail("Target refers to an unknown output", f"targets/{target_id}/outputs/{index}")
    _topological(workflow)
    _json_bytes(workflow, maximum=MAX_WORKFLOW_BYTES)
    return workflow


def workflow_digest(workflow):
    """Canonical digest of the complete normalized definition."""
    return _sha(normalize_workflow(workflow))


def step_digest(workflow, step_id):
    """Digest the execution contract and only the declarations it consumes."""
    return _step_digest_normalized(normalize_workflow(workflow), step_id)


def _step_digest_normalized(normalized, step_id):
    """Internal hot path; caller must own a freshly validated definition."""
    if step_id not in normalized["steps"]:
        _fail("Unknown workflow step", "step_id", step_id=step_id)
    step = copy.deepcopy(normalized["steps"][step_id])
    step.pop("description", None)
    input_slots = {reference["input"] for reference in step["inputs"].values() if "input" in reference}
    def effective(declaration):
        return {key: value for key, value in declaration.items() if key != "description"}
    return _sha({
        "step_id": step_id,
        "step": step,
        "inputs": {key: effective(normalized["inputs"][key]) for key in sorted(input_slots)},
        "parameters": {key: effective(normalized["parameters"][key]) for key in step["parameters"]},
        "environment": {key: effective(normalized["environment"][key]) for key in step["environment"]},
    })


def dependency_closure(workflow, target_ids=None):
    """Return the steps necessary for the selected named targets."""
    return _dependency_closure_normalized(normalize_workflow(workflow), target_ids)


def _dependency_closure_normalized(normalized, target_ids=None):
    targets = list(normalized["targets"]) if target_ids is None else target_ids
    if not isinstance(targets, list) or not targets or any(not isinstance(value, str) for value in targets):
        _fail("target_ids must be a nonempty list of target IDs", "target_ids")
    if len(targets) != len(set(targets)) or any(value not in normalized["targets"] for value in targets):
        _fail("target_ids contains duplicate or unknown targets", "target_ids")
    dependencies = _dependencies(normalized)
    pending = [ref["step"] for target in targets for ref in normalized["targets"][target]["outputs"]]
    result = set()
    while pending:
        step_id = pending.pop()
        if step_id in result:
            continue
        result.add(step_id)
        pending.extend(dependencies[step_id] - result)
    return result


def topological_steps(workflow, step_ids=None):
    """Stable topological order; a subset is ordered within its own members."""
    return _topological_steps_normalized(normalize_workflow(workflow), step_ids)


def _topological_steps_normalized(normalized, step_ids=None):
    if step_ids is not None:
        if not isinstance(step_ids, (set, frozenset, list, tuple)) or any(not isinstance(value, str) for value in step_ids):
            _fail("step_ids must contain step IDs", "step_ids")
        if any(value not in normalized["steps"] for value in step_ids):
            _fail("step_ids contains an unknown step", "step_ids")
    return _topological(normalized, step_ids)


def normalize_parameters(workflow, values, *, require_all=True):
    """Bind typed scalar parameters; preserve false, zero, and empty strings."""
    return _normalize_parameters_normalized(normalize_workflow(workflow), values, require_all=require_all)


def _normalize_parameters_normalized(normalized, values, *, require_all=True):
    code = "invalid_parameters"
    if not isinstance(values, dict) or type(require_all) is not bool:
        _fail("Parameters must be an object", "parameters", code=code)
    _json_bytes(values, maximum=MAX_WORKFLOW_BYTES, code=code, path="parameters")
    if set(values) - set(normalized["parameters"]):
        _fail("Parameters contain undeclared keys", "parameters", code=code,
              unknown=sorted(set(values) - set(normalized["parameters"])))
    result = {}
    for key, definition in normalized["parameters"].items():
        if key in values:
            result[key] = _typed(values[key], definition, f"parameters/{key}", code=code)
        elif "default" in definition:
            result[key] = copy.deepcopy(definition["default"])
        elif definition["required"] and require_all:
            _fail("Required parameter is missing", f"parameters/{key}", code=code)
    return result


def normalize_environment(workflow, values, *, require_all=True):
    """Validate caller-observed scalar environment fingerprints, not processes."""
    return _normalize_environment_normalized(normalize_workflow(workflow), values, require_all=require_all)


def _normalize_environment_normalized(normalized, values, *, require_all=True):
    code = "invalid_environment"
    if not isinstance(values, dict) or type(require_all) is not bool:
        _fail("Environment must be an object", "environment", code=code)
    _json_bytes(values, maximum=MAX_WORKFLOW_BYTES, code=code, path="environment")
    if set(values) - set(normalized["environment"]):
        _fail("Environment contains undeclared keys", "environment", code=code,
              unknown=sorted(set(values) - set(normalized["environment"])))
    result = {}
    for key, definition in normalized["environment"].items():
        if key not in values:
            if definition["required"] and require_all:
                _fail("Required environment fingerprint is missing", f"environment/{key}", code=code)
            continue
        value = values[key]
        if not (type(value) in (str, int, float, bool)) or isinstance(value, str) and not value.strip():
            _fail("Environment fingerprint must be a nonempty scalar", f"environment/{key}", code=code)
        _json_bytes(value, maximum=MAX_VALUE_BYTES, code=code, path=f"environment/{key}")
        result[key] = copy.deepcopy(value)
    return result
