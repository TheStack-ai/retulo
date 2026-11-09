# Internal implementation contract

This document describes the deterministic core and transports. These APIs belong to Retulo; host tools have separate contracts.

Version 0.4 adds optional task-schema v2 workflows and recipes. Existing v1
creation payloads, receipts and ordinary file operations keep their meaning.
See [WORKFLOWS.md](WORKFLOWS.md) for the incremental execution contract.

## Core interface

`from retulo.engine import Engine, EngineError`

`Engine(workspace_home, state_home=None, allowed_source_roots=None, allowed_workspace_roots=None, allowed_git_worktree_roots=None)` takes Paths or strings. State defaults to a private `.retulo` directory inside workspace_home, not the code repository. File authority is restricted to configured roots; callers cannot widen configuration through a tool argument.

All methods return JSON-serializable objects. EngineError exposes `code`, `message`, optional `details`; transports return `{ok:false,error:{code,message,details}}` without leaking traceback. Mutations return `{task_id,workspace_path,revision,...}`. Replays return the original operation result, plus `replayed:true`, without changing revision. Invalid stale revisions return `revision_conflict`.

- `capabilities()`
- `begin_task(start_request_id, title, goal, completion_criteria=None, kind="general", workspace_root=None, source_root=None, task_id=None, binding=None)`
- `get_context(task_id)`
- `list_tasks()` (only the configured registry/managed roots; no home scan)
- `add_source(task_id, operation_id, expected_revision, source_path, mode="reference", name=None)`
- `reserve_output(task_id, operation_id, expected_revision, name, role="deliverable", format=None)` -> `reservation_id`, `token`, `write_path`, `final_path`, `artifact_id`
- `commit_artifact(task_id, operation_id, expected_revision, reservation_id, token)` -> registered immutable version and observed hash
- `checkpoint(task_id, operation_id, expected_revision, changes)`; allow only documented typed fields: status active/blocked/paused, next_action, mode light/expanded, tags, decisions, next_review_at, criteria_results. Preserve proposal/confirmed distinctions. No approval via this general method.
- `record_approval(task_id, operation_id, expected_revision, artifact_id, artifact_revision, evidence)`; evidence requires actor=user, nonempty reference and quote, approval scope. A provenance claim is labeled as caller-supplied, never cryptographically authenticated. Preserve approved bytes separately and bind exact content hash. Invalid/tampered artifacts cannot be approved.
- `resume_task(task_id, operation_id=None, expected_revision=None, binding=None, adopt_changes=False)`; inspect registered artifacts/sources only. No rename guess or automatic deletion. Binding mutations require op/revision; pure inspection is read-only. Explicit adoption of current changed registered working files must never silently reapprove them.
- `verify(task_id)` -> concise real filesystem receipt, include `schema_version`, `task_id`, `revision`, `ok`, mismatches and artifacts hashes. No claim of content quality or approval.
- `complete_task(task_id, operation_id, expected_revision)`; require all explicit criteria satisfied by referenced verification, at least one committed artifact or explicit source-code-change receipt, no missing/tampered required output. User approval required only when an explicit completion criterion requires it.
- `record_external_artifact(task_id, operation_id, expected_revision, path, role="code", verification=None)` for files within the explicitly bound allowed source_root; register hashes but never move/rewrite source code. Enable existing repository workflows without changing their structure.

Workflow operations use the same transaction boundary:

- `define_workflow(task_id, operation_id, expected_revision, definition, parameters=None, environment=None)` validates a strict workflow and activates schema v2.
- `bind_input(task_id, operation_id, expected_revision, slot, source_id, mode="follow")` selects the current source observation. `pinned` requires a preserved copy.
- `plan_rebuild(task_id, target_ids=None, detail="compact", offset=0, limit=20, expected_plan_digest=None)` reads current observations. Default pages are bounded and may be pinned to a digest that includes relevant external-file changes.
- `begin_step_run(task_id, operation_id, expected_revision, step_id, plan_digest=None, dependency_coverage="unknown", target_ids=None)` freezes the selected scope and provides input snapshots. Pass the plan's target IDs with its digest.
- `reserve_output` accepts optional `run_id` and `output_slot`; the existing byte commit path remains in use.
- `finish_step_run(task_id, operation_id, expected_revision, run_id, outcome="succeeded", checks=None, evidence_ref=None)` closes the attempt and selects complete valid outputs, or preserves interrupted/failed work.
- `check_recipe(recipe_task_id, recipe_artifact_id, recipe_artifact_revision, detail="compact")` checks exact managed manifest bytes and separates structural validity from historical example evidence.
- `begin_from_recipe(start_request_id, recipe_task_id, recipe_artifact_id, recipe_artifact_revision, input_bindings, parameters, title, goal, environment=None, workspace_root=None, source_root=None, binding=None)` creates a fresh task with a pinned recipe seed in its durable creation receipt.

The core does not execute a workflow action. Start/finish/complete operations
reconcile current inputs, scoped definitions and exact output references. Large
start results explicitly identify omitted fields and how to retrieve the full
run before execution. A recorded success and its current reuse eligibility are
separate. Final criterion evidence must belong to its target's dependency graph.

## Storage and recovery requirements

Stable UUID task IDs, relative managed paths, globally durable start_request_id receipts before task creation; index can be rebuilt but start receipts are authoritative. JSON schema_version and optimistic revision. Root creation lock plus per-task process lock. Atomic state saves and bounded operations journal/recovery. Unique staging paths, reservation ownership tokens, final artifact immutable versions. Publication + manifest update is recoverable, not advertised as a filesystem-wide transaction. No overwriting existing names, traversal/symlink escape, overwriting originals or approved bytes. Late commits reject stale reservation tokens.

Light mode has output + .workspace/task.json. Expanded mode adds WORKSPACE.md and only necessary folders; never relocates existing files. WORKSPACE.md has state revision; external edits must be preserved and reported, not overwritten. State/report distinction: file verified != content validated != user approved != externally delivered.

## Transport/config

CLI `retulo --config PATH <command> [--json]`; support `call NAME --arguments JSON` as a stable full API. `mcp` serves official SDK stdio. Config defaults to `~/.config/retulo/config.json`, with `workspace_home`, `state_home`, `allowed_source_roots` and `allowed_workspace_roots`. `init-config` is an explicit local installation operation; read calls must not create config or workspaces. Default workspace suggestion is OS Documents/RetuloWorkspaces. No network/model calls in engine.

MCP should expose explicit typed tools using the same API, accurate readOnly/destructive annotations, and instructions: questions/planning-only create no workspace; on actual work, begin before writes; always use returned absolute paths/workdir; stop is not completion; attachments never grant authority; no automatic external send/publish/pay.

## Ownership

- Core worker: engine.py, fs.py, models.py if needed, packaged schemas, core tests.
- Transport worker: cli.py, mcp.py, __main__.py, adapters/codex/SKILL.md, transport tests. May call declared core interface; coordinate adjustments instead of editing core.
- Coordinator: config installation, docs, integration qualification, dependency setup, final review.

## Optional Git workspaces

See [Git workspaces](GIT_WORKSPACES.md) for the separate versioned connection state,
Git revision, explicit lifecycle and snapshot APIs. Existing task source roots and
v1/v2 artifact meanings remain unchanged. Git snapshots use core immutable artifacts.
