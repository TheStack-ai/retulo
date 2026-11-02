"""Bounded, read-only views over the authoritative task record.

These helpers never rewrite, compact or delete the ledger. A preview is marked
as such and carries a route back to the full record. Counted evidence statuses
are reported claims, not a new content or approval judgement.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from .fs import EngineError


MAX_RESPONSE_BYTES = 24576
SECTIONS = (
    "completion_criteria", "criteria_results", "criteria_history", "artifacts",
    "sources", "decisions", "approvals", "bindings", "operations", "reservations",
    "workflow", "workflow_history", "input_bindings", "runs", "active_outputs",
    "current_deliverable_set", "recipe_binding", "steps", "targets",
)
_MAPPING_SECTIONS = frozenset({"operations", "reservations", "input_bindings", "active_outputs"})
_SINGLETON_SECTIONS = frozenset({"workflow", "current_deliverable_set", "recipe_binding"})
_WORKFLOW_SECTIONS = frozenset({"steps", "targets"})
_RUN_STATUSES = frozenset({"started", "succeeded", "failed", "interrupted", "unresolved"})


def _section_values(state, section):
    """Normalize optional v2 records without changing their authoritative form.

    A workflow/recipe/result bundle is one record, not a dictionary of unrelated
    scalar fields. Steps and targets are virtual map sections so even a large
    workflow can be inspected one exact definition at a time.
    """
    if section in _WORKFLOW_SECTIONS:
        workflow = state.get("workflow")
        if workflow is None:
            return {}
        if not isinstance(workflow, dict) or not isinstance(workflow.get("definition"), dict):
            raise EngineError("invalid_state", "Workflow projection requires a definition object")
        values = workflow["definition"].get(section, {})
        if not isinstance(values, dict):
            raise EngineError("invalid_state", f"Workflow {section} must be an object")
        return values
    if section in _SINGLETON_SECTIONS:
        value = state.get(section)
        if value is None:
            return []
        if not isinstance(value, dict):
            raise EngineError("invalid_state", f"{section} must be an object or null")
        return [value]
    if section in _MAPPING_SECTIONS:
        values = state.get(section, {})
        if not isinstance(values, dict):
            raise EngineError("invalid_state", f"{section} must be an object")
        return values
    values = state.get(section, [])
    if not isinstance(values, list):
        raise EngineError("invalid_state", f"{section} must be an array")
    return values


def _scalar_fields(record, names, text_bytes=160):
    """Select bounded metadata only; never copy a nested workflow by accident."""
    selected = {name: value for name in names if name in record
                and (value := record[name]) is not None
                and isinstance(value, (str, int, float, bool))}
    return _fields(selected, tuple(selected), text_bytes)


def _workflow_summaries(state, sections):
    """Static ledger summaries, deliberately independent of filesystem inspection."""
    workflow = state.get("workflow")
    if workflow is None:
        return {}
    definition = workflow["definition"]  # validated by the virtual sections above
    for field in ("inputs", "parameters", "environment"):
        if not isinstance(definition.get(field, {}), dict):
            raise EngineError("invalid_state", f"Workflow {field} must be an object")
    summary = _scalar_fields(workflow, ("revision", "generation", "digest"))
    summary.update(
        input_count=len(definition.get("inputs", {})),
        parameter_count=len(definition.get("parameters", {})),
        environment_count=len(definition.get("environment", {})),
        step_count=len(sections["steps"]), target_count=len(sections["targets"]),
        bound_input_count=len(sections["input_bindings"]),
        active_step_count=len(sections["active_outputs"]),
        freshness="not_inspected",
        read={"tool": "plan_rebuild", "arguments": {
            "task_id": _clip(state["task_id"], 240)[0] if isinstance(state.get("task_id"), str) else None}},
    )
    statuses = {}
    for run in sections["runs"]:
        status = run.get("status", "unknown") if isinstance(run, dict) else "unknown"
        if not isinstance(status, str) or status not in _RUN_STATUSES:
            status = "unknown"
        statuses[status] = statuses.get(status, 0) + 1
    result = {
        "workflow_summary": summary,
        "run_summary": {"total": len(sections["runs"]), "statuses": statuses,
                        "meaning": "Historical execution results; not current reusability or approval."},
    }
    deliverable = state.get("current_deliverable_set")
    result["deliverable_summary"] = {"recorded": deliverable is not None, "freshness": "not_inspected"}
    if deliverable is not None:
        result["deliverable_summary"].update(_scalar_fields(deliverable, (
            "revision", "workflow_revision", "workflow_digest", "input_digest", "digest", "recorded_at", "promoted_at")))
        outputs = deliverable.get("outputs")
        if isinstance(outputs, dict):
            result["deliverable_summary"]["target_count"] = len(outputs)
            if all(isinstance(value, list) for value in outputs.values()):
                result["deliverable_summary"]["output_count"] = sum(len(value) for value in outputs.values())
        if isinstance(deliverable.get("run_ids"), list):
            result["deliverable_summary"]["run_count"] = len(deliverable["run_ids"])
    recipe = state.get("recipe_binding")
    if recipe is not None:
        result["recipe_summary"] = _scalar_fields(recipe, (
            "recipe_id", "recipe_revision", "revision", "sha256", "semantic_digest", "digest", "artifact_ref", "origin_task_id"))
        result["recipe_summary"]["meaning"] = "Pinned recipe identity; no inherited verdicts or approvals."
    return result


def _bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8"))


def _clip(value, maximum):
    if not isinstance(value, str):
        return copy.deepcopy(value), False
    # Count JSON's escaped bytes as well as UTF-8 bytes. A legal control
    # character can otherwise expand sixfold when the tool serializes it.
    if _bytes(value) - 2 <= maximum:
        return value, False
    low, high = 0, min(len(value), maximum)
    while low < high:
        middle = (low + high + 1) // 2
        if _bytes(value[:middle] + "…") - 2 <= maximum:
            low = middle
        else:
            high = middle - 1
    return value[:low] + "…", True


def _fields(record, names, text_bytes=240):
    """Select known fields; list every clipped selected field on its own card."""
    result, clipped = {}, []
    for name in names:
        if name not in record:
            continue
        value, changed = _clip(record[name], text_bytes)
        result[name] = value
        if changed:
            clipped.append(name)
    if clipped:
        result["_projection"] = {"truncated_fields": clipped, "original_bytes": _bytes(record)}
    return result


def _identity(context):
    root = context.get("workspace_path")
    result = {name: copy.deepcopy(context[name]) for name in
              ("task_id", "workspace_path", "revision", "state_path", "guide_path") if name in context}
    if isinstance(root, str):
        result.setdefault("state_path", str(Path(root) / ".workspace" / "task.json"))
        result.setdefault("guide_path", str(Path(root) / "WORKSPACE.md"))
    return result


def _recovery(context):
    return {
        "full": {"tool": "get_context", "arguments": {"task_id": context.get("task_id"), "detail": "full"}},
        "records": {"tool": "read_records", "arguments": {
            "task_id": context.get("task_id"), "section": "<section>",
            "offset": 0, "limit": 20, "expected_revision": context.get("revision")}},
        "sections": list(SECTIONS),
    }


def _count_status(items, field="status"):
    counts = {}
    for item in items:
        status = item.get(field, "unknown") if isinstance(item, dict) else "unknown"
        # Only the finite domain labels become keys, never user-supplied text.
        if status not in {"passed", "failed", "pending", "unknown", "proposed", "confirmed",
                          "superseded", "reserved", "committed", "abandoned"}:
            status = "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _finish(result):
    # The size field includes itself; the digit count settles in at most two passes.
    meta = result["projection"]
    for _ in range(4):
        size = _bytes(result)
        if meta.get("response_bytes") == size:
            break
        meta["response_bytes"] = size
    if _bytes(result) > MAX_RESPONSE_BYTES:
        raise EngineError("projection_too_large", "Bounded task view could not fit; read the explicit state file")
    return result


def _fit_identity(result):
    """Valid local paths fit normally; abnormal oversized paths remain explicit previews."""
    truncated = result["projection"].setdefault("truncated_fields", [])
    for name in ("task_id", "workspace_path", "state_path", "guide_path"):
        if name not in result:
            continue
        value, changed = _clip(result[name], 4096 if name != "task_id" else 240)
        if changed:
            result[name] = value
            truncated.append(name)
    # Identity arguments are repeated in recovery instructions. The actual UUID
    # has a fixed size; do not let an invalid synthetic context bypass the bound.
    for entry in result["projection"].get("read", {}).values():
        if isinstance(entry, dict) and isinstance(entry.get("arguments"), dict):
            entry["arguments"]["task_id"] = result.get("task_id")


def compact_context(full_context, *, verification=None):
    """Return current task cards within 24 KiB; all omitted records stay on disk.

    ``verification`` is an optional bounded summary produced by the core's
    actual inspection, not an arbitrary overlay of top-level context fields.
    """
    if verification is not None:
        try:
            valid = isinstance(verification, dict) and _bytes(verification) <= 2048
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise EngineError("invalid_input", "verification must be a JSON object of at most 2048 bytes")
    if "replayed" in full_context and type(full_context["replayed"]) is not bool:
        raise EngineError("invalid_input", "replayed must be a boolean when supplied")
    state = full_context["state"]
    sections = {name: _section_values(state, name) for name in SECTIONS}
    counts = {name: len(value) for name, value in sections.items()}
    counts["completion_history"] = len(state.get("completion_history", []))
    criteria = sections["completion_criteria"]
    results = {r.get("criterion"): r for r in sections["criteria_results"] if isinstance(r, dict)}
    current_results = [results.get(c.get("id"), {"status": "unknown"}) for c in criteria]
    latest = state.get("latest", {})
    latest_artifacts = [a for a in sections["artifacts"]
                        if latest.get(a.get("artifact_id")) == a.get("revision")]
    latest_found = {a.get("artifact_id") for a in latest_artifacts}
    view = _fields(state, ("schema_version", "task_id", "revision", "title", "goal", "kind", "status", "source_root",
                           "mode", "next_action", "next_review_at", "created_at", "updated_at", "completed_at"), 800)
    # Scalar strings can contain arbitrary text; the status labels themselves
    # come from the engine's schema and are never inferred from that text.
    view.update(
        counts=counts,
        criteria_summary={"total": len(criteria), "statuses": _count_status(current_results),
                          "unreviewed": sum(c.get("id") not in results for c in criteria),
                          "claim_origin": "caller_reported; not independent validation"},
        decision_summary=_count_status(sections["decisions"]),
        reservation_summary=_count_status(sections["reservations"].values()),
        latest_summary={"total": len(latest), "resolved": len(latest_found),
                        "missing_records": len(set(latest) - latest_found)},
        approval_summary={"total": counts["approvals"], "latest_does_not_mean_approved": True},
    )
    view.update(_workflow_summaries(state, sections))
    attention = state.get("attention")
    view["attention_status"] = "unknown" if "attention" not in state else ("open" if attention else "clear")
    view["attention"] = (_fields(attention, ("kind", "owner", "reason", "requested_action", "evidence_ref"), 700)
                         if isinstance(attention, dict) else None)
    if state.get("status") in ("paused", "blocked") and not attention:
        view["status_reason"] = "unknown; no structured attention was recorded"
    result = _identity(full_context)
    result.update(state=view, warnings=[], projection={
        "detail": "compact", "original_bytes": _bytes(full_context),
        "budget_bytes": MAX_RESPONSE_BYTES, "response_bytes": 0,
        "complete": False,
        "omitted": {name: {"total": total, "shown": 0, "omitted": total}
                    for name, total in counts.items()},
        "guide": {"included": False, "original_bytes": _bytes(full_context.get("guide"))
                  if full_context.get("guide") is not None else 0},
        "read": _recovery(full_context),
        "meaning": "Current-state preview only. Omitted or truncated details are not absent, passed, approved, or authorized.",
    })
    if verification is not None:
        result["verification"] = copy.deepcopy(verification)
    if "replayed" in full_context:
        result["replayed"] = full_context["replayed"]
    _fit_identity(result)
    # Reserve room for size fields and omission accounting before adding cards.
    safe_limit = MAX_RESPONSE_BYTES - 256
    for warning in full_context.get("warnings", [])[:20]:
        clipped, _ = _clip(warning, 500)
        result["warnings"].append(clipped)
    result["projection"]["warnings_omitted"] = max(0, len(full_context.get("warnings", [])) - len(result["warnings"]))
    if _bytes(result) > safe_limit:
        for path in ("workspace_path", "state_path", "guide_path"):
            if path in result:
                result[path], changed = _clip(result[path], 800)
                if changed and path not in result["projection"]["truncated_fields"]:
                    result["projection"]["truncated_fields"].append(path)
    if _bytes(result) > safe_limit:
        # Warnings remain discoverable and counted; no state is changed to make room.
        result["projection"]["warnings_omitted"] += len(result["warnings"])
        result["warnings"] = []

    # Current blockers are shown before passed criteria, but aggregate counts
    # always include every criterion, including omitted ones.
    ranked_criteria = sorted(enumerate(criteria), key=lambda pair: (
        results.get(pair[1].get("id"), {}).get("status") == "passed", pair[0]))
    criteria_cards = []
    for _, criterion in ranked_criteria:
        card = _fields(criterion, ("id", "text", "basis", "human_review", "requires_approval",
                                    "approval_scope", "requires_independent_review"), 300)
        card["result_status"] = results.get(criterion.get("id"), {}).get("status", "unknown")
        criteria_cards.append(card)
    decisions = sorted(enumerate(sections["decisions"]), key=lambda pair: (
        {"proposed": 0, "confirmed": 1, "superseded": 2}.get(pair[1].get("status"), 3), -pair[0]))
    result_cards = []
    for item in current_results:
        if not item.get("criterion"):
            continue
        card = _fields(item, ("criterion", "status", "evidence_ref", "note", "claim_origin"), 260)
        if isinstance(item.get("review"), dict):
            card["review"] = _fields(item["review"], ("mode", "reviewer_ref", "execution_ref", "provenance"), 180)
        result_cards.append(card)
    groups = (
        ("completion_criteria", criteria_cards, 8, 3600),
        ("artifacts", [_fields(a, ("artifact_id", "revision", "path", "logical_name", "role", "sha256",
                                    "external", "integrity_status"), 320) for a in reversed(latest_artifacts)], 8, 3000),
        ("criteria_results", result_cards, 8, 2200),
        ("decisions", [_fields(d, ("id", "text", "status", "evidence_ref", "supersedes", "provenance"), 260)
                       for _, d in decisions], 6, 2200),
        ("approvals", [_fields(a, ("approval_id", "artifact_id", "artifact_revision", "sha256", "scope",
                                    "snapshot_path", "provenance"), 260) for a in reversed(sections["approvals"])], 4, 1800),
        ("sources", [_fields(s, ("source_id", "mode", "path", "origin_path", "sha256"), 260)
                     for s in sections["sources"]], 4, 1600),
        ("bindings", [_fields(b, ("provider", "conversation_id", "session_id", "actor_id", "label"), 180)
                      for b in sections["bindings"]], 2, 800),
    )
    for name, cards, limit, group_budget in groups:
        view[name] = []
        for card in cards[:limit]:
            view[name].append(card)
            count = len(view[name])
            result["projection"]["omitted"][name].update(shown=count, omitted=counts[name] - count)
            if _bytes(view[name]) > group_budget or _bytes(result) > safe_limit:
                view[name].pop()
                count -= 1
                result["projection"]["omitted"][name].update(shown=count, omitted=counts[name] - count)
                break
    result["projection"]["omitted"]["artifacts"]["selection"] = "latest registered versions only"
    result["projection"]["omitted"]["completion_history"]["read"] = "get_context(detail='full')"
    return _finish(result)


def _preview(value, depth=0):
    """A small JSON-shaped excerpt; callers get explicit incomplete metadata."""
    if isinstance(value, str):
        return _clip(value, 320)[0]
    if isinstance(value, dict):
        if depth >= 2:
            return {"type": "object", "fields": len(value)}
        preferred = ("id", "artifact_id", "revision", "criterion", "status", "task_id", "path", "sha256")
        keys = [key for key in preferred if key in value]
        keys += [key for key in value if key not in keys]
        return {str(_clip(key, 100)[0]): _preview(value[key], depth + 1) for key in keys[:10]}
    if isinstance(value, list):
        if depth >= 2:
            return {"type": "array", "items": len(value)}
        return [_preview(item, depth + 1) for item in value[:5]]
    return copy.deepcopy(value)


def record_page(full_context, section, offset=0, limit=20, expected_revision=None):
    """Read a stable section page; oversized individual records are marked previews.

    Each ``records`` entry has its original offset, optional dictionary key,
    record, original_bytes and complete flag. ``next_offset`` always advances;
    use the full read route for incomplete individual records, not a retry loop.
    """
    if section not in SECTIONS:
        raise EngineError("invalid_section", "Unknown task record section", {"allowed": list(SECTIONS)})
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise EngineError("invalid_input", "offset must be a nonnegative integer and limit must be 1..50")
    revision = full_context["revision"]
    if expected_revision is not None:
        if type(expected_revision) is not int or expected_revision < 1:
            raise EngineError("invalid_input", "expected_revision must be a positive integer")
        if expected_revision != revision:
            raise EngineError("revision_conflict", "Task changed while reading records",
                              {"expected_revision": expected_revision, "current_revision": revision})
    values = _section_values(full_context["state"], section)
    entries = list(sorted(values.items())) if isinstance(values, dict) else [(None, value) for value in values]
    result = _identity(full_context)
    result.update(section=section, offset=offset, limit=limit, total=len(entries), records=[],
                  next_offset=None, complete=False, projection={
                      "detail": "records", "budget_bytes": MAX_RESPONSE_BYTES, "response_bytes": 0,
                      "original_bytes": _bytes(values), "incomplete_records": [],
                      "read": _recovery(full_context),
                      "meaning": "complete means this returned range reached the end with no record previews.",
                  })
    _fit_identity(result)
    if _bytes(result) > MAX_RESPONSE_BYTES - 2048:
        for name in ("workspace_path", "state_path", "guide_path"):
            if name in result:
                result[name], changed = _clip(result[name], 800)
                if changed and name not in result["projection"]["truncated_fields"]:
                    result["projection"]["truncated_fields"].append(name)
    cursor = min(offset, len(entries))
    for index in range(cursor, min(len(entries), offset + limit)):
        key, value = entries[index]
        original_bytes = _bytes(value)
        entry = {"offset": index, "record": copy.deepcopy(value), "original_bytes": original_bytes, "complete": True}
        if key is not None:
            entry["key"] = key
        if _bytes(entry) > 4096:
            entry["record"] = _preview(value)
            entry["complete"] = False
        result["records"].append(entry)
        if _bytes(result) > MAX_RESPONSE_BYTES - 256:
            result["records"].pop()
            if result["records"]:
                break
            # Ensure even the first unusually large preview makes progress.
            entry["record"] = {"type": type(value).__name__, "preview_unavailable": True}
            if key is not None:
                entry["key"], _ = _clip(key, 240)
            entry["complete"] = False
            result["records"].append(entry)
        if not entry["complete"]:
            result["projection"]["incomplete_records"].append(index)
        cursor = index + 1
    result["next_offset"] = cursor if cursor < len(entries) else None
    result["complete"] = result["next_offset"] is None and not result["projection"]["incomplete_records"]
    return _finish(result)


def _plan_card_preview(card):
    """Summarize an oversized card without fabricating empty dependencies.

    Identifiers are either preserved exactly or omitted with explicit metadata.
    In particular, a shortened waiting list must never look like a runnable
    card. Global planner eligibility remains separately counted in ``ready``.
    """
    preview = {}
    for field in ("step_id", "state", "freshness", "signature", "run_id"):
        value = card.get(field)
        if value is None or isinstance(value, str) and _bytes(value) <= 512:
            preview[field] = copy.deepcopy(value)
    reasons = card.get("reasons", [])
    waiting = card.get("waiting_on", [])
    codes = []
    for reason in reasons if isinstance(reasons, list) else []:
        code = reason.get("code") if isinstance(reason, dict) else None
        if isinstance(code, str) and _bytes(code) <= 160 and code not in codes:
            codes.append(code)
        if len(codes) == 6:
            break
    if isinstance(waiting, list) and _bytes(waiting) <= 512:
        preview["waiting_on"] = copy.deepcopy(waiting)
    preview.update(
        reason_count=len(reasons) if isinstance(reasons, list) else None,
        reason_codes=codes,
        waiting_on_count=len(waiting) if isinstance(waiting, list) else None,
        resolved_input_count=len(card.get("resolved_inputs", {}))
        if isinstance(card.get("resolved_inputs"), dict) else None,
    )
    preview["_projection"] = {
        "complete": False, "original_bytes": _bytes(card), "actionable": False,
        "omitted_fields": [field for field in card if field not in preview],
        "meaning": "Incomplete card. Read the full pinned plan before interpreting details or executing.",
    }
    return preview


def plan_page(full_plan, detail="compact", offset=0, limit=20, expected_plan_digest=None):
    """Project a freshly computed plan; digest pinning precedes pagination.

    Compact output keeps global counts and a global ready preview even when the
    requested topological page has no ready step. ``complete`` describes the
    entire represented plan, while ``next_offset`` controls step-page traversal.
    This function observes no files and never changes the planner's result.
    """
    if not isinstance(detail, str) or detail not in ("compact", "full"):
        raise EngineError("invalid_input", "detail must be compact or full")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise EngineError("invalid_input", "offset must be a nonnegative integer and limit must be 1..50")
    if expected_plan_digest is not None and (not isinstance(expected_plan_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_digest)):
        raise EngineError("invalid_input", "expected_plan_digest must be a SHA-256 digest")
    if not isinstance(full_plan, dict):
        raise EngineError("invalid_state", "Plan must be an object")
    actual_digest = full_plan.get("plan_digest")
    if not isinstance(actual_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", actual_digest):
        raise EngineError("invalid_state", "Plan has no valid content digest")
    if expected_plan_digest is not None and expected_plan_digest != actual_digest:
        raise EngineError("plan_conflict", "Plan changed while reading; inspect the current plan", {
            "expected_plan_digest": expected_plan_digest, "current_plan_digest": actual_digest})
    order, steps = full_plan.get("order"), full_plan.get("steps")
    ready, targets, counts = full_plan.get("ready"), full_plan.get("target_ids"), full_plan.get("counts")
    if (not isinstance(order, list) or not isinstance(steps, dict)
            or any(not isinstance(step, str) or not step for step in order)
            or len(set(order)) != len(order) or set(order) != set(steps)
            or any(not isinstance(card, dict) for card in steps.values())):
        raise EngineError("invalid_state", "Plan order and step records differ")
    if (not isinstance(ready, list) or any(not isinstance(step, str) or step not in steps for step in ready)
            or len(set(ready)) != len(ready) or not isinstance(targets, list)
            or any(not isinstance(target, str) or not target for target in targets)
            or not isinstance(counts, dict)):
        raise EngineError("invalid_state", "Plan summary is invalid")
    original_bytes = _bytes(full_plan)
    total = len(order)
    if detail == "full":
        result = copy.deepcopy(full_plan)
        result.update(total_steps=total, offset=0, limit=total, next_offset=None, complete=True,
                      omitted={"steps": 0, "ready": 0, "target_ids": 0},
                      projection={"detail": "full", "bounded": False, "budget_bytes": None,
                                  "original_bytes": original_bytes, "pagination_applied": False,
                                  "meaning": "Full computed plan; mutations still recheck current inputs and revision."})
        return result

    result = _identity(full_plan)
    # These derived task-file paths are useful in context pages, but the plan's
    # exact digest-pinned recovery uses its task ID and target selection.
    result.pop("state_path", None)
    result.pop("guide_path", None)
    result.update({key: copy.deepcopy(full_plan[key]) for key in
                   ("observed_revision", "workflow_digest", "checked_at", "plan_digest") if key in full_plan})
    recovery = {"tool": "plan_rebuild", "arguments": {
        "task_id": full_plan.get("task_id"), "target_ids": copy.deepcopy(targets),
        "detail": "full", "expected_plan_digest": actual_digest}}
    result.update(
        counts=copy.deepcopy(counts), total_steps=total, offset=offset, limit=limit,
        target_ids=copy.deepcopy(targets[:8]), ready=copy.deepcopy(ready[:8]),
        order=[], steps={}, next_offset=None, complete=False,
        omitted={"steps": total, "ready": max(0, len(ready) - 8), "target_ids": max(0, len(targets) - 8)},
        projection={
            "detail": "compact", "bounded": True, "budget_bytes": MAX_RESPONSE_BYTES,
            "response_bytes": 0, "original_bytes": original_bytes,
            "global_counts": True, "ready_total": len(ready), "target_total": len(targets),
            "incomplete_steps": [], "omitted_step_offsets": [], "omitted_fields": [],
            "read": {"full": recovery},
            "meaning": "Counts and ready eligibility cover the full plan. Order/steps are a page; incomplete cards require a full read. No execution authority is granted.",
        },
    )
    known = set(result) | {"projection"}
    result["projection"]["omitted_fields"] = sorted(set(full_plan) - known)
    safe_limit = MAX_RESPONSE_BYTES - 512
    if _bytes(result) > safe_limit and "workspace_path" in result:
        result.pop("workspace_path")
        result["projection"]["omitted_fields"].append("workspace_path")
    # Retain at least one exact globally ready ID. Do not clip IDs to fit a
    # response: a caller must not accidentally act on a different identifier.
    while _bytes(result) > safe_limit and len(result["target_ids"]) > 0:
        result["target_ids"].pop()
        result["omitted"]["target_ids"] += 1
    while _bytes(result) > safe_limit and len(result["ready"]) > 1:
        result["ready"].pop()
        result["omitted"]["ready"] += 1
    if _bytes(result) > safe_limit:
        raise EngineError("projection_too_large", "Plan identity and pinned recovery cannot fit without truncating IDs; request detail=full")

    cursor = min(offset, total)
    for index in range(cursor, min(total, offset + limit)):
        step_id = order[index]
        original = steps[step_id]
        card = copy.deepcopy(original) if _bytes(original) <= 4096 else _plan_card_preview(original)
        incomplete = "_projection" in card and card["_projection"].get("complete") is False
        result["order"].append(step_id)
        result["steps"][step_id] = card
        if incomplete:
            result["projection"]["incomplete_steps"].append(step_id)
        if _bytes(result) > safe_limit:
            result["order"].pop()
            result["steps"].pop(step_id)
            if incomplete:
                result["projection"]["incomplete_steps"].pop()
            if result["order"]:
                break
            # One bounded card must make progress even when its full detail is
            # individually legal but too large for the remaining page budget.
            card = _plan_card_preview(original)
            result["order"].append(step_id)
            result["steps"][step_id] = card
            result["projection"]["incomplete_steps"].append(step_id)
            if _bytes(result) > safe_limit:
                result["order"].pop()
                result["steps"].pop(step_id)
                result["projection"]["incomplete_steps"].pop()
                result["projection"]["omitted_step_offsets"].append(index)
        cursor = index + 1
    result["next_offset"] = cursor if cursor < total else None
    result["omitted"]["steps"] = total - len(result["steps"])
    result["projection"]["range"] = {"start": min(offset, total), "stop": cursor}
    result["complete"] = (offset == 0 and result["next_offset"] is None
        and not any(result["omitted"].values()) and not result["projection"]["incomplete_steps"]
        and not result["projection"]["omitted_step_offsets"] and not result["projection"]["omitted_fields"])
    return _finish(result)


def _recipe_card(record, fields):
    """Keep small scalar fields exact; omitted identifiers are never shortened."""
    card = {}
    for name in fields:
        if name not in record:
            continue
        value = record[name]
        if value is None or type(value) in (str, int, float, bool):
            if _bytes(value) <= 256:
                card[name] = copy.deepcopy(value)
    omitted = [name for name in record if name not in card]
    if omitted:
        card["_projection"] = {"complete": False, "omitted_fields": omitted,
                               "meaning": "Definition preview; read the full recipe check for exact values."}
    return card


def compact_recipe_check(full_result, detail="compact"):
    """Bound a recipe check without confusing structural or example validation.

    The manifest remains pinned by its original managed artifact reference.
    Compact input/parameter cards deliberately omit descriptions, defaults and
    enum values. They are discovery metadata, not a replacement for the full
    contract when binding a recipe.
    """
    if not isinstance(detail, str) or detail not in ("compact", "full"):
        raise EngineError("invalid_input", "detail must be compact or full")
    if not isinstance(full_result, dict):
        raise EngineError("invalid_state", "Recipe check must be an object")
    for field in ("recipe_task_id", "recipe_artifact_id"):
        if not isinstance(full_result.get(field), str) or not full_result[field]:
            raise EngineError("invalid_state", "Recipe check needs its exact managed artifact reference")
    if type(full_result.get("recipe_artifact_revision")) is not int or full_result["recipe_artifact_revision"] < 1:
        raise EngineError("invalid_state", "Recipe check needs a positive artifact revision")
    inputs, parameters = full_result.get("required_inputs"), full_result.get("parameters")
    qualification, requirements = full_result.get("qualification"), full_result.get("requirements")
    if (not isinstance(inputs, dict) or not isinstance(parameters, dict)
            or any(not isinstance(name, str) or not isinstance(value, dict) for name, value in inputs.items())
            or any(not isinstance(name, str) or not isinstance(value, dict) for name, value in parameters.items())
            or not isinstance(qualification, dict) or not isinstance(qualification.get("cases", []), list)
            or any(not isinstance(case, dict) for case in qualification.get("cases", []))
            or not isinstance(requirements, dict) or not isinstance(requirements.get("features", []), list)
            or any(not isinstance(feature, str) for feature in requirements.get("features", []))):
        raise EngineError("invalid_state", "Recipe check definitions or qualification records are invalid")
    original_bytes = _bytes(full_result)
    if detail == "full":
        result = copy.deepcopy(full_result)
        result["projection"] = {"detail": "full", "bounded": False, "budget_bytes": None,
                                "original_bytes": original_bytes, "complete": True}
        return result

    identity_fields = ("schema_valid", "recipe_id", "revision", "sha256", "semantic_digest", "supported",
                       "execution_authorized", "recipe_task_id", "recipe_artifact_id", "recipe_artifact_revision")
    result = {field: copy.deepcopy(full_result[field]) for field in identity_fields if field in full_result}
    cases, features = qualification.get("cases", []), requirements.get("features", [])
    statuses = {}
    for case in cases:
        status = case.get("status", "unknown")
        if not isinstance(status, str) or status not in {"example_verified", "outputs_verified", "unverified", "failed_example"}:
            status = "unknown"
        statuses[status] = statuses.get(status, 0) + 1
    result.update(
        required_inputs={}, parameters={}, requirements={"features": []},
        qualification={"status": qualification.get("status", "unknown"), "cases": [],
                       "case_count": len(cases), "case_statuses": statuses,
                       "meaning": "Historical case checks; not general quality, new-task approval or execution authority."},
        totals={"required_inputs": len(inputs), "parameters": len(parameters),
                "requirements": len(features), "qualification_cases": len(cases)},
        omitted={"required_inputs": len(inputs), "parameters": len(parameters),
                 "requirements": len(features), "qualification_cases": len(cases)},
        projection={
            "detail": "compact", "bounded": True, "complete": False,
            "budget_bytes": MAX_RESPONSE_BYTES, "response_bytes": 0, "original_bytes": original_bytes,
            "global_counts": True,
            "omitted_fields": sorted(set(full_result) - set(identity_fields) - {
                "required_inputs", "parameters", "requirements", "qualification"}),
            "read": {"full": {"tool": "check_recipe", "arguments": {
                "recipe_task_id": full_result["recipe_task_id"],
                "recipe_artifact_id": full_result["recipe_artifact_id"],
                "recipe_artifact_revision": full_result["recipe_artifact_revision"], "detail": "full"}}},
            "meaning": "Bounded discovery preview. Input and parameter definitions require full reading before use; totals include omitted records.",
        },
    )
    safe_limit = MAX_RESPONSE_BYTES - 256
    if _bytes(result) > safe_limit:
        raise EngineError("projection_too_large", "Recipe identity cannot fit without truncation; request detail=full")
    for section, values, fields in (
        ("required_inputs", inputs, ("required", "format")),
        ("parameters", parameters, ("type", "required")),
    ):
        for name in sorted(values)[:8]:
            card = _recipe_card(values[name], fields)
            if section == "parameters":
                card["has_default"] = "default" in values[name]
                if isinstance(values[name].get("enum"), list):
                    card["enum_count"] = len(values[name]["enum"])
            result[section][name] = card
            if _bytes(result) > safe_limit:
                result[section].pop(name)
                break
            result["omitted"][section] -= 1
    for feature in features[:8]:
        result["requirements"]["features"].append(feature)
        if _bytes(result) > safe_limit:
            result["requirements"]["features"].pop()
            break
        result["omitted"]["requirements"] -= 1
    for case in cases[:6]:
        card = _recipe_card(case, ("kind", "case_id", "task_id", "status", "reason"))
        result["qualification"]["cases"].append(card)
        if _bytes(result) > safe_limit:
            result["qualification"]["cases"].pop()
            break
        result["omitted"]["qualification_cases"] -= 1
    return _finish(result)
