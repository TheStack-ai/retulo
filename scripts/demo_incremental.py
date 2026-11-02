#!/usr/bin/env python3
"""Exercise incremental reports and recipes through separate real CLI processes.

Only the explicitly supplied, initially empty output directory is populated.
No global configuration, installer, model, network service or user document is
changed. With --baseline-zip the first report uses an actual earlier source
snapshot and the update uses the current repository. A second recipe task uses
the repository's actual test/document inventory.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "scripts" / "report_fixture.py"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inventory(root, baseline_zip=None, selected=None):
    areas = {}
    def include(name, data):
        path = Path(name)
        area = path.parts[0] if len(path.parts) > 1 else "root"
        if area not in {"src", "tests", "docs", "scripts", "adapters", "examples", "root"}:
            return
        if selected is not None and area not in selected:
            return
        if name == "BASELINE_MANIFEST.json" or any(part in {"__pycache__", "build", "dist"} or part.endswith(".egg-info") for part in path.parts):
            return
        if path.suffix not in {".py", ".json", ".md", ".toml", ".lock", ".in"} and path.name not in {"LICENSE"}:
            return
        row = areas.setdefault(area, {"area": area, "files": 0, "bytes": 0, "lines": 0})
        row["files"] += 1
        row["bytes"] += len(data)
        row["lines"] += len(data.decode("utf-8", errors="replace").splitlines())
    if baseline_zip:
        with zipfile.ZipFile(baseline_zip) as archive:
            for item in archive.infolist():
                if not item.is_dir():
                    include(item.filename, archive.read(item))
    else:
        for area in ("src", "tests", "docs", "scripts", "adapters", "examples"):
            for path in sorted((root / area).rglob("*")):
                if path.is_file() and not path.is_symlink():
                    include(path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.iterdir()):
            if path.is_file() and not path.is_symlink():
                include(path.name, path.read_bytes())
    return [areas[name] for name in sorted(areas)]


def write_inventory(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["area", "files", "bytes", "lines"])
        writer.writeheader(); writer.writerows(rows)


def graph():
    def step(inputs, outputs, instruction, parameters=None):
        return {"action": {"kind": "agent", "instruction": instruction},
                "inputs": inputs, "parameters": parameters or [], "environment": ["report_producer"],
                "outputs": {name: {"logical_name": file, "role": "deliverable" if name in {"report", "validation"} else "intermediate", "format": Path(file).suffix[1:]}
                            for name, file in outputs.items()},
                "reuse_policy": "if_unchanged", "required_checks": ["output-valid"]}
    upstream = lambda name, output: {"step": name, "output": output}
    return {"workflow_schema_version": 1,
            "inputs": {"inventory": {"required": True, "format": "csv"}, "profile": {"required": True, "format": "json"}},
            "parameters": {"title": {"type": "string", "required": True}, "as_of": {"type": "string", "required": True}},
            "environment": {"report_producer": {"required": True}},
            "steps": {
                "profile": step({"profile": {"input": "profile"}}, {"profile": "profile.html"}, "Escape the supplied profile description into an HTML paragraph. Preserve the source text."),
                "aggregate": step({"inventory": {"input": "inventory"}}, {"metrics": "metrics.json", "table": "inventory-table.csv"}, "Read area,files,bytes,lines CSV. Require unique areas and nonnegative integers; emit totals and the sorted table."),
                "chart": step({"metrics": upstream("aggregate", "metrics")}, {"chart": "composition.svg"}, "Create an SVG bar chart of lines per area using the supplied exact metrics."),
                "summary": step({"metrics": upstream("aggregate", "metrics")}, {"summary": "summary.md"}, "Describe the actual file/line/byte totals and largest area. Do not infer quality from line count."),
                "report": step({"metrics": upstream("aggregate", "metrics"), "profile": upstream("profile", "profile"), "chart": upstream("chart", "chart"), "summary": upstream("summary", "summary")}, {"report": "report.html"}, "Compose a self-contained HTML report with the exact metrics embedded as JSON, profile, SVG, summary and table.", ["title", "as_of"]),
                "validate": step({"metrics": upstream("aggregate", "metrics"), "report": upstream("report", "report")}, {"validation": "validation.json"}, "Verify sums, exact embedded report data, the displayed total and valid embedded SVG. Emit a JSON validation receipt; visual review is separate."),
            },
            "targets": {"report": {"outputs": [upstream("report", "report"), upstream("validate", "validation")], "required_criteria": ["report-valid"]}}}


class Host:
    def __init__(self, config):
        self.config = str(config)
        self.events = []
        self.sequence = 0
        self.revisions = {}

    def call(self, method, **arguments):
        result = subprocess.run([sys.executable, "-m", "retulo", "--config", self.config,
                                 "call", method, "--arguments", json.dumps(arguments, ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True)
        try:
            value = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError(f"{method}: non-JSON CLI output: {result.stderr[-2000:]}") from exc
        if result.returncode or value.get("ok") is False:
            raise RuntimeError(f"{method}: {json.dumps(value, ensure_ascii=False)}")
        if value.get("task_id") and type(value.get("revision")) is int:
            self.revisions[value["task_id"]] = max(self.revisions.get(value["task_id"], 0), value["revision"])
        self.events.append({"method": method, "task_id": value.get("task_id"), "revision": value.get("revision"), "replayed": value.get("replayed", False)})
        return value

    def state(self, task):
        return self.call("get_context", task_id=task, detail="full")["state"]

    def mutate(self, method, task, **arguments):
        if task not in self.revisions:
            self.call("get_context", task_id=task)
        self.sequence += 1
        return self.call(method, task_id=task, operation_id=f"demo-{self.sequence:04d}-{method}", expected_revision=self.revisions[task], **arguments)

    def source(self, task, slot, path):
        record = self.mutate("add_source", task, source_path=str(path))["source"]
        self.mutate("bind_input", task, slot=slot, source_id=record["source_id"], mode="follow")

    def artifact(self, task, ref):
        _, identity, revision = ref.split(":")
        context = self.call("get_context", task_id=task, detail="full")
        item = next(a for a in context["state"]["artifacts"] if a["artifact_id"] == identity and a["revision"] == int(revision))
        return Path(context["workspace_path"]) / item["path"]

    def execute(self, task):
        executed = []
        while True:
            plan = self.call("plan_rebuild", task_id=task)
            ready = plan.get("ready", [])
            if not ready:
                if not any(plan["counts"].get(key, 0) for key in ("needs_run", "blocked", "unknown")):
                    return executed
                raise RuntimeError(f"No runnable steps: {json.dumps(plan, ensure_ascii=False)}")
            step_id = ready[0]
            begun = self.mutate("begin_step_run", task, step_id=step_id, plan_digest=plan["plan_digest"], dependency_coverage="declared_complete", target_ids=plan["target_ids"])
            outputs, reservations = {}, {}
            for slot, spec in begun["step"]["outputs"].items():
                reserved = self.mutate("reserve_output", task, name=spec["logical_name"], role=spec["role"], format=spec.get("format"), run_id=begun["run_id"], output_slot=slot)
                outputs[slot], reservations[slot] = reserved["write_path"], reserved
            inputs = {port: value["path"] for port, value in begun["inputs"].items()}
            command = subprocess.run([sys.executable, str(PRODUCER), "--stage", step_id,
                                      "--inputs", json.dumps(inputs), "--outputs", json.dumps(outputs),
                                      "--parameters", json.dumps(begun["parameters"], ensure_ascii=False)], capture_output=True, text=True)
            if command.returncode:
                raise RuntimeError(f"Report stage {step_id} failed: {command.stderr}")
            refs = {}
            for slot, reservation in reservations.items():
                result = self.mutate("commit_artifact", task, reservation_id=reservation["reservation_id"], token=reservation["token"])
                artifact = result["artifact"]
                refs[slot] = f'artifact:{artifact["artifact_id"]}:{artifact["revision"]}'
            evidence = next(iter(refs.values()))
            self.mutate("finish_step_run", task, run_id=begun["run_id"], outcome="succeeded",
                        checks={"output-valid": {"status": "passed", "evidence_ref": evidence}}, evidence_ref=evidence)
            executed.append(step_id)
            if len(executed) > 30:
                raise RuntimeError("Unexpected repeated stage execution")

    def complete(self, task):
        state = self.state(task)
        evidence = state["active_outputs"]["validate"]["outputs"]["validation"]
        self.mutate("checkpoint", task, changes={"criteria_results": [{"criterion": "report-valid", "status": "passed", "evidence_ref": evidence, "note": "Real report producer validated totals, exact embedded data and SVG; visual review is separate."}]})
        self.mutate("complete_task", task)
        return self.state(task)


def run(args):
    destination = args.output_dir.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("The demonstration output directory must be empty; existing work is never replaced")
    destination.mkdir(parents=True, exist_ok=True)
    inputs = destination / "inputs"; inputs.mkdir()
    current_rows = inventory(ROOT)
    baseline_rows = inventory(ROOT, args.baseline_zip) if args.baseline_zip else inventory(ROOT, selected={"src", "docs"})
    if baseline_rows == current_rows:
        raise ValueError("Baseline and current snapshots must differ for this demonstration")
    baseline = inputs / "baseline-inventory.csv"; current = inputs / "current-inventory.csv"; alternate = inputs / "test-document-inventory.csv"
    write_inventory(baseline, baseline_rows); write_inventory(current, current_rows)
    write_inventory(alternate, inventory(ROOT, selected={"tests", "docs"}))
    profile = inputs / "profile.json"
    dump(profile, {"description": "Retulo 저장소의 실제 소스·검사·문서 파일 구성을 확인합니다. 이 보고서는 부분 재생성과 작업 레시피를 실제 파일 제작에 적용한 결과입니다."})
    original_hashes = {str(path): sha(path) for path in (baseline, current, alternate, profile)}
    config = destination / "config.json"
    dump(config, {"schema_version": 1, "workspace_home": str(destination / "workspaces"), "state_home": str(destination / "state"), "allowed_source_roots": [str(inputs)]})
    host = Host(config)
    criteria = [{"id": "report-valid", "text": "Report totals, embedded data and chart match the exact current inputs."}]
    created = host.call("begin_task", start_request_id="demo-report", title="Retulo source composition report", goal="Produce and incrementally update an actual source inventory report", completion_criteria=criteria)
    task = created["task_id"]
    params = {"title": "Retulo 소스 구성 보고서", "as_of": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")}
    environment = {"report_producer": sha(PRODUCER)}
    host.mutate("define_workflow", task, definition=graph(), parameters=params, environment=environment)
    host.source(task, "inventory", baseline); host.source(task, "profile", profile)
    first_executed = host.execute(task)
    first = host.complete(task)
    first_report_ref = first["active_outputs"]["report"]["outputs"]["report"]
    first_report = host.artifact(task, first_report_ref); first_hash = sha(first_report)
    profile_run = first["active_outputs"]["profile"]["run_id"]

    host.mutate("resume_task", task, reopen=True, reason="Update the report from the captured baseline to the current repository inventory")
    host.source(task, "inventory", current)
    updated_executed = host.execute(task)
    updated = host.complete(task)
    assert "profile" not in updated_executed, "Unchanged profile was executed again"
    assert set(updated_executed) == {"aggregate", "chart", "summary", "report", "validate"}
    assert updated["active_outputs"]["profile"]["run_id"] == profile_run
    assert sha(first_report) == first_hash, "Previous final report was changed"
    current_report = host.artifact(task, updated["active_outputs"]["report"]["outputs"]["report"])
    assert sha(current_report) != first_hash
    frozen_origin = hashlib.sha256(json.dumps(updated, sort_keys=True).encode()).hexdigest()

    authoring = host.call("begin_task", start_request_id="demo-recipe-authoring", title="Source report recipe", goal="Save the successful report workflow as a reusable recipe")
    author_task = authoring["task_id"]
    run_ids = [value["run_id"] for value in updated["active_outputs"].values()]
    recipe = {"recipe_schema_version": 1, "recipe_id": str(uuid.uuid4()), "revision": 1,
              "title": "Source composition report", "description": "Real file inventory to chart, summary and self-contained HTML with validation.",
              "workflow": updated["workflow"]["definition"], "completion_criteria": criteria,
              "origin": {"task_id": task, "workflow_revision": updated["workflow"]["revision"], "workflow_digest": updated["workflow"]["digest"], "run_ids": run_ids},
              "qualification": [{"case_id": "actual-source-inventory", "task_id": task, "workflow_digest": updated["workflow"]["digest"], "run_ids": run_ids}]}
    reservation = host.mutate("reserve_output", author_task, name="source-report-recipe.json", role="recipe", format="json")
    dump(reservation["write_path"], recipe)
    artifact = host.mutate("commit_artifact", author_task, reservation_id=reservation["reservation_id"], token=reservation["token"])["artifact"]
    recipe_args = {"recipe_task_id": author_task, "recipe_artifact_id": artifact["artifact_id"], "recipe_artifact_revision": artifact["revision"]}
    recipe_check = host.call("check_recipe", **recipe_args)
    host.mutate("complete_task", author_task)
    instantiate_args = {"start_request_id": "demo-recipe-instance", **recipe_args,
                        "input_bindings": {"inventory": {"path": str(alternate)}, "profile": {"path": str(profile)}},
                        "parameters": {**params, "title": "Retulo 검사·문서 구성 보고서"}, "environment": environment,
                        "title": "Retulo tests and documentation report", "goal": "Use the same recipe for a different actual inventory"}
    instance = host.call("begin_from_recipe", **instantiate_args)
    replay = host.call("begin_from_recipe", **instantiate_args)
    assert replay["task_id"] == instance["task_id"] and replay["replayed"] is True
    seed = host.state(instance["task_id"])
    assert seed["approvals"] == [] and seed["criteria_results"] == [] and seed["runs"] == []
    recipe_executed = host.execute(instance["task_id"])
    final = host.complete(instance["task_id"])
    final_report = host.artifact(instance["task_id"], final["active_outputs"]["report"]["outputs"]["report"])
    assert hashlib.sha256(json.dumps(host.state(task), sort_keys=True).encode()).hexdigest() == frozen_origin
    assert {name: sha(name) for name in original_hashes} == original_hashes
    summary = {"status": "passed", "execution": "separate real CLI processes plus local report producer; no model or global installation changes",
               "source_data": "actual repository inventories", "task_id": task, "recipe_task_id": author_task, "instance_task_id": instance["task_id"],
               "initial_steps": first_executed, "regenerated_steps": updated_executed, "reused_steps": ["profile"], "recipe_steps": recipe_executed,
               "previous_final_preserved": True, "input_files_preserved": True, "origin_task_unchanged_by_recipe": True,
               "recipe_start_replayed_same_task": True, "approvals_and_verdicts_not_inherited": True,
               "recipe_check": recipe_check, "reports": {"baseline": str(first_report), "updated": str(current_report), "recipe": str(final_report)},
               "config": str(config), "cli_calls": len(host.events), "events": host.events,
               "visual_review": "not_performed_by_this_script", "failure_recovery": "covered separately by focused regression tests"}
    dump(args.evidence_path or destination / "evidence.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "events"}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-zip", type=Path)
    parser.add_argument("--evidence-path", type=Path)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
