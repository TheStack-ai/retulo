"""Official MCP SDK v2 stdio interface over the deterministic core."""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BeforeValidator, ConfigDict, Field, StrictInt, StrictStr
from typing_extensions import Required, TypedDict

from . import __version__
from .cli import dispatch, dispatch_brain, error_payload


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
requires_approval=true retains the approval gate. Main Brain needs genuine source IDs and evidence.
No sending, publishing, model changes, background execution, new chats or permission expansion.
Codex remains the coordinator; optional executors are not dependencies. Report actual errors.
Workflow plans do not execute tools. The current agent runs steps; recipes start new tasks without inherited verdicts.
Required independent reviews use actual distinct reviewer_ref/execution_ref.
"""


def _annotations(read_only: bool = False) -> ToolAnnotations:
    return ToolAnnotations(read_only_hint=read_only, destructive_hint=False,
                           idempotent_hint=True, open_world_hint=False)


def build_server(engine: Any, main_brain: dict[str, Any] | None = None) -> MCPServer:
    server = MCPServer(name="retulo", version=__version__, instructions=INSTRUCTIONS,
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

    async def invoke_brain(name: str, parameters: dict[str, Any]) -> CallToolResult:
        parameters = {key: value for key, value in parameters.items() if key != "invoke_brain"}
        try:
            result = await dispatch_brain(engine, main_brain, name, parameters)
            failed = isinstance(result, dict) and result.get("ok") is False
        except Exception as exc:
            result, failed = error_payload(exc), True
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False,
                                                                               separators=(",", ":"), allow_nan=False))],
                              structured_content=result, is_error=failed)

    @server.tool(annotations=_annotations(True))
    def capabilities() -> CallToolResult:
        """Read engine capabilities. Does not initialize directories or change configuration."""
        return invoke("capabilities", {})

    @server.tool(annotations=_annotations(True))
    def list_tasks() -> CallToolResult:
        """List only this engine's registered tasks, never scan unrelated folders."""
        return invoke("list_tasks", {})

    @server.tool(annotations=_annotations(True))
    def harness_context(session_id: str | None = None, cwd: str | None = None,
                        turn_id: str | None = None, limit: int = 5) -> CallToolResult:
        """Read session/cwd candidates; never select, resume, verify or mutate them. No transcript/artifact bodies."""
        return invoke("harness_context", locals())

    @server.tool(annotations=_annotations())
    def begin_task(start_request_id: str, title: str, goal: str,
                   completion_criteria: list[str | CompletionCriterionInput] | None = None, kind: str = "general",
                   workspace_root: str | None = None, source_root: str | None = None,
                   task_id: str | None = None, binding: dict[str, Any] | None = None) -> CallToolResult:
        """Begin authorized file work, or reconnect task_id without changing its goal. Retry the same request ID."""
        return invoke("begin_task", locals())

    @server.tool(annotations=_annotations(True))
    def get_context(task_id: str, detail: Literal["compact", "full"] = "compact") -> CallToolResult:
        """Read compact current state; use read_records for details. Omission is not absence or approval."""
        return invoke("get_context", locals())

    @server.tool(annotations=_annotations(True))
    def read_records(task_id: str, section: Literal["completion_criteria", "criteria_results", "criteria_history",
                      "artifacts", "sources", "decisions", "approvals", "bindings", "operations", "reservations",
                      "workflow", "workflow_history", "input_bindings", "runs", "active_outputs",
                      "current_deliverable_set", "recipe_binding", "steps", "targets"],
                      offset: int = 0, limit: int = 20,
                      expected_revision: int | None = None) -> CallToolResult:
        """Read a revision-bound page. Follow next_offset; incomplete records require their recovery link."""
        return invoke("read_records", locals())

    @server.tool(annotations=_annotations())
    def attach_workspace(workspace_path: str) -> CallToolResult:
        """Reconnect an explicitly supplied workspace; preserve ID and repair registration. Never search or move files."""
        return invoke("attach_workspace", locals())

    @server.tool(annotations=_annotations())
    def add_source(task_id: str, operation_id: str, expected_revision: int, source_path: str,
                   mode: str = "reference", name: str | None = None) -> CallToolResult:
        """Register a permitted source in reference/copy mode. Preserve the original."""
        return invoke("add_source", locals())

    @server.tool(annotations=_annotations())
    def reserve_output(task_id: str, operation_id: str, expected_revision: int, name: str,
                       role: str = "deliverable", format: str | None = None,
                       run_id: str | None = None, output_slot: str | None = None) -> CallToolResult:
        """Reserve a unique staging destination before generating a file; use returned write_path."""
        return invoke("reserve_output", locals())

    @server.tool(annotations=_annotations())
    def commit_artifact(task_id: str, operation_id: str, expected_revision: int,
                        reservation_id: str, token: str) -> CallToolResult:
        """Inspect staged bytes and register an immutable version; this does not approve its contents."""
        return invoke("commit_artifact", locals())

    @server.tool(annotations=_annotations())
    def abandon_output(task_id: str, operation_id: str, expected_revision: int,
                        reservation_id: str, token: str) -> CallToolResult:
        """Retire a reservation, retaining partial bytes and rejecting late commits."""
        return invoke("abandon_output", locals())

    @server.tool(annotations=_annotations())
    def checkpoint(task_id: str, operation_id: str, expected_revision: int,
                    changes: CheckpointChangesInput) -> CallToolResult:
        """Checkpoint typed changes. Passed evidence is artifact:<id>:<revision>.
        Omit attention to preserve it; null clears it. Open attention blocks completion."""
        return invoke("checkpoint", locals())

    @server.tool(annotations=_annotations())
    def record_approval(task_id: str, operation_id: str, expected_revision: int, artifact_id: str,
                        artifact_revision: int, evidence: dict[str, Any]) -> CallToolResult:
        """Bind actual user approval to exact bytes. evidence needs actor=user, reference, quote and scope;
        provenance is caller-supplied, not authenticated by the engine."""
        return invoke("record_approval", locals())

    @server.tool(annotations=_annotations())
    def resume_task(task_id: str, operation_id: str | None = None, expected_revision: int | None = None,
                    binding: dict[str, Any] | None = None, adopt_changes: bool = False,
                    path_updates: dict[str, str] | None = None, reopen: bool = False,
                    reason: str | None = None, detail: Literal["compact", "full"] = "compact") -> CallToolResult:
        """Inspect registered files; mutations need operation/revision. path_updates requires identical bytes.
        Reopen needs explicit reason; history/approvals remain and criteria reset. Default only inspects."""
        return invoke("resume_task", locals())

    @server.tool(annotations=_annotations(True))
    def verify(task_id: str) -> CallToolResult:
        """Verify real registered paths/hashes; file validation is distinct from content/user approval."""
        return invoke("verify", locals())

    @server.tool(annotations=_annotations())
    def complete_task(task_id: str, operation_id: str, expected_revision: int) -> CallToolResult:
        """Complete only when declared criteria and actual required artifacts pass; Stop is not completion."""
        return invoke("complete_task", locals())

    @server.tool(annotations=_annotations())
    def record_external_artifact(task_id: str, operation_id: str, expected_revision: int, path: str,
                                 role: str = "code", verification: dict[str, Any] | None = None) -> CallToolResult:
        """Register changed code inside the explicitly bound source root without moving or rewriting it."""
        return invoke("record_external_artifact", locals())

    @server.tool(annotations=_annotations())
    def define_workflow(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                        definition: dict[str, Any], parameters: dict[str, Any] | None = None,
                        environment: dict[str, Any] | None = None) -> CallToolResult:
        """Register a validated workflow definition and opt this task into v2; never execute steps."""
        return invoke("define_workflow", locals())

    @server.tool(annotations=_annotations())
    def bind_input(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                   slot: StrictStr, source_id: StrictStr,
                   mode: Literal["follow", "pinned"] = "follow") -> CallToolResult:
        """Bind an input slot to one registered source observation, preserving older bindings."""
        return invoke("bind_input", locals())

    @server.tool(annotations=_annotations(True))
    def plan_rebuild(task_id: StrictStr, target_ids: list[StrictStr] | None = None,
                     detail: Literal["compact", "full"] = "compact",
                     offset: Annotated[StrictInt, Field(ge=0)] = 0,
                     limit: Annotated[StrictInt, Field(ge=1, le=50)] = 20,
                     expected_plan_digest: StrictStr | None = None) -> CallToolResult:
        """Read a bounded rebuild-plan page; no execution/adoption. Pin its digest when paging.
        Counts are global; incomplete cards require explicit full detail before use as exact evidence."""
        return invoke("plan_rebuild", locals())

    @server.tool(annotations=_annotations())
    def begin_step_run(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                       step_id: StrictStr, plan_digest: StrictStr | None = None,
                       dependency_coverage: Literal["unknown", "partial", "declared_complete"] = "unknown",
                       target_ids: list[StrictStr] | None = None) -> CallToolResult:
        """Pin inputs before host execution. Match the plan's target_ids; coverage is caller-supplied."""
        return invoke("begin_step_run", locals())

    @server.tool(annotations=_annotations())
    def finish_step_run(task_id: StrictStr, operation_id: StrictStr, expected_revision: StrictInt,
                        run_id: StrictStr, outcome: Literal["succeeded", "failed", "interrupted"] = "succeeded",
                        checks: dict[str, StepCheckInput] | None = None,
                        evidence_ref: StrictStr | None = None) -> CallToolResult:
        """Close a run with evidence; activate only its complete, unchanged output set. Preserve failed bytes."""
        return invoke("finish_step_run", locals())

    @server.tool(annotations=_annotations(True))
    def check_recipe(recipe_task_id: StrictStr, recipe_artifact_id: StrictStr,
                     recipe_artifact_revision: StrictInt,
                     detail: Literal["compact", "full"] = "compact") -> CallToolResult:
        """Check a pinned recipe and evidence; compact by default, full explicitly. Never execute or infer approval."""
        return invoke("check_recipe", locals())

    @server.tool(annotations=_annotations())
    def begin_from_recipe(start_request_id: StrictStr, recipe_task_id: StrictStr,
                          recipe_artifact_id: StrictStr, recipe_artifact_revision: StrictInt,
                          input_bindings: dict[str, RecipeFileInput], parameters: dict[str, Any],
                          title: StrictStr, goal: StrictStr, environment: dict[str, Any] | None = None,
                          workspace_root: StrictStr | None = None, source_root: StrictStr | None = None,
                          binding: dict[str, Any] | None = None) -> CallToolResult:
        """Create a new task from an exact recipe version and typed inputs. Retry the same request; inherit no approvals."""
        return invoke("begin_from_recipe", locals())

    @server.tool(annotations=_annotations(True))
    async def brain_prepare(task_id: str, prompt: str = "Review prior decisions for this task",
                             detail: Literal["compact", "full"] = "compact") -> CallToolResult:
        """Read compact task and optional memory context; retrieve exact evidence before relying on it."""
        return await invoke_brain("brain_prepare", locals())

    @server.tool(annotations=_annotations())
    async def brain_capture(task_id: str, event_id: str, title: str, body: str, source_id: str,
                             quote: str, kind: str = "task", metadata: dict[str, Any] | None = None) -> CallToolResult:
        """Capture actual source evidence with its exact quote. Retry the same event ID and payload;
        model prose is not execution proof and missing synchronization never implies success."""
        return await invoke_brain("brain_capture", locals())

    @server.tool(annotations=_annotations())
    async def brain_sync(task_id: str, event_id: str) -> CallToolResult:
        """Reconcile/retry the exact immutable saved event; no new body or event is synthesized."""
        return await invoke_brain("brain_sync", locals())

    @server.tool(annotations=_annotations(True))
    async def brain_receipt(task_id: str, event_id: str) -> CallToolResult:
        """Read the exact remote receipt and local sync state; receipt is not current evidence validation."""
        return await invoke_brain("brain_receipt", locals())

    @server.tool(annotations=_annotations(True))
    async def review_context(task_id: str, detail: Literal["compact", "full"] = "compact",
                              event_id: str | None = None) -> CallToolResult:
        """Read bounded current facts and prior evidence; event_id selects one event. The caller judges applicability."""
        return await invoke_brain("review_context", locals())

    return server
