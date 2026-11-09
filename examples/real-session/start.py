#!/usr/bin/env python3
"""Create the first half of a real-session demo in a new, isolated directory.

This script does not start an agent. Continue in your own fresh agent chat.
Only synthetic source material is used. Personal settings are never loaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from demo_continuity import ContinuityHost  # noqa: E402
from demo_incremental import dump  # noqa: E402

NOTES = """Northstar website refresh — fictional demo notes
Goal: Make the studio's services and contact details easier to find.
Scope: Home, Services and Contact pages.
Schedule: Four weeks — discovery, design, build, review.
Keep the existing logo and contact email.
"""
PROPOSAL = """# Northstar website refresh

## Goal
Make the studio's services and contact details easier to find.

## Scope
Refresh three pages: Home, Services and Contact.
Keep the existing logo and contact email.

## Schedule — four weeks
- Week 1: Discovery and content review.
- Week 2: Page design.
- Week 3: Build the three pages.
- Week 4: Review, corrections and handover.

## Next step
Confirm the schedule before work begins.
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--session-id", help="Actual current host session ID, if available")
    args = parser.parse_args()
    raw = args.run_dir.expanduser().absolute()
    if raw.is_symlink() or (raw.exists() and (not raw.is_dir() or any(raw.iterdir()))):
        raise ValueError("Choose a new or empty directory; existing files are preserved")
    run = raw.resolve()
    (run / "inputs").mkdir(parents=True)
    notes = run / "inputs" / "notes.md"
    notes.write_text(NOTES, encoding="utf-8")
    config = run / "config.json"
    dump(config, {"schema_version": 1, "workspace_home": str(run / "workspaces"),
                  "state_home": str(run / "state"), "allowed_source_roots": [str(run / "inputs")],
                  "allowed_workspace_roots": [], "allowed_git_worktree_roots": []})
    host = ContinuityHost(config, "first-chat")
    binding = {"provider": "codex", "session_id": args.session_id} if args.session_id else None
    begun = host.call("begin_task", start_request_id="northstar-first-draft", title="Northstar website refresh",
                      goal="Draft the proposal, retain the source notes and preserve every managed version",
                      kind="document", binding=binding,
                      completion_criteria=[{"id": "proposal-checked", "text": "Proposal follows the source scope and requested schedule"}])
    task = begun["task_id"]
    host.mutate("add_source", task, source_path=str(notes), mode="copy")
    reserved = host.mutate("reserve_output", task, name="proposal.md", role="deliverable", format="md")
    Path(reserved["write_path"]).write_text(PROPOSAL, encoding="utf-8")
    committed = host.mutate("commit_artifact", task, reservation_id=reserved["reservation_id"], token=reserved["token"])
    artifact = committed["artifact"]
    path = Path(begun["workspace_path"]) / artifact["path"]
    assert path.read_text() == PROPOSAL
    host.mutate("checkpoint", task, changes={"next_action": "Continue in a new chat if the schedule changes",
        "criteria_results": [{"criterion": "proposal-checked", "status": "passed",
        "evidence_ref": f"artifact:{artifact['artifact_id']}:{artifact['revision']}",
        "note": "Verified the three-page scope, logo/email constraint and four-week schedule against the synthetic source."}]})
    host.mutate("complete_task", task)
    result = {"task_id": task, "workspace_path": begun["workspace_path"], "config": str(config),
              "source": str(notes), "source_sha256": sha(notes), "v1_path": str(path),
              "v1_sha256": sha(path), "artifact_id": artifact["artifact_id"], "artifact_revision": artifact["revision"],
              "status": "completed", "events": host.events, "synthetic_material": True,
              "execution": "Actual Retulo CLI calls from the first coordinator chat; no agent launched by this script"}
    dump(run / "first-chat.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
