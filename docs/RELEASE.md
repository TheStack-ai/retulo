# Retulo 0.6.0rc1

Local workspaces for AI agents, now with explicit Git worktree workflows.
This is a preview release. Ordinary document tasks still work without Git.

## New in this preview

- Connect an existing worktree or explicitly create a managed worktree for a task.
- Inspect local Git state and continue from the same task association.
- Preserve selected files and their Git identity/hash manifest in immutable ZIP artifacts.
- Prepare and apply fast-forward integration, then separately prepare and remove an integrated managed worktree. The branch is retained.
- Reconcile interrupted operations using durable intents and receipts. Completion is blocked while a Git operation remains pending.
- Use the same nine Git operations through the CLI and typed MCP interface, alongside the existing 24 task/file/workflow tools.

Existing task UUIDs, source roots and v1/v2 artifact meanings are preserved.
Git state uses a separate revision; a clean status does not mean tests passed,
content was reviewed or a user approved the result.

## Install

```sh
curl -fsSL https://thestack-ai.github.io/retulo/install.sh | sh
retulo --version
```

Expect `retulo 0.6.0rc1`. The installer checks the wheel against the release's
`SHA256SUMS` before installing it in a persistent isolated tool environment.
The wheel includes the CLI, MCP server, Codex/Claude setup and managed instructions.
No PyPI or npm release is implied.

Git creation is disabled until a narrowly scoped `allowed_git_worktree_roots`
configuration is supplied. Repositories must already be allowed source roots.
See [Git workspaces](https://github.com/TheStack-ai/retulo/blob/v0.6.0rc1/docs/GIT_WORKSPACES.md) and [installation](https://github.com/TheStack-ai/retulo/blob/v0.6.0rc1/docs/INSTALLATION.md) for
configuration, continuation, checks and client setup.

## Safety and support scope

The Git extension does not stage or commit changes, squash or rebase history,
execute custom hooks, fetch from remotes, launch an agent, or force-delete a
worktree. Executable filters and Git config includes (including inactive
`includeIf`) are rejected before worktree creation. Submodule contents are not
inspected; mutations involving submodules are unsupported.

Worktrees share Git objects, refs and some configuration. They do not isolate
processes, ports, databases or credentials. Cooperative locks do not stop external
editors or Git commands. Unknown or partial outcomes remain pending; there is no
filesystem-wide transaction or automatic rollback. Deletion is permanent, so
preserve needed bytes first. Snapshots cover only the explicitly selected files.

The local qualification target is macOS with a local filesystem and Python 3.11
or later. Hosted CI runs separately on macOS and Ubuntu with Python 3.11/3.14;
check the exact release revision's workflow result. Windows, NAS, multi-host
concurrency and hostile simultaneous filesystem mutation are not qualified.
Actual client adoption is separate from the MCP protocol tests.

See [qualification](https://github.com/TheStack-ai/retulo/blob/v0.6.0rc1/docs/QUALIFICATION.md) for observed local checks and limits.
