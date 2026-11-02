"""Workflow lifecycle integrated with the ordinary Engine transaction boundary.

This module records and checks local work; the host still performs every action.
Input snapshots, operation receipts and immutable artifacts do not authenticate
the host's semantic claims or mediate unrelated tools.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from .fs import EngineError, digest, file_info, publish_copy, safe_path
from .workflow import (dependency_closure, normalize_environment,
                       normalize_parameters, normalize_workflow, step_digest,
                       workflow_digest)
from . import planner


def _now():
    return datetime.now(timezone.utc).isoformat()


def _bounded(value, name, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise EngineError("invalid_input", f"{name} must be a nonempty bounded string")
    return value.strip()


def _artifact_ref(artifact):
    return f"artifact:{artifact['artifact_id']}:{artifact['revision']}"


def _assessment_summary(card):
    return {"step_id": card["step_id"], "state": card["state"],
            "reasons": card["reasons"][:8], "reason_count": len(card["reasons"]),
            "waiting_on": card.get("waiting_on", [])}


class WorkflowEngine:
    """Internal mixin. Public methods use Engine's existing lock and journal."""

    def _require_workflow(self, state):
        if state.get("schema_version") != 2 or not isinstance(state.get("workflow"), dict):
            raise EngineError("workflow_required", "Define a workflow before using step operations")
        return state["workflow"]

    def _workflow_state(self, definition, parameters=None, environment=None):
        definition = normalize_workflow(definition)
        return {
            "schema_version": 2,
            "workflow": {"revision": 1, "generation": 1,
                         "definition": definition, "digest": workflow_digest(definition),
                         "parameters": normalize_parameters(definition, {} if parameters is None else parameters, require_all=False),
                         "environment": normalize_environment(definition, {} if environment is None else environment, require_all=False)},
            "workflow_history": [], "input_bindings": {}, "runs": [],
            "active_outputs": {}, "current_deliverable_set": None,
        }

    def _validate_workflow_state(self, state):
        workflow = self._require_workflow(state)
        definition = normalize_workflow(workflow["definition"])
        if workflow_digest(definition) != workflow["digest"]:
            raise EngineError("invalid_state", "Workflow definition digest differs from its record")
        normalize_parameters(definition, workflow["parameters"], require_all=False)
        normalize_environment(definition, workflow["environment"], require_all=False)
        self._validate_target_criteria(state, definition)
        for slot, binding in state["input_bindings"].items():
            if slot not in definition["inputs"] or not isinstance(binding.get("ref"), str):
                raise EngineError("invalid_state", "Workflow input binding is invalid")
        ids = [run.get("run_id") for run in state["runs"]]
        if len(ids) != len(set(ids)):
            raise EngineError("invalid_state", "Workflow run IDs must be unique")

    def _validate_target_criteria(self, state, definition):
        known = {item["id"] for item in state["completion_criteria"]}
        requested = {key for target in definition["targets"].values()
                     for key in target["required_criteria"]}
        if not requested.issubset(known):
            raise EngineError("invalid_workflow", "Target refers to unknown completion criteria",
                              {"criteria": sorted(requested - known)})

    def _invalidate_workflow_criteria(self, state, step_id=None):
        definition = self._require_workflow(state)["definition"]
        affected = set()
        for target_id, target in definition["targets"].items():
            if step_id is None or step_id in dependency_closure(definition, [target_id]):
                affected.update(target["required_criteria"])
        for result in state["criteria_results"]:
            if result["criterion"] in affected and result["status"] == "passed":
                result["status"] = "pending"
                result["note"] = "Workflow inputs or selected outputs changed; revalidate current targets"

    def define_workflow(self, task_id, operation_id, expected_revision, definition,
                        parameters=None, environment=None):
        definition = normalize_workflow(definition)
        supplied_parameters = copy.deepcopy(parameters)
        supplied_environment = copy.deepcopy(environment)
        payload = {"method": "define_workflow", "definition": definition,
                   "parameters": supplied_parameters, "environment": supplied_environment}

        def change(root, state):
            if state["status"] != "active":
                raise EngineError("task_not_active", "Workflow changes require an active task")
            self._validate_target_criteria(state, definition)
            old = state.get("workflow")
            if state.get("schema_version") == 1 and any(key in state for key in
                    ("workflow", "workflow_history", "input_bindings", "runs", "active_outputs", "current_deliverable_set", "recipe_binding")):
                raise EngineError("migration_conflict", "Legacy state already uses reserved workflow fields; preserve and reconcile it explicitly")
            if old is None:
                state.update(self._workflow_state(definition, parameters, environment))
            else:
                new_parameters = normalize_parameters(definition, parameters if parameters is not None else
                    {k: v for k, v in old["parameters"].items() if k in definition["parameters"]}, require_all=False)
                new_environment = normalize_environment(definition, environment if environment is not None else
                    {k: v for k, v in old["environment"].items() if k in definition["environment"]}, require_all=False)
                state["workflow_history"].append(copy.deepcopy(old))
                state["workflow"] = {"revision": old["revision"] + 1, "generation": old["generation"] + 1,
                    "definition": definition, "digest": workflow_digest(definition),
                    "parameters": new_parameters, "environment": new_environment}
                state["input_bindings"] = {k: v for k, v in state["input_bindings"].items() if k in definition["inputs"]}
                state["active_outputs"] = {k: v for k, v in state["active_outputs"].items() if k in definition["steps"]}
                self._invalidate_workflow_criteria(state)
            state["mode"] = "expanded"
            return {"workflow_revision": state["workflow"]["revision"],
                    "workflow_digest": state["workflow"]["digest"],
                    "generation": state["workflow"]["generation"], "schema_version": 2}, []

        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def _source_record(self, state, reference):
        source_id = reference.removeprefix("source:") if isinstance(reference, str) else None
        item = next((source for source in state["sources"] if source["source_id"] == source_id), None)
        if item is None:
            raise EngineError("unknown_source", "Input must reference a registered source observation")
        return item

    def bind_input(self, task_id, operation_id, expected_revision, slot, source_id, mode="follow"):
        slot, source_id = _bounded(slot, "slot", 96), _bounded(source_id, "source_id", 240)
        if mode not in ("follow", "pinned"):
            raise EngineError("invalid_input", "Input mode must be follow or pinned")
        payload = {"method": "bind_input", "slot": slot, "source_id": source_id, "mode": mode}

        def change(root, state):
            workflow = self._require_workflow(state)
            if state["status"] != "active":
                raise EngineError("task_not_active", "Input binding requires an active task")
            if slot not in workflow["definition"]["inputs"]:
                raise EngineError("unknown_input", "Input slot is not declared")
            source = self._source_record(state, source_id)
            if mode == "pinned" and source["mode"] != "copy":
                raise EngineError("snapshot_required", "Pinned bindings require add_source(mode='copy')")
            ref = f"source:{source['source_id']}"
            if any(key != slot and item["ref"] == ref and item["mode"] != mode
                   for key, item in state["input_bindings"].items()):
                raise EngineError("binding_mode_conflict", "Register separate source observations for different binding policies")
            observation = self._observe_source(root, source, mode)
            if observation["status"] != "available":
                raise EngineError("source_changed", "Register the current source bytes before binding",
                                  {"slot": slot, "observation": observation})
            previous = state["input_bindings"].get(slot)
            value = {"ref": ref, "mode": mode,
                     "binding_revision": (previous or {}).get("binding_revision", 0) + 1,
                     "bound_at": _now()}
            state["input_bindings"][slot] = value
            workflow["generation"] += 1
            self._invalidate_workflow_criteria(state)
            return {"slot": slot, "binding": value, "generation": workflow["generation"]}, []

        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def _observe_source(self, root, source, mode):
        ref = f"source:{source['source_id']}"
        try:
            path = self._managed(root, source["path"], exists=True) if mode == "pinned" else self._source(source["origin_path"])
            actual = file_info(path)
            status = "available" if actual["sha256"] == source["sha256"] else "changed"
            return {"ref": ref, "status": status, "sha256": actual["sha256"], "size": actual["size"],
                    "expected_sha256": source["sha256"], "path": str(path),
                    "external": mode != "pinned", "fingerprint": digest({"ref": ref, "sha256": actual["sha256"]}),
                    "provenance": "engine_observed", "observed_at": _now(), "valid_until": None}
        except (EngineError, OSError) as exc:
            return {"ref": ref, "status": "missing" if getattr(exc, "code", None) == "missing_file" else "unavailable", "reason": getattr(exc, "code", "unreadable_file"),
                    "provenance": "engine_observed", "observed_at": _now()}

    def _workflow_observations(self, root, state):
        modes = {item["ref"]: item["mode"] for item in state["input_bindings"].values()}
        observations = {}
        active_run_ids = {item["run_id"] for item in state["active_outputs"].values()}
        relevant_artifacts = {ref for item in state["active_outputs"].values() for ref in item["outputs"].values()}
        relevant_artifacts.update(check["evidence_ref"] for run in state["runs"] if run["run_id"] in active_run_ids
                                  for check in run["checks"].values() if check.get("evidence_ref"))
        for source in state["sources"]:
            ref = f"source:{source['source_id']}"
            # Only active sources are relevant to a new plan. Historical source
            # references remain in immutable run read-sets, not live bindings.
            if ref in modes:
                observations[ref] = self._observe_source(root, source, modes[ref])
        for artifact in state["artifacts"]:
            ref = _artifact_ref(artifact)
            if ref not in relevant_artifacts:
                continue
            try:
                path = self._artifact_path(root, state, artifact)
                if not path.exists():
                    observations[ref] = {"ref": ref, "status": "missing", "reason": "missing_file",
                                         "provenance": "engine_observed", "observed_at": _now()}
                    continue
                actual = file_info(path)
                observations[ref] = {"ref": ref,
                    "status": "available" if actual["sha256"] == artifact["sha256"] else "changed",
                    "sha256": actual["sha256"], "size": actual["size"],
                    "expected_sha256": artifact["sha256"], "path": str(path),
                    "external": bool(artifact.get("external")),
                    "fingerprint": digest({"ref": ref, "sha256": actual["sha256"]}),
                    "provenance": "engine_observed", "observed_at": _now(), "valid_until": None}
            except (EngineError, OSError) as exc:
                observations[ref] = {"ref": ref, "status": "unavailable",
                    "reason": getattr(exc, "code", "unreadable_file"), "provenance": "engine_observed", "observed_at": _now()}
        return observations

    def _workflow_plan(self, root, state, target_ids=None):
        workflow = self._require_workflow(state)
        observations = self._workflow_observations(root, state)
        active_run_ids = {item["run_id"] for item in state["active_outputs"].values()}
        observed_runs = [self._observed_run(root, run) if run["run_id"] in active_run_ids else run
                         for run in state["runs"]]
        plan = planner.plan_rebuild(workflow["definition"], state["input_bindings"],
            workflow["parameters"], workflow["environment"], observed_runs,
            state["active_outputs"], observations, _now(), target_ids,
            generation=workflow["generation"])
        return plan, observations

    def _observed_run(self, root, run):
        """Overlay current snapshot integrity without changing historical facts."""
        observed = copy.deepcopy(run)
        issues = []
        if run["status"] == "succeeded":
            for port, value in run["resolved_inputs"].items():
                if value.get("status") == "absent" and value.get("optional"):
                    continue
                try:
                    path = self._managed(root, value.get("snapshot_path"), exists=True)
                    if file_info(path)["sha256"] != value.get("sha256"):
                        issues.append({"port": port, "code": "snapshot_changed"})
                except (EngineError, OSError) as exc:
                    issues.append({"port": port, "code": getattr(exc, "code", "snapshot_unavailable")})
        observed["snapshot_issues"] = issues
        return observed

    def plan_rebuild(self, task_id, target_ids=None, detail="compact", offset=0, limit=20,
                     expected_plan_digest=None):
        from .projection import plan_page
        root = self._path(task_id)
        state = self._load(root, task_id)
        plan, _ = self._workflow_plan(root, state, target_ids)
        full = self._result(root, state, observed_revision=state["revision"], **plan)
        return plan_page(full, detail=detail, offset=offset, limit=limit,
                         expected_plan_digest=expected_plan_digest)

    def _run(self, state, run_id, *, started=False):
        run = next((item for item in state["runs"] if item["run_id"] == run_id), None)
        if run is None:
            raise EngineError("unknown_run", "Step run is not registered")
        if started and run["status"] != "started":
            raise EngineError("run_closed", "Step run has already ended")
        return run

    def _step_start_view(self, root, state, run, inputs, definition):
        """Return useful exact small bindings without injecting a huge run log."""
        full = {"run_id": run["run_id"], "run": run, "inputs": inputs,
                "parameters": run["parameters"], "environment": run["environment"], "step": definition}
        def size(value):
            return len(json.dumps(self._result(root, state, **value), ensure_ascii=False,
                                  separators=(",", ":"), allow_nan=False).encode())
        if size(full) <= 24 * 1024 - 512:
            return copy.deepcopy(full)
        result = {"run_id": run["run_id"],
                  "run": {key: run[key] for key in ("run_id", "step_id", "status", "signature",
                             "step_digest", "workflow_revision", "generation", "dependency_coverage")},
                  "inputs": {}, "parameters": {}, "environment": {},
                  "projection": {"detail": "compact", "complete": False, "budget_bytes": 24 * 1024,
                    "original_bytes": size(full),
                    "omitted": {"inputs": len(inputs), "parameters": len(run["parameters"]),
                                "environment": len(run["environment"]), "step": True,
                                "full_run": True},
                    "read": {"tool": "get_context", "arguments": {"task_id": state["task_id"], "detail": "full"},
                             "run_id": run["run_id"], "step_id": run["step_id"]}}}
        for section, values in (("inputs", inputs), ("parameters", run["parameters"]), ("environment", run["environment"])):
            for key, value in values.items():
                result[section][key] = copy.deepcopy(value)
                if size(result) > 24 * 1024 - 512:
                    del result[section][key]
                    continue
                result["projection"]["omitted"][section] -= 1
        result["step"] = copy.deepcopy(definition)
        if size(result) > 24 * 1024 - 512:
            del result["step"]
        else:
            result["projection"]["omitted"]["step"] = False
        return result

    def begin_step_run(self, task_id, operation_id, expected_revision, step_id,
                       plan_digest=None, dependency_coverage="unknown", target_ids=None):
        step_id = _bounded(step_id, "step_id", 96)
        if dependency_coverage not in ("declared_complete", "partial", "unknown"):
            raise EngineError("invalid_input", "Unknown dependency coverage")
        if plan_digest is not None:
            plan_digest = _bounded(plan_digest, "plan_digest", 64)
        payload = {"method": "begin_step_run", "step_id": step_id, "plan_digest": plan_digest,
                   "dependency_coverage": dependency_coverage}
        if target_ids is not None:
            payload["target_ids"] = target_ids

        def change(root, state):
            workflow = self._require_workflow(state)
            if state["status"] != "active":
                raise EngineError("task_not_active", "Step execution requires an active task")
            if any(run["step_id"] == step_id and run["status"] == "started" for run in state["runs"]):
                raise EngineError("step_running", "Resolve the existing step run before starting another")
            plan, observations = self._workflow_plan(root, state, target_ids)
            if plan_digest is not None and plan["plan_digest"] != plan_digest:
                raise EngineError("plan_changed", "Inputs or workflow changed after the inspected plan")
            card = plan["steps"].get(step_id)
            if card is None:
                raise EngineError("unknown_step", "Step is not in the selected target graph")
            if card["waiting_on"] or card["state"] == "blocked" or not card.get("signature"):
                raise EngineError("step_not_ready", "Step inputs or prerequisites are not ready", {"step": _assessment_summary(card)})
            definition = workflow["definition"]["steps"][step_id]
            run_id = str(uuid.uuid4())
            resolved = copy.deepcopy(card["resolved_inputs"])
            actions, exposed = [], {}
            for index, (port, item) in enumerate(sorted(resolved.items())):
                if item.get("status") == "absent" and item.get("optional"):
                    exposed[port] = {"ref": None, "path": None, "optional": True}
                    continue
                observation = observations.get(item.get("ref"), {})
                if observation.get("status") != "available":
                    raise EngineError("step_not_ready", "A declared input is unavailable")
                source = Path(observation["path"])
                suffix = source.suffix[:40]
                basename = f"{index:03d}-{port}{suffix}"
                staging = f".workspace/staging/{run_id}-inputs/{basename}"
                target = f".workspace/run-inputs/{run_id}/{basename}"
                # Capture before journaling. Recovery never has to re-read a
                # mutable original; unreferenced staging is retained on failure.
                capture = self._managed(root, staging)
                publish_copy(source, capture, observation["sha256"])
                capture.chmod(0o400)
                actions.append({"source": staging, "target": target,
                                "sha256": observation["sha256"], "read_only": True})
                item["snapshot_path"] = target
                item.pop("path", None)
                exposed[port] = {"ref": item["ref"], "sha256": item["sha256"],
                                 "size": item.get("size"), "path": str(root / target),
                                 "snapshot_path": str(root / target)}
            # A changing original is never silently promoted to the observed
            # version just because its old bytes were captured successfully.
            fresh_plan, _ = self._workflow_plan(root, state, target_ids)
            if fresh_plan["steps"][step_id].get("signature") != card["signature"] or any(
                entry.get("status") not in ("available", "absent")
                for entry in fresh_plan["steps"][step_id]["resolved_inputs"].values()):
                raise EngineError("inputs_changed", "Inputs changed while the execution snapshot was captured")
            run = {"run_id": run_id, "step_id": step_id, "step_digest": step_digest(workflow["definition"], step_id),
                   "workflow_revision": workflow["revision"], "generation": workflow["generation"],
                   "signature": card["signature"], "resolved_inputs": resolved,
                   "parameters": {k: workflow["parameters"][k] for k in definition["parameters"] if k in workflow["parameters"]},
                   "environment": {k: workflow["environment"][k] for k in definition["environment"] if k in workflow["environment"]},
                   "outputs": {}, "checks": {}, "status": "started", "started_at": _now(),
                   "dependency_coverage": dependency_coverage, "claim_origin": "caller_reported"}
            state["runs"].append(run)
            return self._step_start_view(root, state, run, exposed, definition), actions

        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def _normalize_run_checks(self, root, state, definition, checks, outputs):
        if checks is None:
            checks = {}
        if not isinstance(checks, dict) or len(checks) > 128:
            raise EngineError("invalid_input", "Run checks must be a bounded object")
        result = {}
        for check_id, value in checks.items():
            _bounded(check_id, "check_id", 96)
            if not isinstance(value, dict) or set(value) - {"status", "evidence_ref", "note"}:
                raise EngineError("invalid_input", "Run check fields are invalid")
            if value.get("status") not in ("passed", "failed", "unknown"):
                raise EngineError("invalid_input", "Run check status is invalid")
            item = {"status": value["status"], "claim_origin": "caller_reported",
                    "outputs_digest": digest(outputs)}
            if value.get("note") is not None:
                item["note"] = _bounded(value["note"], "check.note", 4000)
            if value.get("evidence_ref") is not None or value["status"] == "passed":
                artifact = self._artifact(state, value.get("evidence_ref"))
                self._assert_artifact(root, state, artifact)
                item["evidence_ref"] = _artifact_ref(artifact)
            result[check_id] = item
        missing = [key for key in definition["required_checks"]
                   if result.get(key, {}).get("status") != "passed"]
        if missing:
            raise EngineError("checks_pending", "Required step checks have not passed", {"checks": missing})
        return result

    def finish_step_run(self, task_id, operation_id, expected_revision, run_id,
                        outcome="succeeded", checks=None, evidence_ref=None):
        run_id = _bounded(run_id, "run_id", 240)
        if outcome not in ("succeeded", "failed", "interrupted"):
            raise EngineError("invalid_input", "Run outcome must be succeeded, failed or interrupted")
        if evidence_ref is not None:
            evidence_ref = _bounded(evidence_ref, "evidence_ref", 2000)
        payload = {"method": "finish_step_run", "run_id": run_id, "outcome": outcome,
                   "checks": checks, "evidence_ref": evidence_ref}

        def change(root, state):
            workflow = self._require_workflow(state)
            run = self._run(state, run_id, started=True)
            if state["status"] != "active":
                raise EngineError("task_not_active", "Resolve step execution while the task is active")
            status, reasons = outcome, []
            if outcome == "succeeded":
                definition = workflow["definition"]["steps"].get(run["step_id"])
                if definition is None or step_digest(workflow["definition"], run["step_id"]) != run["step_digest"]:
                    status, reasons = "stale", [{"code": "definition_changed"}]
                else:
                    required = set(definition["outputs"])
                    if set(run["outputs"]) != required:
                        raise EngineError("outputs_pending", "Every declared output must be committed",
                                          {"missing": sorted(required - set(run["outputs"]))})
                    for ref in run["outputs"].values():
                        self._assert_artifact(root, state, self._artifact(state, ref))
                    if any(item.get("run_id") == run_id and item["status"] == "reserved"
                           for item in state["reservations"].values()):
                        raise EngineError("outputs_pending", "Step has an unfinished output reservation")
                    checked = self._normalize_run_checks(root, state, definition, checks, run["outputs"])
                    current, observations = self._workflow_plan(root, state)
                    card = current["steps"].get(run["step_id"])
                    if card is None or card.get("signature") != run["signature"] or card.get("waiting_on"):
                        status, reasons = "stale", [{"code": "inputs_or_prerequisites_changed"}]
                    elif any(value.get("status") not in ("available", "absent")
                             for value in card["resolved_inputs"].values()):
                        status, reasons = "stale", [{"code": "input_unavailable"}]
                    else:
                        for item in run["resolved_inputs"].values():
                            if item.get("snapshot_path"):
                                snapshot = self._managed(root, item["snapshot_path"], exists=True)
                                if file_info(snapshot)["sha256"] != item["sha256"]:
                                    raise EngineError("snapshot_changed", "Execution input snapshot was modified")
                        run["checks"] = checked
            run["status"], run["ended_at"] = status, _now()
            if evidence_ref is not None:
                run["execution_evidence_ref"] = evidence_ref
            if reasons:
                run["terminal_reasons"] = reasons
            if status == "succeeded":
                state["active_outputs"][run["step_id"]] = {"run_id": run_id, "outputs": copy.deepcopy(run["outputs"])}
                self._invalidate_workflow_criteria(state, run["step_id"])
            else:
                for reservation in state["reservations"].values():
                    if reservation.get("run_id") == run_id and reservation["status"] == "reserved":
                        reservation["status"] = "abandoned"
                        reservation["abandoned_at"] = _now()
                        reservation["abandon_reason"] = f"Step run ended as {status}"
            return {"run_id": run_id, "status": status, "activated": status == "succeeded",
                    "outputs": copy.deepcopy(run["outputs"]), "reasons": reasons,
                    "partial_files_preserved": status != "succeeded"}, []

        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def _workflow_reservation(self, state, run_id, output_slot, name, role, format):
        if run_id is None and output_slot is None:
            return None
        if not run_id or not output_slot:
            raise EngineError("invalid_input", "Step outputs require both run_id and output_slot")
        workflow = self._require_workflow(state)
        run = self._run(state, run_id, started=True)
        definition = workflow["definition"]["steps"].get(run["step_id"])
        if definition is None or step_digest(workflow["definition"], run["step_id"]) != run["step_digest"]:
            raise EngineError("definition_changed", "Step definition changed during its run")
        expected = definition["outputs"].get(output_slot)
        if expected is None:
            raise EngineError("unknown_output", "Output slot is not declared by the step")
        if (name, role, format) != (expected["logical_name"], expected["role"], expected.get("format")):
            raise EngineError("output_contract", "Reservation does not match the declared output",
                              {"expected": expected})
        if output_slot in run["outputs"] or any(r.get("run_id") == run_id and r.get("output_slot") == output_slot
                                               and r["status"] != "abandoned" for r in state["reservations"].values()):
            raise EngineError("output_reserved", "This run already owns an output for the slot")
        return run

    def _workflow_criterion_context(self, state, criterion):
        if state.get("schema_version") != 2:
            return None
        definition = state["workflow"]["definition"]
        targets = [key for key, target in definition["targets"].items() if criterion in target["required_criteria"]]
        if not targets:
            return None
        selected = {}
        for step_id in sorted(dependency_closure(definition, targets)):
            active = state["active_outputs"].get(step_id)
            if not active:
                raise EngineError("workflow_pending", "Target steps must finish before their criterion can pass")
            selected[step_id] = copy.deepcopy(active)
        return {"target_ids": targets, "digest": digest({"targets": targets, "selected": selected,
                    "definition": {key: step_digest(definition, key) for key in selected},
                    "input_bindings": state["input_bindings"],
                    "parameters": state["workflow"]["parameters"], "environment": state["workflow"]["environment"]})}

    def _workflow_criterion_artifact(self, state, criterion, evidence_ref):
        if state.get("schema_version") != 2:
            return
        definition = state["workflow"]["definition"]
        for target_id, target in definition["targets"].items():
            if criterion not in target["required_criteria"]:
                continue
            allowed = {ref for step_id in dependency_closure(definition, [target_id])
                       for ref in state["active_outputs"].get(step_id, {}).get("outputs", {}).values()}
            if evidence_ref not in allowed:
                raise EngineError("invalid_evidence", "Criterion evidence must belong to each declared target's dependency graph",
                                  {"criterion": criterion, "target_id": target_id})

    def _workflow_complete(self, root, state):
        workflow = self._require_workflow(state)
        definition = workflow["definition"]
        if any(run["status"] == "started" for run in state["runs"]):
            raise EngineError("run_pending", "Unresolved step runs remain")
        observations = self._workflow_observations(root, state)
        closure = dependency_closure(definition)
        run_ids = []
        for step_id in sorted(closure):
            active = state["active_outputs"].get(step_id)
            if active is None:
                raise EngineError("workflow_pending", "A target dependency has no selected output", {"step_id": step_id})
            run = self._run(state, active["run_id"])
            if run["outputs"] != active["outputs"]:
                raise EngineError("invalid_state", "Active output bundle does not match its run")
            resolved = planner.resolve_step_inputs(definition, step_id, state["input_bindings"],
                                                   state["active_outputs"], observations.get)
            card = planner.assess_run(definition, step_id, self._observed_run(root, run), resolved, workflow["parameters"],
                                      workflow["environment"], observations, _now(), generation=workflow["generation"])
            if card["state"] != "reusable":
                raise EngineError("workflow_stale", "Current targets are not valid for the selected inputs",
                                  {"step_id": step_id, "assessment": _assessment_summary(card)})
            run_ids.append(run["run_id"])
        outputs = {}
        for target_id, target in definition["targets"].items():
            outputs[target_id] = []
            for declaration in target["outputs"]:
                ref = state["active_outputs"][declaration["step"]]["outputs"][declaration["output"]]
                artifact = self._artifact(state, ref)
                self._assert_artifact(root, state, artifact)
                outputs[target_id].append({**declaration, "ref": ref, "sha256": artifact["sha256"]})
            for criterion in target["required_criteria"]:
                result = next((item for item in state["criteria_results"] if item["criterion"] == criterion), None)
                if not result or result["status"] != "passed" or result.get("workflow_context") != self._workflow_criterion_context(state, criterion):
                    raise EngineError("criteria_pending", "Target criterion needs a current workflow evaluation", {"criterion": criterion})
                self._workflow_criterion_artifact(state, criterion, result.get("evidence_ref"))
        state["current_deliverable_set"] = {"target_ids": sorted(definition["targets"]), "outputs": outputs,
            "run_ids": run_ids, "workflow_digest": workflow["digest"],
            "input_digest": digest(state["input_bindings"]), "recorded_at": _now()}

    def _recipe_artifact(self, recipe_task_id, recipe_artifact_id, recipe_artifact_revision):
        from .recipes import normalize_recipe
        self._validate_recipe_reference(recipe_task_id, recipe_artifact_id, recipe_artifact_revision)
        root = self._path(recipe_task_id)
        state = self._load(root, recipe_task_id)
        artifact = self._artifact(state, recipe_artifact_id, recipe_artifact_revision)
        path = self._assert_artifact(root, state, artifact)
        if artifact["size"] > 1024 * 1024:
            raise EngineError("invalid_recipe", "Recipe manifest exceeds one MiB")
        try:
            data = path.read_bytes()
            if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
                raise EngineError("artifact_changed", "Recipe bytes changed while being read")
            raw = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeError, OSError) as exc:
            if isinstance(exc, EngineError):
                raise
            raise EngineError("invalid_recipe", "Recipe manifest must be valid UTF-8 JSON") from exc
        recipe = normalize_recipe(raw, criteria_normalizer=self._criteria)
        return recipe, artifact

    def _validate_recipe_reference(self, recipe_task_id, recipe_artifact_id, recipe_artifact_revision):
        for name, value in (("recipe_task_id", recipe_task_id), ("recipe_artifact_id", recipe_artifact_id)):
            try:
                if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError) as exc:
                raise EngineError("invalid_input", f"{name} must be a canonical UUID, not an evidence reference") from exc
        if type(recipe_artifact_revision) is not int or recipe_artifact_revision < 1:
            raise EngineError("invalid_input", "Recipe artifact revision must be a positive integer")

    def _recipe_evidence(self, recipe):
        cases = []
        candidates = []
        if recipe.get("origin"):
            candidates.append({"kind": "origin", **recipe["origin"]})
        candidates.extend({"kind": "qualification", **item} for item in recipe.get("qualification", []))
        for item in candidates:
            case = {"kind": item["kind"], "task_id": item.get("task_id"),
                    "case_id": item.get("case_id"), "status": "unverified"}
            try:
                root = self._path(item["task_id"])
                state = self._load(root, item["task_id"])
                workflow = self._require_workflow(state)
                expected = item.get("workflow_digest")
                if expected != workflow_digest(recipe["workflow"]):
                    raise EngineError("qualification_mismatch", "Case does not identify this workflow")
                definitions = [workflow, *state["workflow_history"]]
                if not any(value["digest"] == expected and
                           ("workflow_revision" not in item or value["revision"] == item["workflow_revision"])
                           for value in definitions):
                    raise EngineError("qualification_mismatch", "Case workflow is not recorded by its task")
                selected = [self._run(state, run_id) for run_id in item.get("run_ids", [])]
                needed = dependency_closure(recipe["workflow"])
                by_step = {run["step_id"]: run for run in selected}
                if len(by_step) != len(selected) or set(by_step) != needed:
                    raise EngineError("qualification_incomplete", "Case does not contain a successful target closure")
                selected_refs = {ref for run in selected for ref in run["outputs"].values()}
                for run in selected:
                    if run["status"] != "succeeded" or run["step_digest"] != step_digest(recipe["workflow"], run["step_id"]):
                        raise EngineError("qualification_failed", "Case contains failed or mismatched runs")
                    step = recipe["workflow"]["steps"][run["step_id"]]
                    if set(run["outputs"]) != set(step["outputs"]):
                        raise EngineError("qualification_incomplete", "Case is missing declared outputs")
                    if run["dependency_coverage"] != "declared_complete":
                        raise EngineError("qualification_incomplete", "Case dependency coverage is incomplete")
                    if planner.execution_signature(recipe["workflow"], run["step_id"], run["resolved_inputs"],
                                                   run["parameters"], run["environment"]) != run["signature"]:
                        raise EngineError("qualification_mismatch", "Run signature does not match its recorded inputs")
                    for port, declaration in step["inputs"].items():
                        resolved = run["resolved_inputs"].get(port, {})
                        if "step" in declaration:
                            upstream = by_step[declaration["step"]]
                            if resolved.get("upstream_run_id") != upstream["run_id"] or resolved.get("ref") != upstream["outputs"][declaration["output"]]:
                                raise EngineError("qualification_mismatch", "Case combines different execution branches")
                        if resolved.get("status") == "absent" and resolved.get("optional"):
                            continue
                        snapshot = self._managed(root, resolved.get("snapshot_path"), exists=True)
                        if file_info(snapshot)["sha256"] != resolved.get("sha256"):
                            raise EngineError("qualification_mismatch", "Historical execution snapshot changed")
                    for ref in run["outputs"].values():
                        self._assert_artifact(root, state, self._artifact(state, ref))
                    for key in step["required_checks"]:
                        check = run["checks"].get(key, {})
                        if check.get("status") != "passed" or check.get("outputs_digest") != digest(run["outputs"]):
                            raise EngineError("qualification_incomplete", "Required historical checks are absent")
                        self._assert_artifact(root, state, self._artifact(state, check["evidence_ref"]))
                case["status"] = "outputs_verified"
                # The same criterion ID is not the same acceptance contract.
                # Stronger human/independent review requirements need new cases.
                recipe_criteria = {value["id"]: value for value in self._criteria(recipe["completion_criteria"])}
                source_criteria = {value["id"]: value for value in state["completion_criteria"]}
                if recipe_criteria != source_criteria:
                    case["reason"] = "qualification_criteria_changed"
                    cases.append(case)
                    continue
                frames = [{"current_deliverable_set": state.get("current_deliverable_set"),
                           "criteria_results": state["criteria_results"]}, *state.get("completion_history", [])]
                for frame in frames:
                    bundle = frame.get("current_deliverable_set")
                    if not bundle or bundle.get("workflow_digest") != expected or set(bundle.get("run_ids", [])) != {run["run_id"] for run in selected}:
                        continue
                    results = {result["criterion"]: result for result in frame["criteria_results"]}
                    valid = True
                    for key, criterion in recipe_criteria.items():
                        result = results.get(key, {})
                        if result.get("status") != "passed":
                            valid = False
                            continue
                        artifact = self._artifact(state, result.get("evidence_ref"))
                        self._assert_artifact(root, state, artifact)
                        self._review(result.get("review"), required=bool(criterion.get("requires_independent_review")))
                        if criterion["requires_approval"]:
                            approvals = [approval for approval in state["approvals"]
                                if approval["artifact_id"] == artifact["artifact_id"]
                                and approval["artifact_revision"] == artifact["revision"]
                                and approval["sha256"] == artifact["sha256"]
                                and (not criterion.get("approval_scope") or approval["scope"] == criterion["approval_scope"])]
                            if not approvals:
                                valid = False
                            for approval in approvals:
                                snapshot = self._managed(root, approval["snapshot_path"], exists=True)
                                if file_info(snapshot)["sha256"] != approval["sha256"]:
                                    valid = False
                    for target_id, target in recipe["workflow"]["targets"].items():
                        allowed_refs = {ref for step_id in dependency_closure(recipe["workflow"], [target_id])
                                        for ref in by_step[step_id]["outputs"].values()}
                        for key in target["required_criteria"]:
                            result = results.get(key, {})
                            if result.get("status") != "passed" or result.get("evidence_ref") not in allowed_refs or not result.get("workflow_context"):
                                valid = False
                    if valid:
                        case["status"] = "example_verified"
                        break
            except (EngineError, KeyError, OSError) as exc:
                case["reason"] = getattr(exc, "code", "evidence_unavailable")
            cases.append(case)
        return {"status": "example_tested" if cases and all(case["status"] == "example_verified" for case in cases) else "unverified",
                "cases": cases, "meaning": "Verified historical execution cases; not general quality, new-task approval or current input validity"}

    def check_recipe(self, recipe_task_id, recipe_artifact_id, recipe_artifact_revision, detail="compact"):
        from .projection import compact_recipe_check
        recipe, artifact = self._recipe_artifact(recipe_task_id, recipe_artifact_id, recipe_artifact_revision)
        from .recipes import check_recipe_bindings
        checked = check_recipe_bindings(recipe, input_slots=set(), parameters={}, capabilities=self.capabilities())
        full = {"schema_valid": True, "recipe_id": recipe["recipe_id"], "revision": recipe["revision"],
                "sha256": artifact["sha256"], "semantic_digest": digest(recipe),
                "recipe_task_id": recipe_task_id, "recipe_artifact_id": recipe_artifact_id,
                "recipe_artifact_revision": recipe_artifact_revision,
                "required_inputs": recipe["workflow"]["inputs"], "parameters": recipe["workflow"]["parameters"],
                "supported": checked.get("supported"), "requirements": checked.get("requirements"),
                "qualification": self._recipe_evidence(recipe), "execution_authorized": False}
        return compact_recipe_check(full, detail=detail)

    def begin_from_recipe(self, start_request_id, recipe_task_id, recipe_artifact_id,
                          recipe_artifact_revision, input_bindings, parameters, title, goal,
                          environment=None, workspace_root=None, source_root=None, binding=None):
        # Normalize only request identity before looking up the receipt. Existing
        # receipts restore the original seed even if a source later moved.
        from .engine import binding_value, filename, identifier, text
        from .recipes import bind_recipe_parameters, check_recipe_bindings
        request_id = identifier(start_request_id, "start_request_id")
        self._validate_recipe_reference(recipe_task_id, recipe_artifact_id, recipe_artifact_revision)
        title, goal = text(title, "title", 300), text(goal, "goal")
        binding = binding_value(binding)
        if not isinstance(input_bindings, dict) or len(input_bindings) > 128:
            raise EngineError("invalid_input", "Recipe input bindings must be a bounded object")
        declared = {}
        for slot, item in input_bindings.items():
            _bounded(slot, "input slot", 96)
            if not isinstance(item, dict) or set(item) != {"path"}:
                raise EngineError("invalid_input", "Recipe input binding requires only an explicit path")
            value = _bounded(item["path"], "input path", 4096)
            if not Path(value).expanduser().is_absolute():
                raise EngineError("invalid_input", "Recipe input paths must be absolute")
            declared[slot] = {"path": str(safe_path(value, self.allowed_source_roots))}
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict) or not isinstance({} if environment is None else environment, dict):
            raise EngineError("invalid_input", "Recipe parameter/environment bindings must be objects")
        # canonical digest will be made by the creation layer; reject NaN before
        # it can become an accepted non-JSON operation identity.
        try:
            json.dumps([parameters, environment], allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise EngineError("invalid_input", "Bindings must contain finite JSON values") from exc
        explicit = safe_path(workspace_root, self.allowed_workspace_roots) if workspace_root else None
        source = safe_path(source_root, self.allowed_source_roots) if source_root else None
        if explicit and (explicit in (self.workspace_home, self.state_home) or self.state_home in explicit.parents):
            raise EngineError("unsafe_root", "A container/state root cannot become a task")
        if type(recipe_artifact_revision) is not int or recipe_artifact_revision < 1:
            raise EngineError("invalid_input", "Recipe artifact revision must be a positive integer")
        payload = {"method": "begin_from_recipe", "title": title, "goal": goal, "kind": "recipe",
                   "criteria": [], "workspace_root": str(explicit) if explicit else None,
                   "source_root": str(source) if source else None, "binding": binding,
                   "recipe_task_id": recipe_task_id, "recipe_artifact_id": recipe_artifact_id,
                   "recipe_artifact_revision": recipe_artifact_revision,
                   "input_bindings": declared, "parameters": copy.deepcopy(parameters),
                   "environment": copy.deepcopy({} if environment is None else environment)}

        def seed(initial):
            recipe, artifact = self._recipe_artifact(recipe_task_id, recipe_artifact_id, recipe_artifact_revision)
            values = bind_recipe_parameters(recipe, parameters)
            checked = check_recipe_bindings(recipe, input_slots=set(declared), parameters=values, capabilities=self.capabilities())
            if not checked.get("bindings_valid") or checked.get("supported") is not True:
                raise EngineError("recipe_bindings", "Recipe inputs or capabilities are incomplete",
                                  {key: value for key, value in checked.items() if key != "parameters"})
            if source is not None and (not source.exists() or not source.is_dir()):
                raise EngineError("invalid_source_root", "Bound source root must exist")
            initial["completion_criteria"] = self._criteria(recipe["completion_criteria"])
            initial.update(self._workflow_state(recipe["workflow"], values, environment))
            for slot, item in sorted(declared.items()):
                path = self._source(item["path"])
                info = file_info(path)
                source_id = str(uuid.uuid4())
                initial["sources"].append({"source_id": source_id, "origin_path": str(path),
                    "mode": "reference", "recorded_at": initial["created_at"], **info})
                initial["input_bindings"][slot] = {"ref": f"source:{source_id}", "mode": "follow",
                    "binding_revision": 1, "bound_at": initial["created_at"]}
            initial["recipe_binding"] = {"recipe_id": recipe["recipe_id"], "revision": recipe["revision"],
                "sha256": artifact["sha256"], "semantic_digest": digest(recipe),
                "origin_task_id": recipe_task_id, "artifact_ref": _artifact_ref(artifact),
                "manifest": copy.deepcopy(recipe)}
            self._validate_target_criteria(initial, recipe["workflow"])
            return initial

        return self._create_task(request_id, payload, seed_factory=seed)
