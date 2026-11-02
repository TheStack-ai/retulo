#!/usr/bin/env python3
"""Create, resume and revise a real managed file task using separate CLI processes.

Uses synthetic visitor-guide content in an explicitly supplied empty directory.
No model, global settings, installed client or user document is changed. This
demonstrates task continuity through the CLI, not automatic host-client adoption.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from demo_incremental import Host, dump


SOURCE = "Visitor desk: ground floor\nOpening hours: Monday–Friday, 09:00–17:00\n"
FIRST = "# Visitor guide\n\nThe visitor desk is on the ground floor.\n\nOpen Monday–Friday, 09:00–17:00.\n"
SECOND = FIRST + "\n## Before your visit\n\nBring your booking reference and check in at the visitor desk.\n"


class ContinuityHost(Host):
    def __init__(self, config, phase):
        super().__init__(config)
        self.phase = phase

    def mutate(self, method, task, **arguments):
        if task not in self.revisions:
            self.call("get_context", task_id=task)
        self.sequence += 1
        return self.call(method, task_id=task,
                         operation_id=f"continuity-{self.phase}-{self.sequence:04d}-{method}",
                         expected_revision=self.revisions[task], **arguments)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def produce(host, task_id, content):
    reservation = host.mutate("reserve_output", task_id, name="visitor-guide.md", role="deliverable", format="md")
    Path(reservation["write_path"]).write_text(content, encoding="utf-8")
    artifact = host.mutate("commit_artifact", task_id,
                           reservation_id=reservation["reservation_id"], token=reservation["token"])["artifact"]
    path = Path(host.call("get_context", task_id=task_id)["workspace_path"]) / artifact["path"]
    assert path.read_text(encoding="utf-8") == content
    ref = f"artifact:{artifact['artifact_id']}:{artifact['revision']}"
    host.mutate("checkpoint", task_id, changes={"criteria_results": [{
        "criterion": "guide-checked", "status": "passed", "evidence_ref": ref,
        "note": "Generated text matches this demo's exact synthetic expected text; human approval is separate."
    }]})
    host.mutate("complete_task", task_id)
    return artifact, path


def run(destination):
    destination = destination.expanduser().absolute()
    if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise ValueError("Choose a new or empty output directory; existing contents are preserved")
    # macOS /tmp is a system alias. Keep the explicitly supplied destination
    # check above, then store canonical paths in the engine configuration.
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    source_dir = destination / "inputs"
    source_dir.mkdir()
    source = source_dir / "visitor-notes.txt"
    source.write_text(SOURCE, encoding="utf-8")
    source_hash = digest(source)
    config = destination / "config.json"
    dump(config, {"schema_version": 1, "workspace_home": str(destination / "workspaces"),
                  "allowed_source_roots": [str(source_dir)], "allowed_workspace_roots": []})

    first_host = ContinuityHost(config, "create")
    begun = first_host.call("begin_task", start_request_id="continuity-visitor-guide",
                            title="Visitor guide", goal="Create a visitor guide and preserve the source notes",
                            completion_criteria=[{"id": "guide-checked", "text": "The generated guide matches the synthetic expected text"}])
    task_id = begun["task_id"]
    workspace = begun["workspace_path"]
    first_host.mutate("add_source", task_id, source_path=str(source))
    artifact_one, version_one = produce(first_host, task_id, FIRST)
    version_one_hash = digest(version_one)

    # A new coordinator object has no in-memory revision or task cache. Every
    # operation below starts a separate actual CLI process and uses the task ID.
    second_host = ContinuityHost(config, "continue")
    resumed = second_host.call("resume_task", task_id=task_id)
    current = second_host.call("get_context", task_id=task_id)
    assert current["task_id"] == task_id and current["workspace_path"] == workspace
    second_host.mutate("resume_task", task_id, reopen=True,
                       reason="Add a before-your-visit section while retaining the previous guide")
    artifact_two, version_two = produce(second_host, task_id, SECOND)
    final = second_host.call("get_context", task_id=task_id)
    assert artifact_one["artifact_id"] == artifact_two["artifact_id"]
    assert artifact_two["revision"] == artifact_one["revision"] + 1
    assert version_one != version_two and digest(version_one) == version_one_hash
    assert digest(source) == source_hash
    assert final["state"]["status"] == "completed"
    assert final["state"]["approvals"] == []
    result = {"status": "passed", "task_id": task_id, "workspace": workspace,
              "same_task_resumed": True, "same_workspace": True,
              "original_source_preserved": True, "previous_output_preserved": True,
              "fresh_checks_recorded": True, "user_approval_claimed": False,
              "versions": [{"revision": artifact_one["revision"], "path": str(version_one), "sha256": version_one_hash},
                           {"revision": artifact_two["revision"], "path": str(version_two), "sha256": digest(version_two)}],
              "cli_calls": len(first_host.events) + len(second_host.events),
              "resume_inspection": resumed.get("verification", {}).get("ok"),
              "execution": "Separate actual CLI processes; synthetic content written by this demo's producer",
              "real_client_adoption": "Not tested by this script",
              "model_calls": 0, "global_settings_changed": False}
    dump(destination / "evidence.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    run(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
