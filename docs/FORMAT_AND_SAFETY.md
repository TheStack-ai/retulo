# Records, recovery and boundaries

## Authority

`task.json` is the current task state. JSON Schema is packaged under `src/retulo/schemas`. `WORKSPACE.md` is a generated view with a state revision, not a transcript or second editable state store. User edits to that guide are preserved and reported. Separate user notes can be kept as ordinary artifacts.

Task state includes goal, explicit completion criteria, status, source references, immutable artifact revisions/hashes, optional approval snapshots, decisions (proposed/confirmed/superseded), optional application bindings, next action and review date. Current task facts live here. Historical decisions and evidence may be linked to Main Brain; stale memory cannot overwrite the current task state.

Opt-in schema v2 adds a workflow definition, current input bindings, immutable
run records, selected step outputs and a final target set. v1 remains supported;
an old engine must reject v2 instead of ignoring its completion contract. The
workflow and recipe schema versions are distinct from the task schema version.
See [workflows and recipes](WORKFLOWS.md).

Core approval and decision provenance is honestly labeled caller-supplied. The engine validates the referenced artifact version and preserves bytes, but it does not authenticate a human just because a caller submits `actor=user`. The client must use actual user evidence. Main Brain independently verifies registered evidence when connected.

## Files

- Source material defaults to reference; copying records origin and hash. Neither mode authorizes modifying the original.
- Reserve outputs before generation. Write only to the unique returned staging path, then commit.
- Commit validates bytes and publishes to an exclusive versioned destination. It does not replace an existing user's file.
- Approval binds artifact ID, revision, hash, evidence and approval scope. New work never inherits approval.
- Existing code repositories use explicit source-root bindings. The engine records selected file hashes and verification claims; it does not roll back the repository, alter its layout, run Git commands or change its AGENTS.md.
- Content quality/test claims and file integrity are distinct. Passed criteria have a specific artifact reference and caller-reported provenance; file presence alone cannot prove semantic correctness.
- In a workflow, file latest is not an active step result or a coherent final
  result set. Step output sets are selected only after their contract is met;
  final target sets are promoted on completion. Older final sets remain
  historical when their selected inputs change.
- Workflow source history remains immutable. Follow bindings check current
  originals; pinned bindings check preserved bytes. Execution snapshots and
  exact upstream references are retained so a changed source cannot silently
  become the input of an already-started attempt.
- Recipe reuse creates a new task. It copies definitions and pins exact manifest
  bytes, never previous approvals or successful verdicts. Example qualification
  requires matching execution and completion contracts, not a claim in JSON.

## Transactions

Creation receipts are durable before directory creation, allowing the same start request to recover after a lost response. The task registry is a locator, not a task identity. Each mutation uses a process lock, expected revision, operation ID and payload digest. Unique output reservation tokens prevent an unrelated writer from committing a reservation.

Filesystem publication, manifest update and guide update are a recoverable sequence, not one filesystem-wide atomic transaction. A pending journal supports retry after interruption. Unexpected files/manual edits are preserved and reported rather than deleted to make recovery appear successful.

External changes are inspected only on requested resume/verify and only over registered material. Changed working files require explicit adoption and renewed validation; approved snapshots remain separate. No whole-home discovery, background watcher or implicit reorganization is performed.

An explicit `path_updates` mapping can adopt a user-moved file only when its bytes still match the registered version. `attach_workspace` reconnects a deliberately supplied moved workspace and repairs its original start receipt; two live folders with the same task identity are rejected. Neither operation performs a folder search or a physical move.

`abandon_output` retains partial bytes while closing an unwanted reservation, so an abandoned alternative does not prevent completion forever. A late commit using its old token is rejected. Completed tasks are read-only until an explicit `resume_task(reopen=true, reason=...)`; reopen preserves the completion history and approval snapshots and requires completion criteria to be verified again.

## Limits

The initial concurrency scope is separate local processes on one machine and a local POSIX filesystem. Shared NAS locks, cloud-synced conflict resolution, hostile concurrent filesystem writers and Windows locking are not qualified. Metadata mutation by the same OS user is not cryptographically prevented.

Allowed roots are installation policy. Engine tool arguments cannot widen them. Traversal and symlink escapes are rejected at managed operations. Attachments and generated files cannot authorize root changes, transmission, publication, purchase or deletion.
