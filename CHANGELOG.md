# Changelog

## 0.6.0rc1 — 2026-11-09

- Connect tasks to local Git worktrees without changing their existing source-root or v1/v2 artifact meanings.
- Add nine explicit Git CLI/MCP operations: status, binding, managed creation, selected-file snapshots, integration and removal plans/actions, and unapplied-intent reconciliation. The public MCP surface now has 33 tools.
- Require an opt-in managed-worktree root and independent Git revisions; preserve durable intent/receipt records across interrupted operations.
- Preserve explicitly selected file bytes and a Git identity/hash manifest as immutable ZIP artifacts.
- Support fast-forward integration only. Cleanup is separate, retains the branch, and refuses non-managed, dirty, ignored, locked or non-integrated worktrees.
- Reject executable Git filters and config includes, including inactive conditional includes; reject unsupported submodule mutations. Do not execute hooks, launch models, or contact remotes through the Git extension.
- Block task completion while a Git intent is pending, and retain existing task and artifact behavior.
- Document external-process races, shared Git state, permanent deletion and unsupported environments instead of claiming full isolation or transactional rollback.

## 0.5.0rc1 — 2026-11-03

- Add a checksum-verified macOS/Linux bootstrap and persistent isolated tool install, without a source checkout or manual virtual environment.
- Ship Codex and Claude Code setup, templates and guarded disconnection inside the wheel.
- Add `retulo setup`, `retulo doctor` and `retulo disconnect`, with preview by default and explicit project roots.
- Preserve other client settings, shared core configuration and existing workspaces when disconnecting a client.
- Verify configured MCP connections without launching an AI model; keep client trust and adoption separate from protocol checks.
- Refresh English, Chinese and Korean installation guides and site, and exercise the installed wheel in CI.

## 0.4.0rc2 — 2026-11-02

- Remove the private memory-system bridge, its five tools, configuration and installer option from the public package.
- Keep the 24 core task, workflow and recipe tools self-contained. Unsupported configuration keys now produce a configuration error.
- Bound actual ZIP/ZIP64 central-directory records before metadata object allocation, regardless of footer counts.
- Enforce TAR header, extension, nesting and metadata budgets while parsing, and reject sparse archive forms in the release inspector.
- Refresh the English, Chinese and Korean public documentation and site to match the standalone package.


## 0.4.0rc1 — 2026-11-02

- Add opt-in v2 tasks with explicit input bindings, workflow definitions and immutable execution history.
- Plan partial regeneration from exact inputs, scoped parameters, declared tool conditions and output integrity.
- Preserve incomplete outputs while promoting complete step output sets and verified final result sets separately.
- Add typed, version-pinned recipes and recoverable new-task creation without inherited approvals or verdicts.
- Extend CLI/MCP, paged records and bounded Codex context without adding a model runner or watcher.
- Include a real file-inventory report example using separate CLI and producer processes.
- Retain v1 task behavior and the existing reservation, recovery, approval and installation contracts.

The release remains a local candidate. Qualification distinguishes automated
regressions, actual CLI/MCP executions and installed-client adoption.

## 0.3.1rc1 — 2026-11-02

Initial Retulo public candidate, prepared from the locally qualified 0.3.0 development baseline.

- Introduce the Retulo package, CLI, paths and Codex integration identity.
- Preserve task UUIDs, output versions, bounded reads, approval boundaries and idempotent operations.
- Add guarded installation-snapshot restore with a read-only preview.
- Add licensing, dependency notices, reporting guidance, CI and release checks.
- Keep the core independent of an additional agent executor.
- Target macOS source installation; candidate preparation performs no public upload.

The renamed candidate has its own qualification record. Earlier client observations are historical evidence, not a guarantee for future clients or requests.
