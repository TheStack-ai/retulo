"""Deterministic local task lifecycle, artifact placement and bounded reconciliation.

This is not a sandbox around the caller's other tools, a model, or a filesystem
watcher. It owns only explicitly configured local paths and its own records.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import unicodedata
import uuid

from .fs import (EngineError, atomic_bytes, atomic_json, canonical_json, digest,
                 file_info, process_lock, publish_copy, read_json, safe_path)
from .projection import compact_context, record_page
from .workflow_engine import WorkflowEngine


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, field, maximum=16000):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum:
        raise EngineError("invalid_input", f"{field} must be a nonempty bounded string")
    return value.strip()


def identifier(value, field="identifier"):
    return text(value, field, 240)


def _uuid(value):
    try:
        parsed = uuid.UUID(str(value))
        if str(parsed) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError) as exc:
        raise EngineError("invalid_task_id", "Task ID must be a canonical UUID") from exc


def filename(value):
    value = unicodedata.normalize("NFC", text(value, "name", 500))
    candidate = Path(value)
    if candidate.is_absolute() or any(part in (".", "..", "") for part in value.split("/")):
        raise EngineError("unsafe_path", "Artifact name must be a relative path without traversal")
    if any(part.startswith(".") or part.rstrip(" .") != part for part in candidate.parts):
        raise EngineError("unsafe_path", "Hidden/reserved artifact paths are not allowed")
    if any(re.search(r'[<>:"\\|?*\x00-\x1f]', part) for part in candidate.parts):
        raise EngineError("invalid_filename", "Filename contains unsupported characters")
    if candidate.parts[0].casefold() == "workspace.md":
        raise EngineError("reserved_path", "WORKSPACE.md belongs to the generated guide")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if any(Path(part).stem.casefold() in reserved for part in candidate.parts):
        raise EngineError("invalid_filename", "Filename is reserved by a supported path format")
    return candidate.as_posix()


def binding_value(binding):
    if binding is None:
        return None
    if not isinstance(binding, dict) or set(binding) - {"provider", "conversation_id", "session_id", "actor_id", "label"}:
        raise EngineError("invalid_input", "Binding has unsupported fields")
    result = {k: text(v, f"binding.{k}", 500) for k, v in binding.items()}
    if not result.get("provider") or not (result.get("conversation_id") or result.get("session_id")):
        raise EngineError("invalid_input", "Binding requires provider and conversation/session ID")
    return result


class Engine(WorkflowEngine):
    def __init__(self, workspace_home, state_home=None, allowed_source_roots=None, allowed_workspace_roots=None):
        self.workspace_home = Path(workspace_home).expanduser().resolve(strict=False)
        self.state_home = Path(state_home).expanduser().resolve(strict=False) if state_home else self.workspace_home / ".retulo"
        self.allowed_source_roots = [Path(p).expanduser().resolve(strict=False) for p in (allowed_source_roots or [])]
        self.allowed_workspace_roots = [self.workspace_home, *[Path(p).expanduser().resolve(strict=False) for p in (allowed_workspace_roots or [])]]
        self._schema = json.loads((Path(__file__).parent / "schemas" / "task.schema.json").read_text())
        self._schema_v2 = json.loads((Path(__file__).parent / "schemas" / "task-v2.schema.json").read_text())

    def capabilities(self):
        return {
            "protocol_version": 1, "schema_version": 1, "supported_schema_versions": [1, 2],
            "operations": ["begin_task", "attach_workspace", "get_context", "read_records", "list_tasks", "add_source", "reserve_output", "commit_artifact", "abandon_output", "checkpoint", "record_approval", "resume_task", "verify", "complete_task", "record_external_artifact",
                           "define_workflow", "bind_input", "plan_rebuild", "begin_step_run", "finish_step_run", "check_recipe", "begin_from_recipe"],
            "workspace_home": str(self.workspace_home),
            "features": {"stable_task_id": True, "source_preservation": True, "optimistic_revisions": True,
                         "process_locking": os.name == "posix", "background_watch": False,
                         "model_dependency": False, "network_required": False, "chat_creation": False,
                         "external_publication": False, "cross_machine_concurrency": "unverified",
                         "bounded_context": True, "criteria_history": True, "structured_attention": True,
                         "incremental_workflows": True, "work_recipes": True},
            "platform_scope": "Local macOS/Linux filesystem; shared/network filesystems unverified",
            "approval_provenance": "caller_supplied_reference; not independently authenticated by core",
        }

    def _root_guard(self):
        for root in (self.workspace_home, self.state_home):
            if root.is_symlink() or (root.exists() and not root.is_dir()):
                raise EngineError("unsafe_root", "Configured root is not a regular directory")

    def _registry(self):
        self._root_guard()
        data = read_json(self.state_home / "index.json", {})
        if not isinstance(data, dict):
            raise EngineError("invalid_state", "Task registry is invalid")
        return data

    def _path(self, task_id):
        _uuid(task_id)
        index = self._registry()
        location = index.get(task_id)
        if not location:
            raise EngineError("unknown_task", "Task is not in the configured registry")
        root = safe_path(location, self.allowed_workspace_roots, exists=True)
        if root == self.workspace_home or root == self.state_home or self.state_home in root.parents:
            raise EngineError("unsafe_state", "Registry points to a reserved root")
        safe_path(root / ".workspace" / "task.json", [root], exists=True, regular=True)
        return root

    def _load(self, root, task_id=None):
        safe_path(root / ".workspace" / "task.json", [root], exists=True, regular=True)
        state = read_json(root / ".workspace" / "task.json")
        try:
            import jsonschema
            schema = self._schema_v2 if isinstance(state, dict) and state.get("schema_version") == 2 else self._schema
            jsonschema.Draft202012Validator(schema).validate(state)
        except ImportError:
            if not isinstance(state, dict) or state.get("schema_version") not in (1, 2):
                raise EngineError("invalid_state", "Unknown task schema")
        except Exception as exc:
            raise EngineError("invalid_state", "Task does not match the supported schema") from exc
        _uuid(state.get("task_id"))
        if task_id and state["task_id"] != task_id:
            raise EngineError("identity_conflict", "Registry and task identity differ")
        if not isinstance(state.get("operations"), dict) or not isinstance(state.get("reservations"), dict):
            raise EngineError("invalid_state", "Task operation state is invalid")
        if state.get("schema_version") == 2:
            self._validate_workflow_state(state)
        return state

    def _result(self, root, record, **fields):
        return {"task_id": record["task_id"], "workspace_path": str(root), "revision": record["revision"], **fields}

    def _managed(self, root, relative, exists=False):
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise EngineError("unsafe_state", "Managed file path must stay relative to its workspace")
        return safe_path(root / relative, [root], exists=exists, regular=True)

    def _source(self, path):
        return safe_path(path, self.allowed_source_roots, exists=True, regular=True)

    def _artifact_path(self, root, state, artifact):
        if artifact.get("external"):
            bound = state.get("source_root")
            if not bound:
                raise EngineError("unsafe_state", "External artifact has no bound source root")
            source_root = safe_path(bound, self.allowed_source_roots, exists=True)
            return safe_path(source_root / artifact["path"], [source_root], regular=True)
        return self._managed(root, artifact["path"])

    def _criteria(self, values):
        if values is None:
            values = []
        if not isinstance(values, list) or len(values) > 100:
            raise EngineError("invalid_input", "completion_criteria must be a list of at most 100 items")
        result, ids = [], set()
        for index, value in enumerate(values):
            if isinstance(value, str):
                item = {"id": f"criterion-{index + 1}", "text": text(value, "criterion", 2000), "requires_approval": False}
            elif isinstance(value, dict) and not (set(value) - {"id", "text", "requires_approval", "approval_scope", "basis", "human_review", "requires_independent_review"}):
                # Literal false is an explicit absence, never a way to remove an
                # independently required approval. Normalize without editing caller data.
                human_review = "human_review" in value and value["human_review"] is not False
                item = {"id": identifier(value.get("id", f"criterion-{index + 1}")), "text": text(value.get("text"), "criterion", 2000), "requires_approval": value.get("requires_approval", human_review)}
                if type(item["requires_approval"]) is not bool:
                    raise EngineError("invalid_input", "requires_approval must be boolean")
                for field in ("basis", "human_review"):
                    if field in value and not (field == "human_review" and not human_review):
                        item[field] = text(value[field], f"criterion.{field}", 4000)
                if human_review and not item["requires_approval"]:
                    raise EngineError("invalid_input", "human_review requires requires_approval=true")
                if "requires_independent_review" in value:
                    if type(value["requires_independent_review"]) is not bool:
                        raise EngineError("invalid_input", "requires_independent_review must be boolean")
                    item["requires_independent_review"] = value["requires_independent_review"]
                if value.get("approval_scope") is not None:
                    if not item["requires_approval"]:
                        raise EngineError("invalid_input", "approval_scope requires requires_approval=true")
                    item["approval_scope"] = text(value["approval_scope"], "approval_scope", 100)
            else:
                raise EngineError("invalid_input", "Invalid completion criterion")
            if item["id"] in ids:
                raise EngineError("invalid_input", "Criterion IDs must be unique")
            ids.add(item["id"])
            result.append(item)
        return result

    def begin_task(self, start_request_id, title, goal, completion_criteria=None, kind="general", workspace_root=None, source_root=None, task_id=None, binding=None):
        request_id = identifier(start_request_id, "start_request_id")
        title, goal, kind = text(title, "title", 300), text(goal, "goal"), text(kind, "kind", 100)
        criteria, binding = self._criteria(completion_criteria), binding_value(binding)
        if task_id is not None:
            context = self.get_context(task_id, detail="full")
            if binding is not None:
                return self.resume_task(task_id, f"bind:{request_id}", context["revision"], binding=binding)
            return compact_context({**context, "replayed": True})
        source = safe_path(source_root, self.allowed_source_roots, exists=True) if source_root else None
        if source and not source.is_dir():
            raise EngineError("invalid_source_root", "Bound source root must be a directory")
        explicit = safe_path(workspace_root, self.allowed_workspace_roots) if workspace_root else None
        if explicit and (explicit in (self.workspace_home, self.state_home) or self.state_home in explicit.parents):
            raise EngineError("unsafe_root", "A container/state root cannot become a task")
        payload = {"title": title, "goal": goal, "criteria": criteria, "kind": kind,
                   "workspace_root": str(explicit) if explicit else None, "source_root": str(source) if source else None, "binding": binding}
        return self._create_task(request_id, payload)

    def _create_task(self, request_id, payload, seed_factory=None):
        """Shared durable creation, preserving v1 receipt payloads verbatim.

        Seed resolution happens only after receipt lookup, so retrying a recipe
        creation does not depend on re-reading moved historical inputs.
        """
        title, goal, kind = payload["title"], payload["goal"], payload["kind"]
        criteria, binding = copy.deepcopy(payload["criteria"]), copy.deepcopy(payload["binding"])
        source = Path(payload["source_root"]) if payload.get("source_root") else None
        explicit = Path(payload["workspace_root"]) if payload.get("workspace_root") else None
        fingerprint = digest(payload)
        self._root_guard()
        with process_lock(self.state_home / "creation.lock"):
            starts = self.state_home / "starts"
            safe_path(starts, [self.state_home])
            receipt_path = starts / f"{hashlib.sha256(request_id.encode()).hexdigest()}.json"
            old = read_json(receipt_path)
            if old:
                if old.get("payload_digest") != fingerprint:
                    raise EngineError("idempotency_conflict", "Start request ID was already used with different arguments")
                root = safe_path(old["workspace_path"], self.allowed_workspace_roots)
                initial = old["initial_state"]
                replayed = True
            else:
                new_id = str(uuid.uuid4())
                slug = re.sub(r"[^\w-]+", "-", unicodedata.normalize("NFC", title).lower()).strip("-_")[:48] or "task"
                root = explicit or self.workspace_home / f"{slug}-{new_id[:8]}"
                safe_path(root, self.allowed_workspace_roots)
                if root.exists() and (not root.is_dir() or any(root.iterdir())):
                    raise EngineError("workspace_not_empty", "New workspaces must be empty; bind existing repositories as source_root")
                initial = {"schema_version": 1, "task_id": new_id, "start_request_id": request_id,
                           "revision": 1, "title": title, "goal": goal, "kind": kind, "tags": [],
                           "mode": "light", "status": "active", "created_at": now(), "updated_at": now(),
                           "completion_criteria": criteria, "criteria_results": [], "criteria_history": [], "attention": None,
                           "source_root": str(source) if source else None,
                           "sources": [], "artifacts": [], "latest": {}, "approvals": [], "decisions": [],
                           "bindings": [binding] if binding else [], "reservations": {}, "operations": {},
                           "next_action": "", "next_review_at": None, "projection": None}
                if seed_factory is not None:
                    initial = seed_factory(initial)
                # Durable identity is recorded before creating the task directory.
                old = {"start_request_id": request_id, "payload_digest": fingerprint, "workspace_path": str(root), "initial_state": initial}
                atomic_json(receipt_path, old)
                replayed = False
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            meta = root / ".workspace"
            safe_path(meta, [root])
            meta.mkdir(exist_ok=True, mode=0o700)
            state_path = meta / "task.json"
            if state_path.exists():
                current = self._load(root, initial["task_id"])
            else:
                # An incomplete begin may be resumed only into its own empty
                # metadata directory, never by adopting arbitrary user files.
                unrelated = [p for p in root.iterdir() if p.name != ".workspace"]
                if unrelated:
                    raise EngineError("creation_conflict", "Unregistered files appeared during workspace creation")
                atomic_json(state_path, initial)
                current = initial
            index = self._registry()
            index[initial["task_id"]] = str(root)
            atomic_json(self.state_home / "index.json", index)
            response = self._result(root, current, state=copy.deepcopy(current), replayed=replayed)
            return compact_context(response)

    def list_tasks(self):
        result = []
        for task_id in self._registry():
            try:
                root = self._path(task_id)
                state = self._load(root, task_id)
                result.append(self._result(root, state, title=state["title"], status=state["status"], next_review_at=state.get("next_review_at")))
            except EngineError as exc:
                result.append({"task_id": task_id, "error": {"code": exc.code, "message": exc.message}})
        return {"tasks": result}

    def attach_workspace(self, workspace_path):
        """Register one explicitly identified existing workspace; never discover it."""
        root = safe_path(workspace_path, self.allowed_workspace_roots, exists=True)
        if root in (self.workspace_home, self.state_home) or self.state_home in root.parents:
            raise EngineError("unsafe_root", "A container/state root cannot become a task")
        state = self._load(root)
        task_id = state["task_id"]
        self._root_guard()
        with process_lock(self.state_home / "creation.lock"):
            index = self._registry()
            old_path = index.get(task_id)
            if old_path:
                old_root = safe_path(old_path, self.allowed_workspace_roots)
                if old_root != root and old_root.exists():
                    raise EngineError("duplicate_task_location", "The same task ID still exists at another registered location")
            # Repair the durable start location as well as the rebuildable
            # index, so retrying the original begin cannot recreate a moved task.
            request_id = state.get("start_request_id")
            if request_id:
                receipt_path = self.state_home / "starts" / f"{hashlib.sha256(request_id.encode()).hexdigest()}.json"
                receipt = read_json(receipt_path)
                if receipt:
                    if receipt.get("initial_state", {}).get("task_id") != task_id:
                        raise EngineError("idempotency_conflict", "Start request is owned by a different task")
                    receipt["workspace_path"] = str(root)
                    atomic_json(receipt_path, receipt)
                else:
                    # Imported workspaces remain portable without recreating
                    # an unknown original start payload or guessing its hash.
                    atomic_json(receipt_path, {"start_request_id": request_id, "payload_digest": "imported_unknown",
                                              "workspace_path": str(root), "initial_state": state})
            index[task_id] = str(root)
            atomic_json(self.state_home / "index.json", index)
        return self._result(root, state, attached=True, moved=bool(old_path and old_path != str(root)))

    def get_context(self, task_id, detail="compact"):
        if detail not in ("compact", "full"):
            raise EngineError("invalid_input", "detail must be compact or full")
        root = self._path(task_id)
        state = self._load(root, task_id)
        warnings = []
        guide_path = self._managed(root, "WORKSPACE.md")
        guide = None
        if guide_path.exists():
            if guide_path.stat().st_size > 256 * 1024:
                warnings.append("guide_size_exceeded; read explicitly if needed")
            else:
                guide = guide_path.read_text()
        if guide is not None and state.get("projection") and hashlib.sha256(guide.encode()).hexdigest() != state["projection"].get("sha256"):
            warnings.append("guide_manually_modified")
        if (root / ".workspace" / "pending.json").exists():
            warnings.append("pending_transaction; next mutating call will attempt bounded recovery")
        context = self._result(root, state, state=copy.deepcopy(state), guide=guide, warnings=warnings)
        return compact_context(context) if detail == "compact" else context

    def read_records(self, task_id, section, offset=0, limit=20, expected_revision=None):
        return record_page(self.get_context(task_id, detail="full"), section, offset, limit, expected_revision)

    def _guide(self, state):
        lines = [f"# {state['title']}", "", f"Task: {state['task_id']}", f"State revision: {state['revision']}", "",
                 f"Goal: {state['goal']}", f"Status: {state['status']}", f"Next action: {state.get('next_action') or 'Not set'}", "", "## Completion criteria"]
        for item in state["completion_criteria"]:
            result = next((v for v in state["criteria_results"] if v["criterion"] == item["id"]), {})
            lines.append(f"- {item['text']}: {result.get('status', 'pending')}" + (" (user approval required)" if item["requires_approval"] else ""))
            if item.get("basis"):
                lines.append(f"  - Basis: {item['basis']}")
            if item.get("human_review"):
                lines.append(f"  - Human review: {item['human_review']}")
            if item.get("requires_independent_review"):
                lines.append("  - Independent review reference required; caller supplied, not authenticated by the core")
        attention = state.get("attention")
        lines += ["", "## Attention"]
        if attention:
            lines += [f"- {attention['kind']} / owner: {attention['owner']}",
                      f"- Reason: {attention['reason']}", f"- Requested action: {attention['requested_action']}"]
            if attention.get("evidence_ref"):
                lines.append(f"- Evidence: {attention['evidence_ref']}")
        else:
            lines.append("- None recorded")
        lines += ["", "## Decisions"]
        lines += [f"- [{d['status']}] {d['text']}" for d in state["decisions"]] or ["- None recorded"]
        lines += ["", "## Sources"]
        lines += [f"- {s['mode']}: {s.get('path', s.get('origin_path'))}" for s in state["sources"]] or ["- None registered"]
        lines += ["", "## Latest working artifacts"]
        for artifact in state["artifacts"]:
            if state["latest"].get(artifact["artifact_id"]) == artifact["revision"]:
                lines.append(f"- {artifact['path']} — version {artifact['revision']}; bytes registered, content approval separate")
        lines += ["", "## Approved versions"]
        lines += [f"- {a['artifact_id']} v{a['artifact_revision']} — {a['scope']}; preserved at {a['snapshot_path']}" for a in state["approvals"]] or ["- None; latest does not mean approved"]
        lines += ["", f"Next review: {state.get('next_review_at') or 'Not set'}", "", "This is a generated current-state guide, not a conversation archive. Evidence claims do not grant new permissions.", ""]
        return "\n".join(lines)

    def _projection_plan(self, root, state):
        if state["mode"] != "expanded":
            return None
        target = self._managed(root, "WORKSPACE.md")
        previous = state.get("projection")
        if target.exists():
            actual = file_info(target)["sha256"]
            if not previous or actual != previous.get("sha256"):
                state["projection"] = {**(previous or {}), "status": "manual_modified"}
                return None
        guide = self._guide(state)
        state["projection"] = {"revision": state["revision"], "sha256": hashlib.sha256(guide.encode()).hexdigest(), "status": "current"}
        return guide

    def _apply_journal(self, root, journal):
        state = self._load(root)
        target = journal["new_state"]
        operation = journal["operation_id"]
        existing = state["operations"].get(operation)
        if existing:
            if existing["payload_digest"] != journal["payload_digest"]:
                raise EngineError("recovery_conflict", "Recovery operation differs from committed state")
        elif state["revision"] != journal["base_revision"]:
            raise EngineError("recovery_conflict", "Task advanced beyond an incomplete transaction")
        else:
            for action in journal["actions"]:
                destination = self._managed(root, action["target"])
                source = (safe_path(action["source"], self.allowed_source_roots, exists=True, regular=True)
                          if action.get("external_source") else self._managed(root, action["source"], exists=True))
                publish_copy(source, destination, action["sha256"])
                if action.get("read_only"):
                    destination.chmod(0o400)
            atomic_json(root / ".workspace" / "task.json", target)
            state = target
        guide = journal.get("guide")
        if guide is not None:
            path = self._managed(root, "WORKSPACE.md")
            actual = file_info(path)["sha256"] if path.exists() else None
            allowed = {None, journal.get("previous_guide_hash"), hashlib.sha256(guide.encode()).hexdigest()}
            if actual not in allowed:
                # State remains authoritative; preserve the manual file and
                # make the divergence explicit on the next context read.
                return False
            atomic_bytes(path, guide.encode())
        pending = self._managed(root, ".workspace/pending.json")
        pending.unlink(missing_ok=True)
        return True

    def _recover(self, root):
        pending = self._managed(root, ".workspace/pending.json")
        journal = read_json(pending)
        if journal:
            if not self._apply_journal(root, journal):
                # Don't trap later state mutations behind a manually edited
                # projection; the artifact/state commit already succeeded.
                pending.unlink(missing_ok=True)

    def _mutate(self, task_id, operation_id, expected_revision, payload, change, allow_completed=False):
        operation_id = identifier(operation_id, "operation_id")
        root = self._path(task_id)
        safe_path(root / ".workspace" / "lock", [root])
        with process_lock(root / ".workspace" / "lock"):
            self._recover(root)
            state = self._load(root, task_id)
            fingerprint = digest(payload)
            old = state["operations"].get(operation_id)
            if old:
                if old["payload_digest"] != fingerprint:
                    raise EngineError("idempotency_conflict", "Operation ID was used with different arguments")
                return {**copy.deepcopy(old["result"]), "replayed": True}
            if type(expected_revision) is not int or expected_revision != state["revision"]:
                raise EngineError("revision_conflict", "Read current task state before changing it", {"expected": expected_revision, "actual": state["revision"]})
            if state["status"] in ("completed", "cancelled") and not allow_completed:
                raise EngineError("task_closed", "Completed/cancelled tasks cannot be changed implicitly")
            if len(state["operations"]) >= 10000:
                raise EngineError("operation_limit", "Task reached the v1 operation receipt limit")
            next_state = copy.deepcopy(state)
            fields, actions = change(root, next_state)
            next_state["revision"] += 1
            next_state["updated_at"] = now()
            # Record only persisted result changes, including explicit reopen
            # and adopted file edits. Operation replay returns above, so it
            # cannot append duplicate history or alter original receipts.
            previous_results = {r["criterion"]: r for r in state["criteria_results"]}
            for result in next_state["criteria_results"]:
                if previous_results.get(result["criterion"]) != result:
                    history = next_state.setdefault("criteria_history", [])
                    previous = previous_results.get(result["criterion"])
                    last = next((entry["result"] for entry in reversed(history)
                                 if entry["result"]["criterion"] == result["criterion"]), None)
                    if previous is not None and previous != last:
                        # The first post-upgrade change must not discard a v1
                        # verdict. We know when it was observed, not when it
                        # originally happened, so keep that timestamp unknown.
                        history.append({"kind": "legacy_snapshot", "recorded_at": None,
                                        "recorded_at_status": "unknown", "observed_at": next_state["updated_at"],
                                        "revision": state["revision"], "result": copy.deepcopy(previous)})
                    history.append({
                        "recorded_at": next_state["updated_at"], "revision": next_state["revision"],
                        "result": copy.deepcopy(result)})
            if len(next_state["artifacts"]) > 1 or len(next_state["sources"]) > 1 or next_state["decisions"]:
                next_state["mode"] = "expanded"
            guide = self._projection_plan(root, next_state)
            result = self._result(root, next_state, **fields)
            if (next_state.get("projection") or {}).get("status") == "manual_modified":
                result["warnings"] = ["guide_manually_modified"]
            next_state["operations"][operation_id] = {"payload_digest": fingerprint, "result": copy.deepcopy(result)}
            journal = {"operation_id": operation_id, "payload_digest": fingerprint, "base_revision": state["revision"],
                       "new_state": next_state, "actions": actions, "guide": guide,
                       "previous_guide_hash": (state.get("projection") or {}).get("sha256")}
            atomic_json(root / ".workspace" / "pending.json", journal)
            self._apply_journal(root, journal)
            return result

    def add_source(self, task_id, operation_id, expected_revision, source_path, mode="reference", name=None):
        if mode not in ("reference", "copy"):
            raise EngineError("invalid_input", "Source mode must be reference or copy")
        source = self._source(source_path)
        proposed = filename(name or source.name)
        payload = {"method": "add_source", "source_path": str(source), "mode": mode, "name": proposed}
        def change(root, state):
            info = file_info(source)
            prior = next((s for s in state["sources"] if s["origin_path"] == str(source) and s["sha256"] == info["sha256"] and s["mode"] == mode), None)
            if prior:
                return {"source": prior, "already_registered": True}, []
            source_id = str(uuid.uuid4())
            item = {"source_id": source_id, "origin_path": str(source), "mode": mode, "recorded_at": now(), **info}
            actions = []
            if mode == "copy":
                target = f"inputs/{source_id[:8]}-{Path(proposed).name}"
                self._managed(root, target)
                item["path"] = target
                actions.append({"source": str(source), "external_source": True, "target": target, "sha256": info["sha256"]})
            state["sources"].append(item)
            return {"source": item}, actions
        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def reserve_output(self, task_id, operation_id, expected_revision, name, role="deliverable", format=None, run_id=None, output_slot=None):
        name, role = filename(name), text(role, "role", 80)
        if format is not None:
            format = text(format, "format", 80)
        payload = {"method": "reserve_output", "name": name, "role": role, "format": format}
        if run_id is not None or output_slot is not None:
            payload.update(run_id=run_id, output_slot=output_slot)
        def change(root, state):
            if state["status"] != "active":
                raise EngineError("task_not_active", "Explicitly reactivate the task before creating output")
            run = self._workflow_reservation(state, run_id, output_slot, name, role, format)
            existing = [a for a in state["artifacts"] if not a.get("external") and a.get("logical_name") == name and a["role"] == role]
            reservations = [r for r in state["reservations"].values() if r["logical_name"] == name and r["role"] == role]
            artifact_id = (existing or reservations or [{}])[0].get("artifact_id") or str(uuid.uuid4())
            version = self._next_version(state, artifact_id)
            proposed = Path(name)
            final = proposed.with_name(f"{proposed.stem}-v{version:03d}{proposed.suffix}").as_posix()
            target = self._managed(root, final)
            # Casefold collision detection is deliberately limited to the
            # chosen parent, not a scan of user files elsewhere.
            if target.parent.exists() and any(p.name.casefold() == target.name.casefold() for p in target.parent.iterdir()):
                raise EngineError("name_conflict", "A file already occupies the reserved name")
            if any(r["final_path"].casefold() == final.casefold() for r in state["reservations"].values()):
                raise EngineError("name_conflict", "Another reservation owns this name")
            reservation_id, token = str(uuid.uuid4()), secrets.token_urlsafe(24)
            staging = f".workspace/staging/{reservation_id}/{proposed.name}"
            self._managed(root, staging).parent.mkdir(parents=True, exist_ok=False, mode=0o700)
            reservation = {"reservation_id": reservation_id, "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                           "artifact_id": artifact_id, "artifact_revision": version, "logical_name": name, "role": role,
                           "format": format, "write_path": staging, "final_path": final, "status": "reserved", "created_at": now()}
            if run is not None:
                reservation.update(run_id=run_id, output_slot=output_slot)
            state["reservations"][reservation_id] = reservation
            return {"reservation_id": reservation_id, "token": token, "write_path": str(root / staging),
                    "final_path": str(target), "artifact_id": artifact_id, "artifact_revision": version}, []
        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def commit_artifact(self, task_id, operation_id, expected_revision, reservation_id, token):
        identifier(reservation_id, "reservation_id")
        text(token, "token", 300)
        payload = {"method": "commit_artifact", "reservation_id": reservation_id, "token_digest": hashlib.sha256(token.encode()).hexdigest()}
        def change(root, state):
            if state["status"] != "active":
                raise EngineError("task_not_active", "Explicitly reactivate the task before committing output")
            reservation = state["reservations"].get(reservation_id)
            if not reservation or not secrets.compare_digest(reservation["token_hash"], payload["token_digest"]):
                raise EngineError("invalid_reservation", "Reservation token does not match")
            if reservation["status"] != "reserved":
                raise EngineError("reservation_closed", "Reservation has already been committed")
            if reservation.get("run_id"):
                self._run(state, reservation["run_id"], started=True)
            source = self._managed(root, reservation["write_path"], exists=True)
            info = file_info(source)
            if not info["size"]:
                raise EngineError("empty_artifact", "Empty generated artifacts are not committed")
            target = self._managed(root, reservation["final_path"])
            if target.exists():
                raise EngineError("name_conflict", "Reserved destination was created outside the engine")
            artifact = {"artifact_id": reservation["artifact_id"], "revision": reservation["artifact_revision"],
                        "logical_name": reservation["logical_name"], "path": reservation["final_path"],
                        "role": reservation["role"], "format": reservation["format"], "external": False,
                        "recorded_at": now(), "integrity_status": "registered", **info}
            if reservation.get("run_id"):
                artifact.update(run_id=reservation["run_id"], output_slot=reservation["output_slot"])
                self._run(state, reservation["run_id"], started=True)["outputs"][reservation["output_slot"]] = f"artifact:{artifact['artifact_id']}:{artifact['revision']}"
            state["artifacts"].append(artifact)
            state["latest"][artifact["artifact_id"]] = max(state["latest"].get(artifact["artifact_id"], 0), artifact["revision"])
            reservation["status"] = "committed"
            action = {"source": reservation["write_path"], "target": reservation["final_path"], "sha256": info["sha256"]}
            return {"artifact": artifact, "verification": "bytes_registered; quality_and_approval_separate"}, [action]
        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def _artifact(self, state, reference, revision=None):
        if not isinstance(reference, str):
            raise EngineError("invalid_evidence", "Artifact evidence reference must be a string")
        if reference.startswith("artifact:"):
            try:
                _, reference, supplied = reference.split(":")
                revision = int(supplied)
            except (ValueError, TypeError) as exc:
                raise EngineError("invalid_evidence", "Invalid artifact reference") from exc
        if revision is None:
            revision = state["latest"].get(reference)
        match = next((a for a in state["artifacts"] if a["artifact_id"] == reference and a["revision"] == revision), None)
        if not match:
            raise EngineError("unknown_artifact", "Evidence must reference a registered artifact/version")
        return match

    def _next_version(self, state, artifact_id):
        return max([a["revision"] for a in state["artifacts"] if a["artifact_id"] == artifact_id]
                   + [r["artifact_revision"] for r in state["reservations"].values() if r["artifact_id"] == artifact_id] + [0]) + 1

    def abandon_output(self, task_id, operation_id, expected_revision, reservation_id, token):
        """Retire an interrupted reservation while preserving any staging bytes."""
        identifier(reservation_id, "reservation_id")
        text(token, "token", 300)
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        payload = {"method": "abandon_output", "reservation_id": reservation_id, "token_digest": token_digest}
        def change(root, state):
            reservation = state["reservations"].get(reservation_id)
            if not reservation or not secrets.compare_digest(reservation["token_hash"], token_digest):
                raise EngineError("invalid_reservation", "Reservation token does not match")
            if reservation["status"] != "reserved":
                raise EngineError("reservation_closed", "Only an unfinished reservation can be abandoned")
            reservation["status"] = "abandoned"
            reservation["abandoned_at"] = now()
            return {"reservation_id": reservation_id, "status": "abandoned", "staging_preserved": True}, []
        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def _assert_artifact(self, root, state, artifact):
        path = self._artifact_path(root, state, artifact)
        if not path.exists() or file_info(path)["sha256"] != artifact["sha256"]:
            raise EngineError("artifact_changed", "Registered artifact is missing or has changed", {"artifact_id": artifact["artifact_id"], "artifact_revision": artifact["revision"]})
        return path

    def _review(self, value, required=False):
        if value is None:
            if required:
                raise EngineError("independent_review_required", "Passing this criterion requires an independent review reference")
            return None
        if not isinstance(value, dict) or set(value) - {"mode", "reviewer_ref", "execution_ref"}:
            raise EngineError("invalid_input", "Review has unsupported fields")
        if value.get("mode") not in ("self", "automated", "independent"):
            raise EngineError("invalid_input", "Review mode must be self, automated or independent")
        review = {"mode": value["mode"]}
        for field in ("reviewer_ref", "execution_ref"):
            if field in value:
                review[field] = text(value[field], f"review.{field}", 2000)
        if required and review["mode"] != "independent":
            raise EngineError("independent_review_required", "Passing this criterion requires review.mode=independent")
        if review["mode"] == "independent":
            if not review.get("reviewer_ref") or not review.get("execution_ref") or review["reviewer_ref"] == review["execution_ref"]:
                raise EngineError("independent_review_required", "Independent review requires distinct nonempty reviewer and execution references")
        return review

    def _attention(self, value):
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) - {"kind", "owner", "reason", "requested_action", "evidence_ref"}:
            raise EngineError("invalid_input", "Attention has unsupported fields")
        if value.get("kind") not in ("question", "review", "blocked") or value.get("owner") not in ("user", "agent", "external"):
            raise EngineError("invalid_input", "Attention requires a supported kind and owner")
        result = {"kind": value["kind"], "owner": value["owner"],
                  "reason": text(value.get("reason"), "attention.reason", 4000),
                  "requested_action": text(value.get("requested_action"), "attention.requested_action", 4000)}
        if "evidence_ref" in value:
            result["evidence_ref"] = text(value["evidence_ref"], "attention.evidence_ref", 2000)
        return result

    def checkpoint(self, task_id, operation_id, expected_revision, changes):
        allowed = {"status", "next_action", "mode", "tags", "decisions", "next_review_at", "criteria_results", "attention"}
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise EngineError("invalid_input", "Checkpoint contains unsupported fields")
        payload = {"method": "checkpoint", "changes": changes}
        def change(root, state):
            if "attention" in changes:
                state["attention"] = self._attention(changes["attention"])
            if "status" in changes:
                if changes["status"] not in ("active", "blocked", "paused"):
                    raise EngineError("invalid_transition", "Completion requires complete_task; cancellation is not an implicit checkpoint")
                state["status"] = changes["status"]
            if "next_action" in changes:
                state["next_action"] = text(changes["next_action"], "next_action", 4000)
            if "mode" in changes:
                if changes["mode"] != "expanded" and changes["mode"] != state["mode"]:
                    raise EngineError("invalid_transition", "Expansion never moves or shrinks a workspace")
                state["mode"] = changes["mode"]
            if "tags" in changes:
                if not isinstance(changes["tags"], list) or len(changes["tags"]) > 30:
                    raise EngineError("invalid_input", "Tags must be a bounded list")
                state["tags"] = list(dict.fromkeys(text(v, "tag", 100) for v in changes["tags"]))
            if "next_review_at" in changes:
                value = changes["next_review_at"]
                if value is not None:
                    try:
                        datetime.fromisoformat(value)
                    except (ValueError, TypeError) as exc:
                        raise EngineError("invalid_input", "Review date must be ISO-8601 or null") from exc
                state["next_review_at"] = value
            if "decisions" in changes:
                decisions = changes["decisions"]
                if not isinstance(decisions, list) or len(decisions) > 100:
                    raise EngineError("invalid_input", "Decisions must be a bounded list")
                for decision in decisions:
                    if not isinstance(decision, dict) or set(decision) - {"id", "text", "status", "evidence_ref", "supersedes"}:
                        raise EngineError("invalid_input", "Invalid decision fields")
                    identifier(decision.get("id"), "decision.id")
                    text(decision.get("text"), "decision.text", 4000)
                    if decision.get("status") not in ("proposed", "confirmed", "superseded"):
                        raise EngineError("invalid_input", "Decision status is required")
                    if decision["status"] == "confirmed":
                        text(decision.get("evidence_ref"), "decision.evidence_ref", 2000)
                    old = next((v for v in state["decisions"] if v["id"] == decision["id"]), None)
                    if old and old["status"] == "confirmed" and old["text"] != decision["text"]:
                        raise EngineError("decision_conflict", "Confirmed decisions are superseded with a new ID, not silently rewritten")
                    if old and old["status"] == "confirmed" and decision["status"] == "proposed":
                        raise EngineError("decision_conflict", "Confirmed decisions cannot silently become proposals")
                    if decision.get("supersedes"):
                        prior = next((v for v in state["decisions"] if v["id"] == decision["supersedes"]), None)
                        if not prior or decision["status"] != "confirmed":
                            raise EngineError("decision_conflict", "Supersession requires an existing decision and a confirmed replacement")
                        prior["status"] = "superseded"
                    value = {**decision, "provenance": "caller_supplied_reference"}
                    if old:
                        old.update(value)
                    else:
                        state["decisions"].append(value)
            if "criteria_results" in changes:
                values = changes["criteria_results"]
                if not isinstance(values, list) or len(values) > 100:
                    raise EngineError("invalid_input", "criteria_results must be a bounded list")
                for result in values:
                    if not isinstance(result, dict) or set(result) - {"criterion", "status", "evidence_ref", "note", "review"}:
                        raise EngineError("invalid_input", "Invalid criterion result fields")
                    criterion = next((v for v in state["completion_criteria"] if result.get("criterion") in (v["id"], v["text"])), None)
                    if not criterion or result.get("status") not in ("passed", "pending", "failed", "unknown"):
                        raise EngineError("invalid_input", "Unknown criterion or invalid result")
                    value = {**result, "criterion": criterion["id"], "claim_origin": "caller_reported"}
                    for field in ("note", "evidence_ref"):
                        if field in result:
                            value[field] = text(result[field], f"criterion_result.{field}", 4000 if field == "note" else 2000)
                    review = self._review(result.get("review"), required=bool(criterion.get("requires_independent_review") and result["status"] == "passed"))
                    if "review" in result:
                        if review is None:
                            raise EngineError("invalid_input", "Review must be an object when supplied")
                        value["review"] = review
                    if result["status"] == "passed":
                        artifact = self._artifact(state, result.get("evidence_ref"))
                        self._assert_artifact(root, state, artifact)
                        value["evidence_ref"] = f"artifact:{artifact['artifact_id']}:{artifact['revision']}"
                        workflow_context = self._workflow_criterion_context(state, criterion["id"])
                        if workflow_context is not None:
                            self._workflow_criterion_artifact(state, criterion["id"], value["evidence_ref"])
                            value["workflow_context"] = workflow_context
                    state["criteria_results"] = [v for v in state["criteria_results"] if v["criterion"] != criterion["id"]] + [value]
            return {"updated": sorted(changes)}, []
        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def record_approval(self, task_id, operation_id, expected_revision, artifact_id, artifact_revision, evidence):
        if not isinstance(evidence, dict) or set(evidence) - {"actor", "reference", "quote", "scope"}:
            raise EngineError("invalid_approval", "Approval requires a bounded evidence object")
        if evidence.get("actor") != "user":
            raise EngineError("invalid_approval", "Only an explicit user approval reference is accepted")
        for field in ("reference", "quote", "scope"):
            text(evidence.get(field), f"approval.{field}", 4000)
        payload = {"method": "record_approval", "artifact_id": artifact_id, "artifact_revision": artifact_revision, "evidence": evidence}
        def change(root, state):
            artifact = self._artifact(state, artifact_id, artifact_revision)
            source = self._assert_artifact(root, state, artifact)
            snapshot = f".workspace/approved/{artifact_id}-v{artifact_revision:03d}/{Path(artifact['path']).name}"
            approval = {"approval_id": str(uuid.uuid4()), "artifact_id": artifact_id, "artifact_revision": artifact_revision,
                        "sha256": artifact["sha256"], "snapshot_path": snapshot, "scope": evidence["scope"],
                        "evidence": copy.deepcopy(evidence), "provenance": "caller_supplied_reference", "recorded_at": now()}
            state["approvals"].append(approval)
            action = {"source": str(source) if artifact.get("external") else artifact["path"],
                      "external_source": bool(artifact.get("external")), "target": snapshot, "sha256": artifact["sha256"], "read_only": True}
            return {"approval": approval}, [action]
        return self._mutate(task_id, operation_id, expected_revision, payload, change, allow_completed=True)

    def record_external_artifact(self, task_id, operation_id, expected_revision, path, role="code", verification=None):
        role = text(role, "role", 80)
        if verification is not None and (not isinstance(verification, dict) or len(canonical_json(verification).encode()) > 8000):
            raise EngineError("invalid_input", "Verification reference must be a small object")
        source = self._source(path)
        payload = {"method": "record_external_artifact", "path": str(source), "role": role, "verification": verification}
        def change(root, state):
            if state["status"] != "active":
                raise EngineError("task_not_active", "Explicitly reactivate the task before recording new code results")
            bound = state.get("source_root")
            if not bound:
                raise EngineError("unbound_source", "Task has no explicitly bound source root")
            source_root = safe_path(bound, self.allowed_source_roots, exists=True)
            safe_path(source, [source_root], exists=True, regular=True)
            relative = source.relative_to(source_root).as_posix()
            info = file_info(source)
            previous = [a for a in state["artifacts"] if a.get("external") and a["path"] == relative]
            artifact_id = previous[0]["artifact_id"] if previous else str(uuid.uuid4())
            artifact = {"artifact_id": artifact_id, "revision": self._next_version(state, artifact_id),
                        "path": relative, "role": role, "external": True, "recorded_at": now(),
                        "verification_claim": verification, "claim_origin": "caller_reported", "integrity_status": "registered", **info}
            state["artifacts"].append(artifact)
            state["latest"][artifact_id] = artifact["revision"]
            return {"artifact": artifact, "source_unchanged_by_engine": True}, []
        return self._mutate(task_id, operation_id, expected_revision, payload, change)

    def _inspection(self, root, state):
        mismatches, artifacts = [], []
        selected_refs = None
        if state.get("schema_version") == 2:
            selected_refs = {ref for active in state["active_outputs"].values() for ref in active["outputs"].values()}
            selected_refs.update(result["evidence_ref"] for result in state["criteria_results"]
                                 if result["status"] == "passed" and result.get("evidence_ref"))
        for artifact in state["artifacts"]:
            latest = state["latest"].get(artifact["artifact_id"]) == artifact["revision"]
            required = latest if selected_refs is None else (
                f"artifact:{artifact['artifact_id']}:{artifact['revision']}" in selected_refs
                or (latest and not artifact.get("run_id")))
            try:
                path = self._artifact_path(root, state, artifact)
                actual = file_info(path) if path.exists() else None
                intact = actual is not None and actual["sha256"] == artifact["sha256"]
                if not intact and required:
                    mismatches.append({"kind": "artifact_changed" if actual else "artifact_missing", "artifact_id": artifact["artifact_id"], "artifact_revision": artifact["revision"], "path": artifact["path"]})
                artifacts.append({"id": artifact["artifact_id"], "version": artifact["revision"], "sha256": artifact["sha256"], "current_sha256": actual["sha256"] if actual else None, "latest": latest, "intact": intact,
                                  **({"required_current": required} if selected_refs is not None else {})})
            except (EngineError, OSError) as exc:
                mismatches.append({"kind": "unsafe_artifact", "artifact_id": artifact["artifact_id"], "reason": getattr(exc, "code", "unreadable_file")})
        active_source_refs = {item["ref"]: item["mode"] for item in state.get("input_bindings", {}).values()}
        for source in state["sources"]:
            if state.get("schema_version") == 2:
                ref = f"source:{source['source_id']}"
                if ref not in active_source_refs:
                    continue
                observation = self._observe_source(root, source, active_source_refs[ref])
                if observation["status"] != "available":
                    mismatches.append({"kind": "source_changed" if observation["status"] == "changed" else "source_unavailable", "source_id": source["source_id"]})
                continue
            try:
                path = self._managed(root, source["path"]) if source["mode"] == "copy" else self._source(source["origin_path"])
                if not path.exists() or file_info(path)["sha256"] != source["sha256"]:
                    mismatches.append({"kind": "source_changed", "source_id": source["source_id"]})
            except (EngineError, OSError) as exc:
                mismatches.append({"kind": "source_unavailable", "source_id": source["source_id"], "reason": getattr(exc, "code", "unreadable_file")})
        for approval in state["approvals"]:
            try:
                path = self._managed(root, approval["snapshot_path"])
                if not path.exists() or file_info(path)["sha256"] != approval["sha256"]:
                    mismatches.append({"kind": "approval_snapshot_changed", "approval_id": approval["approval_id"]})
            except (EngineError, OSError):
                mismatches.append({"kind": "approval_snapshot_unsafe", "approval_id": approval["approval_id"]})
        if state["mode"] == "expanded":
            try:
                path = self._managed(root, "WORKSPACE.md")
                projection = state.get("projection") or {}
                if not path.exists():
                    mismatches.append({"kind": "guide_missing"})
                elif file_info(path)["sha256"] != projection.get("sha256") or projection.get("revision") != state["revision"]:
                    mismatches.append({"kind": "guide_out_of_sync"})
            except (EngineError, OSError):
                mismatches.append({"kind": "guide_unsafe"})
        return mismatches, artifacts

    def verify(self, task_id):
        root = self._path(task_id)
        state = self._load(root, task_id)
        return self._verification(root, state)

    def _verification(self, root, state):
        mismatches, artifacts = self._inspection(root, state)
        if (root / ".workspace" / "pending.json").exists():
            mismatches.append({"kind": "pending_transaction"})
        return {"schema_version": 1, "task_id": state["task_id"], "revision": state["revision"], "ok": not mismatches,
                "mismatches": mismatches, "artifacts": artifacts,
                "verification_scope": "registered_file_bytes_and_guide; no_content_quality_or_user_approval_claim"}

    def _verification_summary(self, verification):
        kinds = {}
        for mismatch in verification["mismatches"]:
            kind = mismatch["kind"]
            kinds[kind] = kinds.get(kind, 0) + 1
        return {"detail": "compact", "schema_version": verification["schema_version"],
                "task_id": verification["task_id"], "revision": verification["revision"],
                "ok": verification["ok"], "mismatch_count": len(verification["mismatches"]),
                "mismatch_kinds": kinds, "artifact_count": len(verification["artifacts"]),
                "verification_scope": verification["verification_scope"],
                "read": {"tool": "resume_task", "arguments": {"task_id": verification["task_id"], "detail": "full"}}}

    def resume_task(self, task_id, operation_id=None, expected_revision=None, binding=None, adopt_changes=False, path_updates=None, reopen=False, reason=None, detail="compact"):
        if detail not in ("compact", "full"):
            raise EngineError("invalid_input", "detail must be compact or full")
        binding = binding_value(binding)
        if type(adopt_changes) is not bool:
            raise EngineError("invalid_input", "adopt_changes must be boolean")
        if path_updates is not None and (not isinstance(path_updates, dict) or len(path_updates) > 100):
            raise EngineError("invalid_input", "path_updates must map artifact IDs to relative paths")
        if type(reopen) is not bool:
            raise EngineError("invalid_input", "reopen must be boolean")
        if reopen:
            reason = text(reason, "reopen.reason", 2000)
        elif reason is not None:
            raise EngineError("invalid_input", "reason is only used with explicit reopen=true")
        if binding is None and not adopt_changes and not path_updates and not reopen:
            context = self.get_context(task_id, detail="full")
            verification = self._verification(Path(context["workspace_path"]), context["state"])
            if detail == "compact":
                return compact_context(context, verification=self._verification_summary(verification))
            context["verification"] = verification
            return context
        payload = {"method": "resume_task", "binding": binding, "adopt_changes": adopt_changes, "path_updates": path_updates, "reopen": reopen, "reason": reason}
        def change(root, state):
            if reopen:
                if state["status"] != "completed":
                    raise EngineError("invalid_transition", "Explicit reopen applies only to completed tasks")
                state.setdefault("completion_history", []).append({"completed_at": state.get("completed_at"),
                    "revision_before_reopen": state["revision"], "criteria_results": copy.deepcopy(state["criteria_results"]),
                    "reopened_at": now(), "reason": reason})
                if state.get("schema_version") == 2:
                    state["completion_history"][-1]["current_deliverable_set"] = copy.deepcopy(state.get("current_deliverable_set"))
                state.pop("completed_at", None)
                state["status"] = "active"
                if state.get("schema_version") == 2:
                    state["workflow"]["generation"] += 1
                state["next_action"] = f"Reopened explicitly: {reason}"
                for item in state["criteria_results"]:
                    item["status"] = "pending"
                    item["note"] = "Explicitly reopened; current work requires revalidation"
            if binding is not None and binding not in state["bindings"]:
                state["bindings"].append(binding)
            actions, adopted, relocated = [], [], []
            adopted_from_refs = set()
            destinations = set()
            for artifact_id, supplied_path in (path_updates or {}).items():
                relative = filename(supplied_path)
                artifact = self._artifact(state, artifact_id)
                boundary = (safe_path(state["source_root"], self.allowed_source_roots, exists=True)
                            if artifact.get("external") else root)
                destination = safe_path(boundary / relative, [boundary], exists=True, regular=True)
                key = (bool(artifact.get("external")), str(destination).casefold())
                if key in destinations:
                    raise EngineError("path_conflict", "Two artifact IDs cannot adopt the same destination")
                destinations.add(key)
                for other in state["artifacts"]:
                    if other["artifact_id"] != artifact_id and state["latest"].get(other["artifact_id"]) == other["revision"] and bool(other.get("external")) == bool(artifact.get("external")) and other["path"].casefold() == relative.casefold():
                        raise EngineError("path_conflict", "Destination belongs to a different artifact")
                if file_info(destination)["sha256"] != artifact["sha256"]:
                    raise EngineError("artifact_changed", "Path adoption requires identical registered bytes; changed content is a separate operation")
                previous = artifact["path"]
                artifact.setdefault("previous_paths", []).append(previous)
                artifact["path"] = relative
                if not artifact.get("external"):
                    logical = Path(relative)
                    suffix = f"-v{artifact['revision']:03d}"
                    stem = logical.stem[:-len(suffix)] if logical.stem.endswith(suffix) else logical.stem
                    artifact["logical_name"] = logical.with_name(stem + logical.suffix).as_posix()
                relocated.append({"artifact_id": artifact_id, "previous_path": previous, "path": relative})
            if adopt_changes:
                for artifact in list(state["artifacts"]):
                    if state["latest"].get(artifact["artifact_id"]) != artifact["revision"]:
                        continue
                    path = self._artifact_path(root, state, artifact)
                    if not path.exists():
                        raise EngineError("missing_file", "Missing files require an explicit corrected path; rename is not guessed")
                    info = file_info(path)
                    if info["sha256"] == artifact["sha256"]:
                        continue
                    new = {**artifact, **info, "revision": self._next_version(state, artifact["artifact_id"]), "recorded_at": now(), "integrity_status": "adopted_external_edit"}
                    if state.get("schema_version") == 2:
                        adopted_from_refs.add(f"artifact:{artifact['artifact_id']}:{artifact['revision']}")
                        if new.get("run_id"):
                            new["adopted_from"] = {"run_id": new.pop("run_id"), "output_slot": new.pop("output_slot", None),
                                                   "artifact_ref": f"artifact:{artifact['artifact_id']}:{artifact['revision']}"}
                    if new.get("external"):
                        new["verification_claim"] = None
                    artifact["integrity_status"] = "historical_bytes_changed"
                    if not artifact.get("external"):
                        logical = Path(artifact["logical_name"])
                        new["path"] = logical.with_name(f"{logical.stem}-v{new['revision']:03d}{logical.suffix}").as_posix()
                        if self._managed(root, new["path"]).exists():
                            raise EngineError("name_conflict", "Adoption destination already exists")
                        actions.append({"source": artifact["path"], "target": new["path"], "sha256": info["sha256"]})
                    state["artifacts"].append(new)
                    state["latest"][new["artifact_id"]] = new["revision"]
                    adopted.append({"artifact_id": new["artifact_id"], "revision": new["revision"], "approval": "not_inherited"})
                # Evidence tied to changed old working files is no longer valid.
                changed_ids = {item["artifact_id"] for item in adopted}
                for result in state["criteria_results"]:
                    if any(f"artifact:{aid}:" in result.get("evidence_ref", "") for aid in changed_ids):
                        result["status"] = "pending"
                        result["note"] = "Working bytes changed; revalidate explicitly"
                if state.get("schema_version") == 2 and adopted_from_refs:
                    retired_steps = [step_id for step_id, active in state["active_outputs"].items()
                                     if adopted_from_refs.intersection(active["outputs"].values())]
                    for step_id in retired_steps:
                        # Historical runs retain their actual original refs.
                        # An adopted user edit is preserved as a new artifact,
                        # not re-labelled as that old generated/checked result.
                        del state["active_outputs"][step_id]
                        self._invalidate_workflow_criteria(state, step_id)
                    if retired_steps:
                        state["workflow"]["generation"] += 1
            return {"binding_added": binding is not None, "adopted": adopted, "path_updates": relocated, "reopened": reopen}, actions
        result = self._mutate(task_id, operation_id, expected_revision, payload, change, allow_completed=reopen or not adopt_changes)
        verification = self.verify(task_id)
        result["verification"] = self._verification_summary(verification) if detail == "compact" else verification
        return result

    def complete_task(self, task_id, operation_id, expected_revision):
        payload = {"method": "complete_task"}
        def change(root, state):
            if state["status"] != "active":
                raise EngineError("task_not_active", "Only an active task can be completed")
            if state.get("attention") is not None:
                raise EngineError("attention_pending", "Resolve the recorded attention explicitly before completion", {"attention": state["attention"]})
            mismatches, _ = self._inspection(root, state)
            if mismatches:
                raise EngineError("verification_failed", "Registered files or guide need reconciliation", {"mismatches": mismatches})
            if not state["artifacts"]:
                raise EngineError("no_artifact", "Completion requires a committed artifact or a registered source-code result")
            if any(r["status"] == "reserved" for r in state["reservations"].values()):
                raise EngineError("pending_output", "Uncommitted output reservations remain")
            failures = []
            for criterion in state["completion_criteria"]:
                result = next((r for r in state["criteria_results"] if r["criterion"] == criterion["id"]), None)
                if not result or result["status"] != "passed":
                    failures.append(criterion["id"])
                    continue
                self._review(result.get("review"), required=bool(criterion.get("requires_independent_review")))
                artifact = self._artifact(state, result["evidence_ref"])
                self._assert_artifact(root, state, artifact)
                if criterion["requires_approval"] and not any(a["artifact_id"] == artifact["artifact_id"] and a["artifact_revision"] == artifact["revision"] and a["sha256"] == artifact["sha256"] and (not criterion.get("approval_scope") or a["scope"] == criterion["approval_scope"]) for a in state["approvals"]):
                    failures.append(criterion["id"])
            if failures:
                raise EngineError("criteria_pending", "Completion criteria are not satisfied", {"criteria": failures})
            if state.get("schema_version") == 2:
                self._workflow_complete(root, state)
            state["status"] = "completed"
            state["next_action"] = "Completed against recorded criteria; delivery and future use are separate"
            state["completed_at"] = now()
            return {"status": "completed", "content_claims": "caller_reported_with_artifact_references"}, []
        return self._mutate(task_id, operation_id, expected_revision, payload, change)
