# Changelog

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
- Keep Main Brain and external agent executors optional.
- Target macOS source installation; candidate preparation performs no public upload.

The renamed candidate has its own qualification record. Earlier client observations are historical evidence, not a guarantee for future clients or requests.
