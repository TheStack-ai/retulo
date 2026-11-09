"""Opt-in Git associations with independent revisions and recoverable intents.

Task identity/source_root are never rewritten. These cooperative locks do not
serialize Git commands run by other programs. No background action or force flag.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from pathlib import Path
import uuid

from .fs import EngineError, atomic_json, digest, process_lock, read_json, safe_path
from .git_backend import GitBackend


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, name):
    if not isinstance(value, str) or not value or len(value.encode()) > 4096 or "\x00" in value:
        raise EngineError("invalid_input", f"{name} must be a bounded nonempty string")
    return value


class GitWorkspaces:
    def __init__(self, engine, backend=None):
        self.engine = engine
        self.backend = backend or GitBackend()

    def _paths(self, task_id):
        root = self.engine._path(task_id)
        return root, safe_path(root / ".workspace/git.json", [root], regular=True)

    def _load(self, task_id):
        root, path = self._paths(task_id)
        value = read_json(path)
        if value is None:
            value = {"schema_version": 1, "task_id": task_id, "git_revision": 0,
                     "binding": None, "operations": {}, "pending": None}
        if (not isinstance(value, dict) or set(value) != {"schema_version", "task_id", "git_revision", "binding", "operations", "pending"}
                or value["schema_version"] != 1 or value["task_id"] != task_id
                or type(value["git_revision"]) is not int or value["git_revision"] < 0
                or not isinstance(value["operations"], dict)
                or (value["binding"] is not None and not isinstance(value["binding"], dict))
                or (value["pending"] is not None and not isinstance(value["pending"], dict))):
            raise EngineError("invalid_git_state", "Unsupported or malformed Git sidecar")
        binding = value["binding"]
        required = {"association_id", "path", "repo_path", "common_dir", "git_dir", "managed", "removed", "created_at", "initial_head"}
        if binding is not None:
            if (not required <= set(binding) or set(binding) - required - {"removed_at", "last_head"}
                    or any(not isinstance(binding[k], str) or not binding[k] for k in required - {"managed", "removed", "initial_head"})
                    or type(binding["managed"]) is not bool or type(binding["removed"]) is not bool
                    or binding["initial_head"] is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(binding["initial_head"]))):
                raise EngineError("invalid_git_state", "Malformed Git association")
        pending = value["pending"]
        if pending is not None:
            if (set(pending) != {"operation_id", "payload_digest", "kind", "context", "started_at"}
                    or any(not isinstance(pending[k], str) or not pending[k] for k in ("operation_id", "payload_digest", "kind", "started_at"))
                    or pending["kind"] not in {"bind", "create_worktree", "integrate", "remove"}
                    or not isinstance(pending["context"], dict) or not isinstance(pending["context"].get("common_dir"), str)):
                raise EngineError("invalid_git_state", "Malformed pending Git intent")
            context = pending["context"]
            keys = ({"common_dir", "repo_path", "path", "branch", "base_head", "association_id"} if pending["kind"] == "create_worktree"
                    else {"common_dir", "observation", "association_id"} if pending["kind"] == "bind"
                    else {"common_dir", "plan"})
            if set(context) != keys:
                raise EngineError("invalid_git_state", "Malformed Git intent context")
            if pending["kind"] == "create_worktree" and any(not isinstance(context[k], str) or not context[k] for k in keys):
                raise EngineError("invalid_git_state", "Malformed creation intent")
            if pending["kind"] in {"integrate", "remove"}:
                self._validate_plan(context["plan"], task_id, pending["kind"])
        for key, receipt in value["operations"].items():
            if (not isinstance(key, str) or not isinstance(receipt, dict) or set(receipt) != {"payload_digest", "result"}
                    or not isinstance(receipt["payload_digest"], str) or not isinstance(receipt["result"], dict)):
                raise EngineError("invalid_git_state", "Malformed Git receipt")
        return root, path, value

    def assert_completable(self, task_id):
        if self._load(task_id)[2]["pending"]:
            raise EngineError("git_recovery_needed", "Resolve pending Git action before completing this task")

    def _validate_context(self, state, context, kind):
        safe_path(context["common_dir"], self.engine.allowed_source_roots, exists=True)
        if kind == "create_worktree":
            self._allowed(context["path"], True, exists=False)
            repo = self._inspect(context["repo_path"])
            if repo["common_dir"] != context["common_dir"] or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", context["base_head"]):
                raise EngineError("git_identity_changed", "Creation intent no longer identifies this repository")
        elif kind in {"integrate", "remove"}:
            plan = context["plan"]
            binding = state["binding"]
            source = plan["source"]
            if (not binding or binding["association_id"] != plan["association_id"]
                    or any(binding[k] != source[k] for k in ("path", "common_dir", "git_dir"))):
                raise EngineError("git_identity_changed", "Git intent does not match its task association")
            self._allowed(source["path"], binding["managed"], exists=False)
            target = self._inspect(plan["target"]["path"])
            if target["common_dir"] != context["common_dir"] or target["git_dir"] != plan["target"]["git_dir"]:
                raise EngineError("git_identity_changed", "Git target identity changed")

    def _allowed(self, path, managed=False, exists=True):
        roots = getattr(self.engine, "allowed_git_worktree_roots", []) if managed else self.engine.allowed_source_roots
        value = safe_path(_text(str(path), "path"), roots, exists=exists)
        if exists and not value.is_dir():
            raise EngineError("invalid_git_path", "Git path must be a directory")
        return value

    def _inspect(self, path, managed=False):
        path = self._allowed(path, managed)
        observed = self.backend.status(path)
        if Path(observed["path"]) != path:
            raise EngineError("invalid_git_path", "Bind the exact worktree root, not a subdirectory")
        safe_path(observed["common_dir"], self.engine.allowed_source_roots, exists=True)
        return observed

    def _binding_status(self, binding):
        if not binding or binding.get("removed"):
            raise EngineError("git_not_bound", "Task has no live Git worktree association")
        observed = self._inspect(binding["path"], binding["managed"])
        if observed["common_dir"] != binding["common_dir"] or observed["git_dir"] != binding["git_dir"]:
            raise EngineError("git_identity_changed", "Worktree no longer belongs to the bound repository")
        return observed

    @staticmethod
    def _clean(observed):
        if (not observed.get("head") or not observed.get("branch") or observed.get("dirty")
                or observed.get("ignored") or observed.get("locked")
                or observed.get("unsafe_index_flags") or observed.get("operation_in_progress")
                or observed.get("submodules")):
            raise EngineError("git_unsafe_state", "Operation requires an unlocked clean branch without ignored files, submodules or unsafe index flags")

    @staticmethod
    def _snapshot(observed):
        return {key: observed.get(key) for key in ("path", "common_dir", "git_dir", "head", "branch", "dirty", "staged", "modified", "untracked", "ignored", "conflicts", "locked", "unsafe_index_flags", "operation_in_progress", "submodules")}

    def status(self, task_id):
        root, _, state = self._load(task_id)
        task = self.engine._load(root, task_id)
        result = {"task_id": task_id, "task_revision": task["revision"], "git_revision": state["git_revision"],
                  "binding": copy.deepcopy(state["binding"]), "observed_at": _now(),
                  "pending": copy.deepcopy(state["pending"]), "status": "unbound"}
        if state["binding"]:
            if state["binding"].get("removed"):
                result["status"] = "removed"
            else:
                try:
                    result["observation"] = self._binding_status(state["binding"])
                    result["status"] = "observed"
                except (EngineError, OSError) as exc:
                    result.update(status="unknown", error={"code": getattr(exc, "code", "git_unavailable"), "message": str(exc)})
        if state["pending"]:
            result["status"] = "recovery_needed"
        return result

    def _mutate(self, task_id, operation_id, expected_git_revision, payload, prepare, execute, recover=None):
        _text(operation_id, "operation_id")
        if type(expected_git_revision) is not int or expected_git_revision < 0:
            raise EngineError("invalid_input", "expected_git_revision must be a nonnegative integer")
        root, _ = self._paths(task_id)
        task_lock = safe_path(root / ".workspace/lock", [root], regular=True)
        with process_lock(task_lock):
            self.engine._recover(root)
            task = self.engine._load(root, task_id)
            _, path, state = self._load(task_id)
            fingerprint = digest(payload)
            old = state["operations"].get(operation_id)
            if old:
                if old.get("payload_digest") != fingerprint:
                    raise EngineError("idempotency_conflict", "Git operation ID has different arguments")
                return {**copy.deepcopy(old["result"]), "replayed": True}
            if task["status"] != "active":
                raise EngineError("task_not_active", "Git mutations require an active task")
            if expected_git_revision != state["git_revision"]:
                raise EngineError("git_revision_conflict", "Read current Git state before mutation")
            if len(state["operations"]) >= 10000:
                raise EngineError("operation_limit", "Git receipt limit reached")
            pending = state["pending"]
            if pending and (pending.get("operation_id") != operation_id or pending.get("payload_digest") != fingerprint):
                raise EngineError("git_recovery_needed", "Retry the pending Git operation with the same ID and arguments")
            context = pending["context"] if pending else prepare(state)
            common = context["common_dir"]
            safe_path(common, self.engine.allowed_source_roots, exists=True)
            repo_lock = safe_path(self.engine.state_home / "git-locks" / (digest(common) + ".lock"), [self.engine.state_home], regular=True)
            with process_lock(repo_lock):
                self._validate_context(state, context, payload["method"])
                if pending:
                    if recover is None:
                        raise EngineError("git_recovery_needed", "Inspect the pending operation before retry")
                    result, binding = recover(state, context)
                else:
                    # Recompute preconditions under the repository cooperative lock.
                    context = prepare(state)
                    if context["common_dir"] != common:
                        raise EngineError("git_identity_changed", "Repository changed while acquiring its lock")
                    self._validate_context(state, context, payload["method"])
                    state["pending"] = {"operation_id": operation_id, "payload_digest": fingerprint,
                                        "kind": payload["method"], "context": context, "started_at": _now()}
                    atomic_json(path, state)
                    result, binding = execute(state, context)
                state["binding"] = binding
                state["git_revision"] += 1
                state["pending"] = None
                result = {"task_id": task_id, "git_revision": state["git_revision"], **result}
                state["operations"][operation_id] = {"payload_digest": fingerprint, "result": result}
                atomic_json(path, state)
                return result

    def bind(self, task_id, operation_id, expected_git_revision, path):
        path = _text(path, "path")
        def prepare(state):
            if state["binding"] and not state["binding"].get("removed"):
                raise EngineError("git_already_bound", "Task already has a Git association")
            observed = self._inspect(path)
            return {"common_dir": observed["common_dir"], "observation": observed,
                    "association_id": str(uuid.uuid4())}
        def execute(state, context):
            observed = self._inspect(path)
            if observed["common_dir"] != context["common_dir"]:
                raise EngineError("git_identity_changed", "Repository changed before binding")
            binding = {"association_id": context["association_id"], "path": observed["path"],
                       "repo_path": observed["path"], "common_dir": observed["common_dir"],
                       "git_dir": observed["git_dir"], "managed": False, "removed": False,
                       "created_at": _now(), "initial_head": observed["head"]}
            return {"binding": binding, "observation": observed}, binding
        return self._mutate(task_id, operation_id, expected_git_revision,
                            {"method": "bind", "path": path}, prepare, execute, execute)

    def create_worktree(self, task_id, operation_id, expected_git_revision, repo_path, path, branch, base="HEAD"):
        repo_path, path, branch, base = (_text(v, n) for v, n in ((repo_path,"repo_path"),(path,"path"),(branch,"branch"),(base,"base")))
        def prepare(state):
            if state["binding"] and not state["binding"].get("removed"):
                raise EngineError("git_already_bound", "Task already has a live Git association")
            repo = self._inspect(repo_path)
            target = self._allowed(path, True, exists=False)
            roots = [Path(p).resolve() for p in getattr(self.engine, "allowed_git_worktree_roots", [])]
            if target in roots or target.exists():
                raise EngineError("git_path_conflict", "Worktree requires a new child path in its configured root")
            for entry in self.backend.list_worktrees(repo_path):
                existing = Path(entry.get("path", entry.get("worktree", ""))).resolve()
                if target == existing or target in existing.parents or existing in target.parents:
                    raise EngineError("git_path_conflict", "Worktree paths must not overlap")
            for existing in (Path(repo["common_dir"]), self.engine.workspace_home, self.engine.state_home):
                if target == existing or target in existing.parents or existing in target.parents:
                    raise EngineError("git_path_conflict", "Worktree overlaps repository metadata or Retulo state")
            # Pin base without shell or revision-expression execution in a later mutation.
            base_head = self.backend.resolve_commit(repo_path, base)
            self.backend.preflight_create(repo_path, str(target), branch, base_head)
            return {"common_dir": repo["common_dir"], "repo_path": repo["path"], "path": str(target),
                    "branch": branch, "base_head": base_head, "association_id": str(uuid.uuid4())}
        def finish(context, observed):
            if observed["common_dir"] != context["common_dir"] or observed["head"] != context["base_head"] or observed["branch"] != context["branch"]:
                raise EngineError("git_recovery_needed", "Created worktree differs from the pinned intent")
            binding = {"association_id": context["association_id"], "path": observed["path"],
                       "repo_path": context["repo_path"], "common_dir": observed["common_dir"],
                       "git_dir": observed["git_dir"], "managed": True, "removed": False,
                       "created_at": _now(), "initial_head": observed["head"]}
            return {"binding": binding, "observation": observed}, binding
        def execute(state, context):
            observed = self.backend.create_worktree(context["repo_path"], context["path"], context["branch"], context["base_head"])
            return finish(context, observed)
        def recover(state, context):
            if not Path(context["path"]).exists():
                # A partially created branch cannot safely be reused. Backend refuses it.
                return execute(state, context)
            observed = self._inspect(context["path"], True)
            self._clean(observed)
            return finish(context, observed)
        return self._mutate(task_id, operation_id, expected_git_revision,
                            {"method":"create_worktree","repo_path":repo_path,"path":path,"branch":branch,"base":base}, prepare, execute, recover)

    def _plan(self, task_id, target_path, kind):
        root, _, state = self._load(task_id)
        if state["pending"]:
            raise EngineError("git_recovery_needed", "Resolve pending Git operation before planning")
        source = self._binding_status(state["binding"])
        target = self._inspect(target_path)
        if source["path"] == target["path"] or source["common_dir"] != target["common_dir"]:
            raise EngineError("git_target_mismatch", "Target must be another worktree of the same repository")
        self._clean(source)
        self._clean(target)
        if kind == "remove":
            if not state["binding"]["managed"]:
                raise EngineError("git_not_managed", "Only Retulo-created worktrees can be removed")
            eligible = self.backend.is_ancestor(source["path"], source["head"], target["head"])
        else:
            eligible = self.backend.is_ancestor(source["path"], target["head"], source["head"])
        plan = {"schema_version":1,"kind":kind,"task_id":task_id,"git_revision":state["git_revision"],
                "association_id":state["binding"]["association_id"], "source":self._snapshot(source),
                "target":self._snapshot(target),"eligible":eligible}
        plan["fingerprint"] = digest(plan)
        return plan

    def prepare_integration(self, task_id, target_path):
        return self._plan(task_id, target_path, "integrate")

    def prepare_remove(self, task_id, target_path):
        return self._plan(task_id, target_path, "remove")

    @staticmethod
    def _validate_plan(plan, task_id, kind):
        if not isinstance(plan, dict) or set(plan) != {"schema_version","kind","task_id","git_revision","association_id","source","target","eligible","fingerprint"}:
            raise EngineError("invalid_git_plan", "Plan must be the complete Git prepare result")
        if plan.get("kind") != kind or plan.get("task_id") != task_id or not isinstance(plan.get("target"), dict):
            raise EngineError("invalid_git_plan", "Plan does not match this operation")
        expected = {"path", "common_dir", "git_dir", "head", "branch", "dirty", "staged", "modified", "untracked", "ignored", "conflicts", "locked", "unsafe_index_flags", "operation_in_progress", "submodules"}
        if (type(plan["schema_version"]) is not int or plan["schema_version"] != 1
                or type(plan["git_revision"]) is not int or type(plan["eligible"]) is not bool
                or not isinstance(plan["association_id"], str)):
            raise EngineError("invalid_git_plan", "Malformed plan identity")
        for snapshot in (plan["source"], plan["target"]):
            if not isinstance(snapshot, dict) or set(snapshot) != expected:
                raise EngineError("invalid_git_plan", "Malformed plan snapshot")
            for key in ("path", "common_dir", "git_dir", "head", "branch"):
                _text(snapshot[key], key)
        if plan["fingerprint"] != digest({k:v for k,v in plan.items() if k != "fingerprint"}):
            raise EngineError("invalid_git_plan", "Plan fingerprint is invalid")

    def _apply_plan(self, task_id, operation_id, expected_git_revision, plan, kind):
        self._validate_plan(plan, task_id, kind)
        target_path = plan["target"]["path"]
        def prepare(state):
            current = self._plan(task_id, target_path, kind)
            if current != plan:
                raise EngineError("git_plan_stale", "Git state changed; prepare a new plan")
            if not current["eligible"]:
                raise EngineError("git_not_integrated" if kind == "remove" else "git_not_fast_forward", "Branch history does not satisfy this operation")
            return {"common_dir": current["source"]["common_dir"], "plan": current}
        def execute(state, context):
            selected = context["plan"]
            source, target = selected["source"], selected["target"]
            if kind == "integrate":
                observed = self.backend.integrate(source["path"], target["path"], source["head"], target["head"])
                return {"integrated": True, "observation": observed, "source_preserved": True}, state["binding"]
            self.backend.remove_worktree(target["path"], source["path"], source["head"])
            binding = {**state["binding"], "removed": True, "removed_at": _now(), "last_head": source["head"]}
            return {"removed": True, "branch_preserved": True, "binding": binding}, binding
        def recover(state, context):
            selected = context["plan"]
            source, target = selected["source"], selected["target"]
            target_now = self._inspect(target["path"])
            self._clean(target_now)
            if target_now["common_dir"] != context["common_dir"] or target_now["branch"] != target["branch"]:
                raise EngineError("git_recovery_needed", "Target identity changed during recovery")
            if kind == "integrate":
                source_now = self._binding_status(state["binding"])
                self._clean(source_now)
                if self._snapshot(source_now) != source:
                    raise EngineError("git_recovery_needed", "Source changed after interrupted integration")
                if target_now["head"] == source["head"]:
                    return {"integrated":True,"observation":target_now,"source_preserved":True,"recovered":True}, state["binding"]
                if target_now["head"] == target["head"]:
                    return execute(state, context)
            else:
                if target_now["head"] != target["head"]:
                    raise EngineError("git_recovery_needed", "Target changed after interrupted removal")
                entries = self.backend.list_worktrees(target["path"])
                exists = any(entry.get("path", entry.get("worktree")) == source["path"] for entry in entries)
                if not Path(source["path"]).exists() and not exists:
                    if self.backend.resolve_commit(target["path"], "refs/heads/" + source["branch"]) != source["head"]:
                        raise EngineError("git_recovery_needed", "Original branch was not preserved")
                    binding = {**state["binding"], "removed":True,"removed_at":_now(),"last_head":source["head"]}
                    return {"removed":True,"branch_preserved":True,"binding":binding,"recovered":True}, binding
                if self._snapshot(self._binding_status(state["binding"])) == source:
                    return execute(state, context)
            raise EngineError("git_recovery_needed", "Interrupted Git result cannot be safely reconciled")
        return self._mutate(task_id, operation_id, expected_git_revision,
                            {"method":kind,"plan":copy.deepcopy(plan)}, prepare, execute, recover)

    def integrate(self, task_id, operation_id, expected_git_revision, plan):
        return self._apply_plan(task_id, operation_id, expected_git_revision, plan, "integrate")

    def remove_worktree(self, task_id, operation_id, expected_git_revision, plan):
        return self._apply_plan(task_id, operation_id, expected_git_revision, plan, "remove")

    def abandon_pending(self, task_id, operation_id, expected_git_revision, pending_operation_id):
        """Clear only a proven unapplied intent. Never reset, remove or roll back Git."""
        _text(operation_id, "operation_id")
        _text(pending_operation_id, "pending_operation_id")
        if operation_id == pending_operation_id:
            raise EngineError("invalid_input", "Abandonment requires a distinct operation ID")
        root, _ = self._paths(task_id)
        payload = {"method":"abandon_pending", "pending_operation_id":pending_operation_id}
        fingerprint = digest(payload)
        with process_lock(safe_path(root / ".workspace/lock", [root], regular=True)):
            task = self.engine._load(root, task_id)
            _, path, state = self._load(task_id)
            old = state["operations"].get(operation_id)
            if old:
                if old["payload_digest"] != fingerprint:
                    raise EngineError("idempotency_conflict", "Abandonment ID has different arguments")
                return {**copy.deepcopy(old["result"]), "replayed":True}
            if task["status"] != "active":
                raise EngineError("task_not_active", "Git reconciliation requires an active task")
            if type(expected_git_revision) is not int or expected_git_revision != state["git_revision"]:
                raise EngineError("git_revision_conflict", "Read current Git revision")
            pending = state["pending"]
            if not pending or pending["operation_id"] != pending_operation_id:
                raise EngineError("git_pending_mismatch", "Pending operation does not match")
            context, kind = pending["context"], pending["kind"]
            common = context["common_dir"]
            safe_path(common, self.engine.allowed_source_roots, exists=True)
            lock = safe_path(self.engine.state_home / "git-locks" / (digest(common) + ".lock"), [self.engine.state_home], regular=True)
            with process_lock(lock):
                self._validate_context(state, context, kind)
                if kind == "create_worktree":
                    # Re-running all no-write preflight checks proves target and branch absent.
                    self.backend.preflight_create(context["repo_path"], context["path"], context["branch"], context["base_head"])
                elif kind in {"integrate", "remove"}:
                    plan = context["plan"]
                    source = self._binding_status(state["binding"])
                    target = self._inspect(plan["target"]["path"])
                    if self._snapshot(source) != plan["source"] or self._snapshot(target) != plan["target"]:
                        raise EngineError("git_recovery_needed", "Git may have changed; intent cannot be abandoned")
                state["git_revision"] += 1
                result = {"task_id":task_id,"git_revision":state["git_revision"],"abandoned":True,
                          "pending_operation_id":pending_operation_id,"git_changed":False}
                state["operations"][pending_operation_id] = {"payload_digest":pending["payload_digest"],"result":result}
                state["operations"][operation_id] = {"payload_digest":fingerprint,"result":result}
                state["pending"] = None
                atomic_json(path, state)
                return result
