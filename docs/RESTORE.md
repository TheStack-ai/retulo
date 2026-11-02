# Restore a Codex installation step

`scripts/restore_codex.py` restores **one completed installation snapshot**. It previews by default. The snapshot, Codex settings, engine configuration, skill directory and snapshot directory must all be supplied as explicit absolute paths. No host directories are guessed.

Use the snapshot path returned by `scripts/install_codex.py --apply`. Close editors that may change these settings during restoration. Coordinate a reconnect at a suitable time: this tool does not stop Codex or reload a running MCP connection.

```sh
.venv/bin/python scripts/restore_codex.py \
  --snapshot "$HOME/.local/state/retulo/install/SNAPSHOT_DIRECTORY" \
  --codex-home "$HOME/.codex" \
  --engine-config "$HOME/.config/retulo/config.json" \
  --skills-home "$HOME/.agents/skills" \
  --snapshot-home "$HOME/.local/state/retulo/install"
```

Review the listed paths and actions, then repeat the same command with `--apply`. Preview creates no files. Apply shares the installer's target locks and checks the snapshot and all target files again after taking those locks.

The restore tool validates:

- The snapshot is a direct child of the explicit snapshot directory and has a completed version-1 manifest.
- Every recorded target belongs to the exact seven-file target set: Codex configuration, engine configuration, the Retulo skill, global guidance, hooks and the two managed-default receipts.
- Targets and backups contain no symlinks or parent traversal. Backups must be their indexed files inside that snapshot and match their recorded hashes.
- Every current file still matches its recorded installed hash before anything is restored. Later user edits, partial state and an older inapplicable snapshot cause a refusal.

Files that existed before installation are restored from their verified backup bytes. Files created by that installation step are removed. Workspaces, task artifacts, source repositories and the installation snapshots remain untouched. Empty directories and lock files may remain.

If an installation was updated, restore snapshots **newest to oldest**. Restoring the latest snapshot reverses that update; it does not necessarily remove the original installation. Continue to the initial installation snapshot to remove the installed integration. A re-run with every target already at its verified before-state reports `already_at_before_state` without changing anything.

Existing version-1 installer snapshots do not record original permission modes. The installer preserves modes of files it replaces, so restoration preserves the current modes for those files. Original modes cannot be recovered if someone changed them later. If a snapshot contains explicit before/after modes, those values are validated and used. No mode recovery beyond the available metadata is claimed.

On an apply failure the tool attempts to put already-processed targets back to their installed bytes and modes. It preserves any concurrent user edit instead of overwriting it and reports remaining review notes. This is bounded operation-failure recovery, not a power-loss journal or a way to prevent arbitrary editors from writing the same settings. A refusal has no force option: reconcile the changed file manually before trying again.

Snapshots may contain private original settings. Keep them local and outside the public source repository. The restore tool never publishes or removes them.
