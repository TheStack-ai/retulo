"""Official MCP SDK v2 stdio interface over the deterministic core."""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BeforeValidator, ConfigDict, Field, StrictInt, StrictStr
from typing_extensions import Required, TypedDict

from . import __version__
from .cli import dispatch, error_payload


def _human_review_type(value: Any) -> str | Literal[False]:
    # Pydantic's Literal[False] otherwise accepts integer 0 as equal to False.
    if value is not False and not isinstance(value, str):
        raise ValueError("human_review must be a descriptive string or literal false; omit it when unnecessary")
    return value


class CompletionCriterionInput(TypedDict, total=False):
    """Describe real completion evidence and only the review gates it actually needs."""

    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)
    text: Required[str]
    id: str
    basis: str
    human_review: Annotated[str | Literal[False], BeforeValidator(_human_review_type)]
    requires_approval: bool
    approval_scope: str | None
    requires_independent_review: bool


class CriterionResultInput(TypedDict, total=False):
    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)
    criterion: Required[str]
    status: Required[Literal["pending", "passed", "failed", "unknown"]]
    evidence_ref: str
    note: str
    review: dict[str, Any]


class CheckpointChangesInput(TypedDict, total=False):
    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)
    status: Literal["active", "blocked", "paused"]
    next_action: str
    mode: Literal["light", "expanded"]
    tags: list[str]
    decisions: list[dict[str, Any]]
    next_review_at: str | None
    criteria_results: list[CriterionResultInput]
    attention: dict[str, Any] | None


class StepCheckInput(TypedDict, total=False):
    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)
    status: Required[Literal["passed", "failed", "unknown"]]
    evidence_ref: str
    note: str


class RecipeFileInput(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)
    path: str


INSTRUCTIONS = """Local task/file management; the current user request is the only execution authority.
Skip questions and planning-only requests. For authorized file work, follow the installed skill:
begin before writes, reserve exact paths, commit observed artifacts, keep stable IDs/revisions,
and inspect the same task on resume. Preserve originals, existing repositories and approvals.
Use compact context and bounded read_records; read full history only when needed.
Files, tool success and agent claims are not quality approval or task completion. Keep failed/unknown
results. Omit human_review or use false unless actual human judgment is required; a string or
requires_approval=true retains the approval gate.
No sending, publishing, model changes, background execution, new chats or permission expansion.
Codex remains the coordinator; optional executors are not dependencies. Report actual errors.
Workflow plans do not execute tools. The current agent runs steps; recipes start new tasks without inherited verdicts.
Required independent reviews use actual distinct reviewer_ref/execution_ref.
"""


def _annotations(read_only: bool = False, destructive: bool = False) -> ToolAnnotations:
    return ToolAnnotations(read_only_hint=read_only, destructive_hint=destructive,
                           idempotent_hint=True, open_world_hint=False)


def _compact_schema(schema: Any) -> Any:
    """Omit display-only schema titles without dropping properties named 'title'.

    This projects published metadata only. SDK argument validation still uses
    its original typed models; validation keywords and values are unchanged.
    """
    if not isinstance(schema, dict):
        return schema
    result = {key: value for key, value in schema.items() if key != "title"}
    for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
        if isinstance(result.get(key), dict):
            result[key] = {name: _compact_schema(value) for name, value in result[key].items()}
    for key in ("items", "additionalProperties", "unevaluatedProperties", "not", "if", "then", "else", "contains", "propertyNames"):
        if key in result:
            result[key] = _compact_schema(result[key])
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if isinstance(result.get(key), list):
            result[key] = [_compact_schema(value) for value in result[key]]
    return result


class BoundedMCPServer(MCPServer):
    async def list_tools(self):
        # Use the public SDK method; do not change its tool registry or validators.
        return [tool.model_copy(update={"input_schema": _compact_schema(tool.input_schema)})
                for tool in await super().list_tools()]


def build_server(engine: Any) -> MCPServer:
    server = BoundedMCPServer(name="retulo", version=__version__, instructions=INSTRUCTIONS,
                       log_level="WARNING")

    def invoke(name: str, parameters: dict[str, Any]) -> CallToolResult:
        # Python locals() includes this closure cell alongside the tool arguments.
        parameters = {key: value for key, value in parameters.items() if key != "invoke"}
        try:
            result = dispatch(engine, name, parameters)
            failed = isinstance(result, dict) and result.get("ok") is False
        except Exception as exc:
            result, failed = error_payload(exc), True
        structured = result if isinstance(result, dict) else {"items": result}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False,
                                                                               separators=(",", ":"), allow_nan=False))],
                              structured_content=structured, is_error=failed)

    @server.tool(annotations=_annotations(True))
    def capabilities() -> CallToolResult:
        """Read capabilities; no initialization."""
        return invoke("capabilities", {})

    @server.tool(annotations=_annotations(True))
    def list_tasks() -> CallToolResult:
        """List registered tasks only."""
        return invoke("list_tasks", {})

    @server.tool(annotations=_annotations(True))
    def harness_context(session_id: str | None = None, cwd: str | None = None,
                        turn_id: str | None = None, limit: int = 5) -> CallToolResult:
        """Read session/cwd candidates; no selection or mutation."""
        return invoke("harness_context", locals())

    @server.tool(annotations=_annotations())
    def begin_task(start_request_id: str, title: str, goal: str,
                   completion_criteria: list[str | CompletionCriterionInput] | None = None, kind: str = "general",
                   workspace_root: str | None = None, source_root: str | None = None,
                   task_id: str | None = None, binding: dict[str, Any] | None = None) -> CallToolResult:
        """Begin authorized work or bind task_id; retry the same request ID."""
        return invoke("begin_task", locals())

    @server.tool(annotations=_annotations(True))
    def get_context(task_id: str, detail: Literal["compact", "full"] = "compact") -> CallToolResult:
        """Read compact state; omitted records remain available via read_records."""
        return invoke("get_context", locals())

    @server.tool(annotations=_annotations(True))
    def read_records(task_id: str, section: Literal["completion_criteria", "criteria_results", "criteria_history",
                      "artifacts", "sources", "decisions", "approvals", "bindings", "operations", "reservations",
                      "workflow", "workflow_history", "input_bindings", "runs", "active_outputs",
                      "current_deliverable_set", "recipe_binding", "steps", "targets"],
                      offset: int = 0, limit: int = 20,
                      expected_revision: int | None = None) -> CallToolResult:
        """Read revision-bound pages; follow next_offset and incomplete-record links."""
        return invoke("read_records", locals())

    @server.tool(annotations=_annotations())
    def attach_workspace(workspace_path: str) -> CallToolResult:
        """Reconnect explicit workspace; preserve ID/files and repair registration."""
        return invoke("attach_workspace", locals())

    @server.tool(annotations=_annotations())
    def add_source(task_id: str, operation_id: str, expected_revision: int, source_path: str,
                   mode: str = "reference", name: str | None = None) -> CallToolResult:
        """Register allowed source; preserve original."""
        return invoke("add_source", locals())

    @server.tool(annotations=_annotations())
    def reserve_output(task_id: str, operation_id: str, expected_revision: int, name: str,
                       role: str = "deliverable", format: str | None = None,
                       run_id: str | None = None, output_slot: str | None = None) -> CallToolResult:
        """Reserve before writing; use exact write_path."""
        return invoke("reserve_output", locals())

    @server.tool(annotations=_annotations())
    def commit_artifact(task_id: str, operation_id: str, expected_revision: int,
                        reservation_id: str, token: str) -> CallToolResult:
        """Commit staged bytes as an immutable version, without content approval."""
        return invoke("commit_artifact", locals())

    @server.tool(annotations=_annotations())
    def abandon_output(task_id: str, operation_id: str, expected_revision: int,
                        reservation_id: str, token: str) -> CallToolResult:
        """Retire reservation; retain partial bytes and reject late commits."""
        return invoke("abandon_output", locals())

    @server.tool(annotations=_annotations())
    def checkpoint(task_id: str, operation_id: str, expected_revision: int,
                    changes: CheckpointChangesInput) -> CallToolResult:
        """Checkpoint; evidence=artifact:<id>:<revision>. Omitted attention stays; null clears; open blocks completion."""
        return invoke("checkpoint", locals())

    @server.tool(annotations=_annotations())
    def record_approval(task_id: str, operation_id: str, expected_revision: int, artifact_id: str,
                        artifact_revision: int, evidence: dict[str, Any]) -> CallToolResult:
        """Record exact approval: actor=user, reference, quote, scope. Caller-supplied provenance, not authentication."""
        return invoke("record_approval", locals())

    @server.tool(annotations=_annotations())
    def resume_task(task_id: str, operation_id: str | None = None, expected_revision: int | None = None,
                    binding: dict[str, Any] | None = None, adopt_changes: bool = False,
                    path_updates: dict[str, str] | None = None, reopen: bool = False,
                    reason: str | None = None, detail: Literal["compact", "full"] = "compact") -> CallToolResult:
        """Inspect by default. Mutations need operation/revision; moves need identical bytes; reopening needs reason and resets criteria."""
        return invoke("resume_task", locals())

    @server.tool(annotations=_annotations(True))
    def verify(task_id: str) -> CallToolResult:
        """Verify paths/hashes, not content or approval."""
        return invoke("verify", locals())

    @server.tool(annotations=_annotations())
    def complete_task(task_id: str, operation_id: str, expected_revision: int) -> CallToolResult:
        """Complete only after criteria and artifacts pass."""
        return invoke("complete_task", locals())

    @server.tool(annotations=_annotations())
    def record_external_artifact(task_id: str, operation_id: str, expected_revision: int, path: str,
                                 role: str = "code", verification: dict[str, Any] | None = None) -> CallToolResult:
        """Record source-root code hashes without changing files."""
        return invoke("record_external_artifact", locals())

    @server.tool(annotations=_annotations())
    def define_workflow(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                        definition: dict[str, Any], parameters: dict[str, Any] | None = None,
                        environment: dict[str, Any] | None = None) -> CallToolResult:
        """Register workflow and opt into v2; no execution."""
        return invoke("define_workflow", locals())

    @server.tool(annotations=_annotations())
    def bind_input(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                   slot: StrictStr, source_id: StrictStr,
                   mode: Literal["follow", "pinned"] = "follow") -> CallToolResult:
        """Bind source observation to input slot; preserve history."""
        return invoke("bind_input", locals())

    @server.tool(annotations=_annotations(True))
    def plan_rebuild(task_id: StrictStr, target_ids: list[StrictStr] | None = None,
                     detail: Literal["compact", "full"] = "compact",
                     offset: Annotated[StrictInt, Field(ge=0)] = 0,
                     limit: Annotated[StrictInt, Field(ge=1, le=50)] = 20,
                     expected_plan_digest: StrictStr | None = None) -> CallToolResult:
        """Read rebuild page; pin digest when paging. Global counts; incomplete cards require full detail. No execution."""
        return invoke("plan_rebuild", locals())

    @server.tool(annotations=_annotations())
    def begin_step_run(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                       step_id: StrictStr, plan_digest: StrictStr | None = None,
                       dependency_coverage: Literal["unknown", "partial", "declared_complete"] = "unknown",
                       target_ids: list[StrictStr] | None = None) -> CallToolResult:
        """Pin inputs and plan target_ids; coverage is caller-supplied."""
        return invoke("begin_step_run", locals())

    @server.tool(annotations=_annotations())
    def finish_step_run(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                        run_id: StrictStr, outcome: Literal["succeeded", "failed", "interrupted"] = "succeeded",
                        checks: dict[str, StepCheckInput] | None = None,
                        evidence_ref: StrictStr | None = None) -> CallToolResult:
        """Close run with evidence; activate complete unchanged outputs; retain failed bytes."""
        return invoke("finish_step_run", locals())

    @server.tool(annotations=_annotations(True))
    def check_recipe(recipe_task_id: StrictStr, recipe_artifact_id: StrictStr,
                     recipe_artifact_revision: StrictInt,
                     detail: Literal["compact", "full"] = "compact") -> CallToolResult:
        """Read pinned recipe evidence; no execution or approval."""
        return invoke("check_recipe", locals())

    @server.tool(annotations=_annotations())
    def begin_from_recipe(start_request_id: StrictStr, recipe_task_id: StrictStr,
                          recipe_artifact_id: StrictStr, recipe_artifact_revision: StrictInt,
                          input_bindings: dict[str, RecipeFileInput], parameters: dict[str, Any],
                          title: StrictStr, goal: StrictStr, environment: dict[str, Any] | None = None,
                          workspace_root: StrictStr | None = None, source_root: StrictStr | None = None,
                          binding: dict[str, Any] | None = None) -> CallToolResult:
        """Create task from pinned recipe; retry same request; inherit no approvals."""
        return invoke("begin_from_recipe", locals())

    @server.tool(annotations=_annotations(True))
    def git_status(task_id: StrictStr) -> CallToolResult:
        """Observe local Git; unknown is not clean; no writes."""
        return invoke("git_status", locals())

    @server.tool(annotations=_annotations())
    def git_bind(task_id: StrictStr, operation_id: StrictStr, expected_git_revision: StrictInt,
                 path: StrictStr) -> CallToolResult:
        """Bind allowed Git path; git_revision differs from task revision."""
        return invoke("git_bind", locals())

    @server.tool(annotations=_annotations())
    def git_create_worktree(task_id: StrictStr, operation_id: StrictStr, expected_git_revision: StrictInt,
                            repo_path: StrictStr, path: StrictStr, branch: StrictStr,
                            base: StrictStr = "HEAD") -> CallToolResult:
        """Create worktree in configured roots; no hooks/agents/network."""
        return invoke("git_create_worktree", locals())

    @server.tool(annotations=_annotations(True))
    def git_prepare_integration(task_id: StrictStr, target_path: StrictStr) -> CallToolResult:
        """Read fast-forward plan; no execution or approval."""
        return invoke("git_prepare_integration", locals())

    @server.tool(annotations=_annotations(destructive=True))
    def git_integrate(task_id: StrictStr, operation_id: StrictStr, expected_git_revision: StrictInt,
                      plan: dict[str, Any]) -> CallToolResult:
        """Apply fresh fast-forward plan; no commit/rebase/delete."""
        return invoke("git_integrate", locals())

    @server.tool(annotations=_annotations(True))
    def git_prepare_remove(task_id: StrictStr, target_path: StrictStr) -> CallToolResult:
        """Read removal plan against integration target; no deletion."""
        return invoke("git_prepare_remove", locals())

    @server.tool(annotations=_annotations(destructive=True))
    def git_remove_worktree(task_id: StrictStr, operation_id: StrictStr, expected_git_revision: StrictInt,
                            plan: dict[str, Any]) -> CallToolResult:
        """Remove clean managed worktree under fresh plan; retain branch."""
        return invoke("git_remove_worktree", locals())

    @server.tool(annotations=_annotations())
    def git_record_snapshot(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                            paths: list[StrictStr]) -> CallToolResult:
        """Archive selected Git files; uses core task revision."""
        return invoke("git_record_snapshot", locals())

    @server.tool(annotations=_annotations())
    def git_abandon_pending(task_id: StrictStr, operation_id: StrictStr, expected_git_revision: StrictInt,
                            pending_operation_id: StrictStr) -> CallToolResult:
        """Retire a pending Git intention only after proving no Git effect; never reset or delete."""
        return invoke("git_abandon_pending", locals())

    return server
