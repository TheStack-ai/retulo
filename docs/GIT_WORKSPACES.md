# Git workspaces (unreleased)

Retulo can connect a task to a local Git worktree, observe changes, create a
separate worktree on explicit request, preserve selected files, and prepare an
explicit fast-forward integration or cleanup. Worktrunk inspired the lifecycle
separation; Retulo does not install or invoke Worktrunk or copy its source.

## Configuration and scope

Git is optional for ordinary document tasks. Git commands require a local Git
executable. Existing configurations remain valid. Add a narrowly scoped
`allowed_git_worktree_roots` array to a Retulo configuration to permit managed
worktree creation. The default is empty; tool arguments cannot extend it.
Repositories must already be inside `allowed_source_roots`. Do not permit the
whole home directory just to accommodate a sibling worktree.

The existing task `source_root` is never changed. Versioned Git metadata has its
own `git_revision`, independent of the task's `revision`. Core v1/v2 tasks retain
their meanings. Old engines do not understand the Git extension: use the upgraded
runtime for Git actions and retain its metadata when changing runtimes.

## Explicit workflow

All operations are available through MCP and `retulo call NAME --arguments JSON`.
Read returned revisions; never guess them or create a new operation ID merely to
hide a failed or ambiguous attempt.

1. Start or explicitly resume the intended Retulo task.
2. Use `git_bind` for an existing allowed worktree, or `git_create_worktree` for a
   new task-owned path with an explicit branch and base. One task has one current
   connection; the repository and other tasks remain in place.
3. Read `git_status` when continuing work. An unavailable/unknown observation is
   not clean, tested, approved, or ready to merge. Detached HEAD, locks, pending
   operations and untracked files are distinct conditions.
4. Use `git_record_snapshot` with exact relative file paths to preserve selected
   file bytes plus their manifest in an immutable ZIP artifact. This advances the
   core task revision. It does not mark tests or content review as passed. It is
   not a full repository backup and excludes unselected files.
5. Commit changes using the project's normal workflow. Retulo does not stage,
   commit, squash, rebase, or generate commit messages for the caller.
6. Use `git_prepare_integration` with a target worktree. Review the plan and pass
   it to `git_integrate`. Apply rechecks the source/target identities and heads.
   Only fast-forward integration is supported; divergence requires an external
   reviewed workflow. Integration does not automatically remove the worktree.
7. Use `git_prepare_remove`, then `git_remove_worktree` only for a Retulo-created
   managed worktree whose changes are integrated into the specified target.
   Tracked modifications, untracked/ignored files and locks block deletion.
   The branch is retained. Existing or externally created worktrees are not
   adopted as owned cleanup targets.

## Safety and recovery

Commands use argument arrays, bounded timeouts and controlled Git configuration.
No network access, custom hooks, model calls, background agent launches or forced
removal is part of this extension. Repositories with config includes (including inactive `includeIf`), executable Git filters and
other unsupported states fail closed. Submodule contents are explicitly
not inspected; mutation of repositories/target commits containing submodules is
unsupported in this version. Worktrees share Git objects, refs and some
configuration; they do not isolate processes, ports, databases or credentials.

Git mutations and Retulo state publication are separate operations. Durable
intent/receipt records permit reconciliation; they are not one atomic transaction.
A timeout or interrupted command can have an unknown outcome. Inspect and retry
the same operation with identical arguments instead of deleting remnants.
`git_abandon_pending` can clear an intent only after checking that Git has not
applied its changes; it never rolls back or deletes Git data. Unknown or partial
results remain pending. Task completion is blocked while a Git intent is pending.
Cooperative locks coordinate Retulo callers only. External Git commands, editors
and running programs can still race with observation; this is not a hostile-writer
sandbox or a process-termination service. Stop active work before integration or
cleanup. Retulo does not certify that no process has an open file in a worktree.

Removal is permanent and is not a recycle-bin promise. Preserve needed files with
an explicit snapshot or normal Git commits before cleanup. A saved snapshot only
covers the selected bytes and cannot restore ignored files or live processes.

## Installation and migration

This is an unreleased source change, not a published package update. Qualify the
new runtime using temporary configurations/workspaces and the official MCP stdio
handshake before changing a live client. Existing installations with different
package names, MCP keys or integration hooks are not automatically migrated.

Before replacing a runtime, inventory the exact launcher, configuration, task
registry and active work. Preserve the original configuration and task bytes;
avoid running two automatic workspace hooks over the same request. Test any
required format/configuration conversion on a copy. A transport test is not proof
that existing chats have loaded the new server. Keep the original runtime until
the new connection and continuation of a selected task have been verified.
