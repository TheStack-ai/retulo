"""Pure, conservative rebuild planning over caller-supplied observations.

The planner never reads files, executes an action, or changes a run.  The engine
collects observations and owns the revision/read-set checks around mutations.
An ``available`` observation is evidence supplied by that boundary, not a claim
that the planner observed a file or every dependency of a host tool.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import re

from .fs import EngineError
from .workflow import (_dependency_closure_normalized,
                       _normalize_environment_normalized,
                       _normalize_parameters_normalized,
                       _step_digest_normalized, _topological_steps_normalized,
                       normalize_workflow)


_STATES = ("reusable", "needs_run", "blocked", "unknown")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNRESOLVED = {"started", "unresolved"}


def _error(message, **details):
    raise EngineError("invalid_planner_input", message, details)


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise EngineError("invalid_planner_input", "Planner inputs must be finite JSON") from exc


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _mapping(value, name):
    if not isinstance(value, dict):
        _error(f"{name} must be an object")
    return value


def _instant(value, name):
    if not isinstance(value, str) or not value:
        _error(f"{name} must be an ISO timestamp with timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EngineError("invalid_planner_input", f"{name} is not a valid timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        _error(f"{name} must include timezone")
    return result.astimezone(timezone.utc)


def _step(workflow, step_id):
    if step_id not in workflow["steps"]:
        _error("Step is not defined", step_id=step_id)
    return workflow["steps"][step_id]


def _observation(ref, resolve_ref):
    if not isinstance(ref, str) or not ref:
        return {"ref": None, "status": "missing"}
    value = resolve_ref(ref)
    if value is None:
        return {"ref": ref, "status": "unavailable"}
    if not isinstance(value, dict):
        _error("A resolved observation must be an object", ref=ref)
    result = copy.deepcopy(value)
    # A callback cannot substitute another identity for the requested record.
    if result.get("ref", ref) != ref:
        _error("Observation reference differs from the requested reference", ref=ref)
    result["ref"] = ref
    result.setdefault("status", "unavailable")
    return result


def resolve_step_inputs(workflow, step_id, input_bindings, selected_outputs, resolve_ref):
    """Resolve declared ports to immutable-value snapshots through a lookup.

    ``resolve_ref`` should normally look up a map that the engine already
    observed. Missing slots and missing upstream outputs remain explicit rather
    than raising, so a new graph can return a useful plan before its first run.
    """
    definition = normalize_workflow(workflow)
    return _resolve_step_inputs(definition, step_id, input_bindings, selected_outputs, resolve_ref)


def _resolve_step_inputs(definition, step_id, input_bindings, selected_outputs, resolve_ref):
    step = _step(definition, step_id)
    _mapping(input_bindings, "input_bindings")
    _mapping(selected_outputs, "selected_outputs")
    if not callable(resolve_ref):
        _error("resolve_ref must be a callable lookup")
    result = {}
    for port, declaration in sorted(step["inputs"].items()):
        if "input" in declaration:
            slot = declaration["input"]
            binding = input_bindings.get(slot)
            optional = not definition["inputs"][slot].get("required", True)
            if binding is None:
                snapshot = {"ref": None, "status": "absent" if optional else "missing"}
            else:
                if not isinstance(binding, dict):
                    _error("Input binding must be an object", input_slot=slot)
                snapshot = _observation(binding.get("ref"), resolve_ref)
                snapshot["binding_mode"] = binding.get("mode", "follow")
            snapshot.update(origin="input", input_slot=slot, optional=optional)
        else:
            upstream, output = declaration["step"], declaration["output"]
            selected = selected_outputs.get(upstream)
            if selected is not None and not isinstance(selected, dict):
                _error("Selected step outputs must be an object", step_id=upstream)
            outputs = (selected or {}).get("outputs", {})
            if not isinstance(outputs, dict):
                _error("Selected output slots must be an object", step_id=upstream)
            snapshot = _observation(outputs.get(output), resolve_ref)
            snapshot.update(origin="step", step_id=upstream, output_slot=output,
                            upstream_run_id=(selected or {}).get("run_id"), optional=False)
        result[port] = snapshot
    return result


def _scoped_values(definition, step_id, parameters, environment):
    # The caller owns a freshly normalized definition and value maps. Missing
    # unrelated required bindings do not block an independent branch.
    step = definition["steps"][step_id]
    scoped_parameters, scoped_environment, missing = {}, {}, []
    for name in step["parameters"]:
        if name in parameters:
            scoped_parameters[name] = {"present": True, "value": parameters[name]}
        else:
            scoped_parameters[name] = {"present": False}
            if definition["parameters"][name].get("required", True):
                missing.append({"code": "parameter_missing", "parameter": name})
    for name in step["environment"]:
        if name in environment:
            scoped_environment[name] = {"present": True, "value": environment[name]}
        else:
            scoped_environment[name] = {"present": False}
            if definition["environment"][name].get("required", True):
                missing.append({"code": "environment_missing", "environment": name})
    return scoped_parameters, scoped_environment, missing


def execution_signature(workflow, step_id, resolved_inputs, parameters, environment):
    """Hash exact input identities/content plus the step's selected conditions.

    Observation time, paths, task revision and unrelated parameter/environment
    values are deliberately excluded. A caller-provided fingerprint never
    replaces the exact ref and content hash, so it cannot hide changed bytes.
    """
    definition = normalize_workflow(workflow)
    parameters = _normalize_parameters_normalized(definition, parameters, require_all=False)
    environment = _normalize_environment_normalized(definition, environment, require_all=False)
    return _execution_signature(definition, step_id, resolved_inputs, parameters, environment,
                                _step_digest_normalized(definition, step_id))


def _execution_signature(definition, step_id, resolved_inputs, parameters, environment, definition_digest):
    step = _step(definition, step_id)
    _mapping(resolved_inputs, "resolved_inputs")
    if set(resolved_inputs) != set(step["inputs"]):
        _error("Resolved input ports differ from the step definition", step_id=step_id)
    scoped_parameters, scoped_environment, missing = _scoped_values(
        definition, step_id, parameters, environment)
    if missing:
        _error("Required execution conditions are missing", missing=missing)
    inputs = {}
    for port, snapshot in sorted(resolved_inputs.items()):
        _mapping(snapshot, f"resolved_inputs.{port}")
        declaration = step["inputs"][port]
        optional = "input" in declaration and not definition["inputs"][declaration["input"]]["required"]
        if snapshot.get("status") == "absent" and optional and snapshot.get("ref") is None:
            inputs[port] = {"absent": True}
            continue
        ref, checksum = snapshot.get("ref"), snapshot.get("sha256")
        if not isinstance(ref, str) or not ref or not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            _error("Resolved inputs need exact references and SHA-256 values", port=port)
        inputs[port] = {"ref": ref, "sha256": checksum,
                        "fingerprint": snapshot.get("fingerprint") or _digest({"ref": ref, "sha256": checksum})}
    return _digest({"step_digest": definition_digest, "inputs": inputs,
                    "parameters": scoped_parameters, "environment": scoped_environment})


def _observation_issue(snapshot, checked_at, *, subject, port=None, ref=None):
    suffix = {"port": port} if port is not None else {}
    if ref is not None:
        suffix["ref"] = ref
    status = snapshot.get("status", "unavailable")
    if subject == "input" and status == "absent" and snapshot.get("optional") is True:
        return None
    if status == "changed":
        return "blocked", {"code": f"{subject}_changed", **suffix}
    if status == "missing":
        state = "needs_run" if subject in {"output", "check_evidence"} else "blocked"
        return state, {"code": f"{subject}_missing", **suffix}
    if status != "available":
        return "unknown", {"code": f"{subject}_unavailable", **suffix}
    checksum = snapshot.get("sha256")
    if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
        return "unknown", {"code": f"{subject}_hash_unknown", **suffix}
    expiry = snapshot.get("valid_until")
    if expiry is not None:
        try:
            expiration = _instant(expiry, "valid_until")
        except EngineError:
            return "unknown", {"code": f"{subject}_expiry_unknown", **suffix}
        if expiration <= checked_at:
            state = "blocked" if subject == "input" else "needs_run"
            return state, {"code": f"{subject}_expired", **suffix}
    return None


def _aggregate(issues, fallback="needs_run"):
    states = {state for state, _ in issues}
    for state in ("blocked", "unknown", "needs_run"):
        if state in states:
            return state
    return fallback


def _stable_observation(snapshot):
    """Meaningful observation content for a plan ID, without read-time noise."""
    fields = ("ref", "status", "sha256", "expected_sha256", "size", "fingerprint",
              "valid_until", "reason", "binding_mode", "upstream_run_id",
              "origin", "input_slot", "step_id", "output_slot", "optional")
    return {field: copy.deepcopy(snapshot[field]) for field in fields if field in snapshot}


def assess_run(workflow, step_id, run, resolved_inputs, parameters, environment,
               observations, checked_at, *, respect_reuse_policy=True, generation=None):
    """Assess current applicability without changing a historical execution.

    ``respect_reuse_policy=False`` is for the engine's current-run prerequisite
    and completion checks. It does not grant permission to reuse a manual/never
    output in a new requested rebuild. The default planner respects the policy.
    """
    definition = normalize_workflow(workflow)
    parameters = _normalize_parameters_normalized(definition, parameters, require_all=False)
    environment = _normalize_environment_normalized(definition, environment, require_all=False)
    checked = _instant(checked_at, "checked_at")
    return _assess_run(definition, step_id, run, resolved_inputs, parameters, environment,
                       observations, checked, _step_digest_normalized(definition, step_id),
                       respect_reuse_policy=respect_reuse_policy, generation=generation)


def _assess_run(definition, step_id, run, resolved_inputs, parameters, environment,
                observations, checked, definition_digest, *, respect_reuse_policy=True, generation=None):
    step = _step(definition, step_id)
    _mapping(resolved_inputs, "resolved_inputs")
    _mapping(observations, "observations")
    if type(respect_reuse_policy) is not bool:
        _error("respect_reuse_policy must be boolean")
    if generation is not None and (type(generation) is not int or generation < 1):
        _error("generation must be a positive integer or null")
    if set(resolved_inputs) != set(step["inputs"]):
        _error("Resolved input ports differ from the step definition", step_id=step_id)
    if run is not None and not isinstance(run, dict):
        _error("run must be an object or null")
    card = {"step_id": step_id, "state": "needs_run", "reasons": [],
            "signature": None, "run_id": run.get("run_id") if run else None,
            "waiting_on": [], "resolved_inputs": copy.deepcopy(resolved_inputs),
            "freshness": "unknown"}
    issues = []
    for port, snapshot in sorted(resolved_inputs.items()):
        _mapping(snapshot, f"resolved_inputs.{port}")
        declaration = step["inputs"][port]
        optional = "input" in declaration and not definition["inputs"][declaration["input"]]["required"]
        if snapshot.get("status") == "absent" and (not optional or snapshot.get("ref") is not None):
            issues.append(("blocked", {"code": "input_missing", "port": port}))
            continue
        issue = _observation_issue(snapshot, checked, subject="input", port=port,
                                   ref=snapshot.get("ref"))
        if issue:
            issues.append(issue)
    _, _, missing = _scoped_values(definition, step_id, parameters, environment)
    issues.extend(("unknown", reason) for reason in missing)
    if not issues:
        card["signature"] = _execution_signature(definition, step_id, resolved_inputs, parameters,
                                                   environment, definition_digest)
    if issues:
        card.update(state=_aggregate(issues), reasons=[reason for _, reason in issues])
        return card
    if run is None:
        card.update(reasons=[{"code": "no_successful_run"}], freshness="missing")
        return card
    if run.get("step_id") != step_id:
        card.update(state="blocked", reasons=[{"code": "run_step_mismatch"}])
        return card
    if run.get("status") in _UNRESOLVED:
        card.update(state="blocked", reasons=[{"code": "run_unresolved", "run_id": run.get("run_id")}])
        return card
    if run.get("status") != "succeeded":
        card.update(reasons=[{"code": "run_not_succeeded", "status": run.get("status", "unknown")}],
                    freshness="missing")
        return card
    definition_changed = run.get("step_digest") != definition_digest
    outputs = run.get("outputs")
    if (not isinstance(outputs, dict) or not outputs
            or any(not isinstance(name, str) or not isinstance(ref, str) or not ref.startswith("artifact:")
                   for name, ref in outputs.items())
            or (not definition_changed and set(outputs) != set(step["outputs"]))):
        card.update(state="blocked", reasons=[{"code": "run_outputs_mismatch"}])
        return card
    # A changed definition may add, remove or rename output slots. Inspect the
    # historical run's real outputs before classifying it for regeneration;
    # applying the new slot contract to an old run would make valid changes
    # impossible to rebuild and could omit a removed output's manual edit.
    for output, ref in sorted(outputs.items()):
        snapshot = _observation(ref, observations.get)
        issue = _observation_issue(snapshot, checked, subject="output", port=output, ref=ref)
        if issue:
            issues.append(issue)
    checks = run.get("checks", {})
    if not isinstance(checks, dict):
        issues.append(("unknown", {"code": "checks_unavailable"}))
        checks = {}
    required_checks = set() if definition_changed else set(step["required_checks"])
    passed_checks = {check_id for check_id, value in checks.items()
                     if isinstance(value, dict) and value.get("status") == "passed"}
    # New requirements do not retroactively apply to an old execution. Already
    # passed evidence is still checked even if that check was removed from the
    # new definition. Optional failed/unknown checks need not become passes.
    for check_id in sorted(required_checks | passed_checks):
        check = checks.get(check_id)
        if not isinstance(check, dict) or check.get("status") != "passed":
            issues.append(("needs_run", {"code": "check_not_passed", "check_id": check_id}))
            continue
        ref = check.get("evidence_ref")
        if not isinstance(ref, str) or not ref.startswith("artifact:"):
            issues.append(("unknown", {"code": "check_evidence_missing", "check_id": check_id}))
            continue
        issue = _observation_issue(_observation(ref, observations.get), checked,
                                   subject="check_evidence", ref=ref)
        if issue:
            issues.append((issue[0], {**issue[1], "check_id": check_id}))
        if "signature" in check and check["signature"] != run.get("signature"):
            issues.append(("needs_run", {"code": "check_signature_changed", "check_id": check_id}))
    # Integrity conflicts have priority over a stale signature so that a manual
    # edit is surfaced instead of being mistaken for ordinary regeneration.
    if issues:
        card.update(state=_aggregate(issues), reasons=[reason for _, reason in issues], freshness="stale")
        return card
    snapshot_issues = run.get("snapshot_issues")
    if snapshot_issues:
        # The engine attaches this observation to a runtime copy of the run.
        # Missing or modified execution snapshots invalidate reuse, not the
        # historical outcome. Valid current inputs can produce a new run.
        observed = snapshot_issues if isinstance(snapshot_issues, list) else [{}]
        details = [{"port": str(issue.get("port", "unknown"))[:96],
                    "code": issue.get("code") if issue.get("code") in {
                        "snapshot_missing", "snapshot_changed", "snapshot_unsafe"} else "snapshot_unavailable"}
                   for issue in observed[:8] if isinstance(issue, dict)]
        card.update(state="needs_run", freshness="stale", reasons=[{
            "code": "execution_snapshot_unavailable", "issues_count": len(observed),
            "issues": details, "issues_omitted": max(0, len(observed) - len(details))}])
        return card
    if definition_changed:
        card.update(reasons=[{"code": "step_definition_changed"}], freshness="stale")
        return card
    if run.get("signature") != card["signature"]:
        card.update(reasons=[{"code": "execution_conditions_changed"}], freshness="stale")
        return card
    coverage = run.get("dependency_coverage", "unknown")
    if coverage not in {"declared_complete", "observed_complete"}:
        card.update(state="unknown", reasons=[{"code": "dependency_coverage_unknown", "coverage": coverage}])
        return card
    card["freshness"] = "current"
    policy = step["reuse_policy"]
    # A result produced in this explicit update generation may feed downstream
    # steps without re-executing forever. It must still pass every freshness,
    # integrity and validation check above. Across generations the policy holds.
    same_generation = generation is not None and type(run.get("generation")) is int and run["generation"] == generation
    if respect_reuse_policy and not same_generation and policy == "never":
        card.update(state="needs_run", reasons=[{"code": "reuse_disabled"}])
    elif respect_reuse_policy and not same_generation and policy == "manual":
        card.update(state="unknown", reasons=[{"code": "manual_reuse_required"}])
    else:
        card.update(state="reusable", reasons=[{"code": "execution_unchanged"}])
    return card


def plan_rebuild(workflow, input_bindings, parameters, environment, runs,
                 active_outputs, observations, checked_at, target_ids=None, *,
                 respect_reuse_policy=True, generation=None):
    """Return a bounded graph plan using only the exact selected active runs.

    A dirty upstream prevents an old downstream result from looking current.
    Missing outputs of a not-yet-run upstream are reported as ``waiting_on``;
    missing source data remains a blocker. Historical successful runs are not
    silently selected in place of the engine's active output binding.
    """
    definition = normalize_workflow(workflow)
    _mapping(input_bindings, "input_bindings")
    _mapping(active_outputs, "active_outputs")
    _mapping(observations, "observations")
    parameters = _normalize_parameters_normalized(definition, parameters, require_all=False)
    environment = _normalize_environment_normalized(definition, environment, require_all=False)
    checked = _instant(checked_at, "checked_at")
    if generation is not None and (type(generation) is not int or generation < 1):
        _error("generation must be a positive integer or null")
    if not isinstance(runs, list) or len(runs) > 10000:
        _error("runs must be a bounded list")
    by_id, by_step = {}, {}
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("run_id"), str) or not run["run_id"]:
            _error("Each run needs a nonempty run_id")
        if not isinstance(run.get("step_id"), str) or not run["step_id"]:
            _error("Each run needs a nonempty step_id", run_id=run["run_id"])
        if run["run_id"] in by_id:
            _error("Run IDs must be unique", run_id=run["run_id"])
        by_id[run["run_id"]] = run
        by_step.setdefault(run.get("step_id"), []).append(run)
    if target_ids is None:
        target_ids = sorted(definition["targets"])
    else:
        if (not isinstance(target_ids, list) or not target_ids
                or any(not isinstance(name, str) or not name for name in target_ids)
                or len(set(target_ids)) != len(target_ids)):
            _error("target_ids must be a nonempty list of distinct target names")
        target_ids = sorted(target_ids)
    selected = _dependency_closure_normalized(definition, target_ids)
    order = _topological_steps_normalized(definition, selected)
    definition_digests = {step_id: _step_digest_normalized(definition, step_id) for step_id in order}
    cards = {}
    for step_id in order:
        step = definition["steps"][step_id]
        resolved = _resolve_step_inputs(definition, step_id, input_bindings, active_outputs, observations.get)
        active = active_outputs.get(step_id)
        if active is not None and not isinstance(active, dict):
            _error("Active outputs must be objects", step_id=step_id)
        run = by_id.get((active or {}).get("run_id"))
        unresolved = [candidate for candidate in by_step.get(step_id, [])
                      if candidate.get("status") in _UNRESOLVED]
        selected_run = unresolved[-1] if unresolved else run
        card = _assess_run(definition, step_id, selected_run, resolved, parameters,
                           environment, observations, checked, definition_digests[step_id],
                           respect_reuse_policy=respect_reuse_policy, generation=generation)
        if active is not None and run is None:
            card.update(state="blocked", reasons=[{"code": "active_run_missing"}], freshness="unknown")
        elif run is not None and (run.get("step_id") != step_id or active.get("outputs") != run.get("outputs")):
            card.update(state="blocked", reasons=[{"code": "active_outputs_mismatch"}], freshness="unknown")
        # A run with published files but no active binding was never adopted by
        # finish_step_run. It cannot silently become an input through planning.
        elif active is None and not unresolved:
            card["run_id"] = None
        parents = sorted({ref["step"] for ref in step["inputs"].values() if "step" in ref})
        waiting = [parent for parent in parents if cards[parent]["state"] != "reusable"]
        if waiting:
            # Remove only the temporary unavailable-input reasons caused by an
            # upstream which is already scheduled for work. Other conflicts or
            # missing conditions remain visible and retain their severity.
            remaining = []
            for reason in card["reasons"]:
                port = reason.get("port")
                temporary = (reason.get("code", "").startswith("input_") and port in resolved
                             and resolved[port].get("origin") == "step"
                             and resolved[port].get("step_id") in waiting)
                if not temporary:
                    remaining.append(reason)
            direct_blocked, direct_unknown = False, False
            for port, snapshot in resolved.items():
                if snapshot.get("origin") != "input":
                    continue
                issue = _observation_issue(snapshot, checked,
                                           subject="input", port=port, ref=snapshot.get("ref"))
                direct_blocked |= issue is not None and issue[0] == "blocked"
                direct_unknown |= issue is not None and issue[0] == "unknown"
            strong_blockers = {"run_unresolved", "active_run_missing", "active_outputs_mismatch",
                               "run_step_mismatch", "run_outputs_mismatch", "output_changed", "check_evidence_changed"}
            strong_unknowns = {"parameter_missing", "environment_missing", "output_unavailable",
                               "output_hash_unknown", "check_evidence_unavailable", "check_evidence_hash_unknown",
                               "dependency_coverage_unknown"}
            if direct_blocked or any(cards[p]["state"] == "blocked" for p in waiting) or any(r.get("code") in strong_blockers for r in remaining):
                state = "blocked"
            elif direct_unknown or any(cards[p]["state"] == "unknown" for p in waiting) or any(r.get("code") in strong_unknowns for r in remaining):
                state = "unknown"
            else:
                state = "needs_run"
            card.update(state=state, signature=None, freshness="stale", waiting_on=waiting,
                        reasons=[*remaining, {"code": "upstream_pending", "steps": waiting}])
        cards[step_id] = card
    counts = {state: sum(card["state"] == state for card in cards.values()) for state in _STATES}
    result = {"workflow_digest": _digest(definition), "checked_at": checked_at,
              "target_ids": target_ids, "order": order, "steps": cards, "counts": counts,
              "ready": [step_id for step_id in order if cards[step_id]["state"] == "needs_run"
                        and not cards[step_id]["waiting_on"] and cards[step_id]["signature"] is not None]}
    # The plan's content ID is stable across observation times. CAS and fresh
    # filesystem checks still belong to each engine mutation, not this digest.
    observations_by_step = {}
    for step_id, card in cards.items():
        # A blocked input may have no execution signature. Its changed bytes
        # must still invalidate the plan. The same applies to changed outputs
        # and check evidence whose reason/status can stay unchanged across two
        # different edits. Include only observations used by this target closure.
        refs = set()
        active = active_outputs.get(step_id) or {}
        for run_id in {card["run_id"], active.get("run_id")} - {None}:
            recorded = by_id.get(run_id, {})
            outputs = recorded.get("outputs", {})
            if isinstance(outputs, dict):
                refs.update(ref for ref in outputs.values() if isinstance(ref, str))
            checks = recorded.get("checks", {})
            if isinstance(checks, dict):
                refs.update(check["evidence_ref"] for check in checks.values()
                            if isinstance(check, dict) and isinstance(check.get("evidence_ref"), str))
        scoped_parameters, scoped_environment, _ = _scoped_values(definition, step_id, parameters, environment)
        observations_by_step[step_id] = {
            "inputs": {port: _stable_observation(snapshot)
                       for port, snapshot in card["resolved_inputs"].items()},
            "artifacts": {ref: _stable_observation(_observation(ref, observations.get)) for ref in sorted(refs)},
            "parameters": scoped_parameters, "environment": scoped_environment}
    result["plan_digest"] = _digest({"workflow_digest": result["workflow_digest"],
        "target_ids": target_ids, "respect_reuse_policy": respect_reuse_policy, "generation": generation,
        "steps": {name: {key: card[key] for key in ("state", "signature", "run_id", "waiting_on", "reasons")}
                  for name, card in cards.items()}, "observations": observations_by_step})
    return result
