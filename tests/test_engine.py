import copy
import hashlib
import json
import multiprocessing
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from retulo.engine import Engine, EngineError
from retulo import engine as engine_module


def start_concurrently(home, queue):
    try:
        result = Engine(home).begin_task("same-request", "Shared", "One shared task")
        queue.put(("ok", result["task_id"]))
    except Exception as exc:
        queue.put(("error", str(exc)))


def update_concurrently(home, task_id, op, queue):
    try:
        Engine(home).checkpoint(task_id, op, 1, {"next_action": op})
        queue.put("ok")
    except EngineError as exc:
        queue.put(exc.code)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "workspaces"
        self.sources = self.base / "originals"
        self.sources.mkdir()
        self.engine = Engine(self.home, allowed_source_roots=[self.sources])

    def tearDown(self):
        self.tmp.cleanup()

    def begin(self, criteria=None, **kwargs):
        return self.engine.begin_task("start", "Proposal", "Produce a useful document", criteria, **kwargs)

    def artifact(self, task, *, name="proposal.md", body="A grounded proposal", prefix="first"):
        current = self.engine.get_context(task["task_id"], detail="full")
        reserve = self.engine.reserve_output(task["task_id"], f"{prefix}:reserve", current["revision"], name)
        Path(reserve["write_path"]).write_text(body)
        commit = self.engine.commit_artifact(task["task_id"], f"{prefix}:commit", reserve["revision"], reserve["reservation_id"], reserve["token"])
        return reserve, commit

    def assertError(self, code, fn, *args, **kwargs):
        with self.assertRaises(EngineError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_read_only_calls_do_not_create_any_directories(self):
        self.assertFalse(self.home.exists())
        self.assertEqual([], self.engine.list_tasks()["tasks"])
        self.assertFalse(self.engine.capabilities()["features"]["model_dependency"])
        self.assertFalse(self.home.exists())

    def test_start_retry_has_same_uuid_path_and_revision(self):
        first = self.begin()
        second = self.begin()
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(first["workspace_path"], second["workspace_path"])
        self.assertEqual(1, second["revision"])
        self.assertTrue(second["replayed"])
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_start_key_reuse_with_different_payload_is_conflict(self):
        self.begin()
        self.assertError("idempotency_conflict", self.engine.begin_task, "start", "Other", "Different goal")

    def test_creation_recovers_if_registry_write_fails_after_task_created(self):
        real = engine_module.atomic_json
        observed = []
        def interrupted(path, payload):
            if Path(path).name == "index.json":
                observed.append(payload)
                raise OSError("simulated power loss before index")
            return real(path, payload)
        with patch.object(engine_module, "atomic_json", interrupted):
            with self.assertRaises(OSError):
                self.begin()
        task_id = next(iter(observed[0]))
        resumed = self.begin()
        self.assertEqual(task_id, resumed["task_id"])
        self.assertTrue(resumed["replayed"])
        self.assertEqual(1, len(list(self.home.glob("proposal-*"))))

    def test_existing_nonempty_directory_is_not_reorganized(self):
        root = self.home / "existing"
        root.mkdir(parents=True)
        original = root / "keep.txt"
        original.write_text("untouched")
        self.assertError("workspace_not_empty", self.engine.begin_task, "x", "X", "X", workspace_root=root)
        self.assertEqual("untouched", original.read_text())
        self.assertFalse((root / ".workspace").exists())

    def test_creation_outside_allowed_root_is_rejected(self):
        self.assertError("outside_scope", self.engine.begin_task, "x", "X", "X", workspace_root=self.base / "outside")

    def test_output_is_staged_then_published_without_overwrite(self):
        task = self.begin()
        reserved, committed = self.artifact(task)
        self.assertNotEqual(reserved["write_path"], reserved["final_path"])
        self.assertEqual("A grounded proposal", Path(reserved["final_path"]).read_text())
        self.assertEqual(hashlib.sha256(b"A grounded proposal").hexdigest(), committed["artifact"]["sha256"])
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])
        self.assertFalse((Path(task["workspace_path"]) / "WORKSPACE.md").exists())

    def test_commit_retry_is_idempotent_even_with_old_revision(self):
        task = self.begin()
        reserved, committed = self.artifact(task)
        repeated = self.engine.commit_artifact(task["task_id"], "first:commit", reserved["revision"], reserved["reservation_id"], reserved["token"])
        self.assertTrue(repeated["replayed"])
        self.assertEqual(committed["revision"], self.engine.get_context(task["task_id"], detail="full")["revision"])
        self.assertEqual(1, len(self.engine.get_context(task["task_id"], detail="full")["state"]["artifacts"]))

    def test_out_of_order_commits_do_not_move_latest_backwards(self):
        task = self.begin()
        first = self.engine.reserve_output(task["task_id"], "v1", 1, "result.md")
        second = self.engine.reserve_output(task["task_id"], "v2", first["revision"], "result.md")
        Path(first["write_path"]).write_text("older")
        Path(second["write_path"]).write_text("newer")
        v2 = self.engine.commit_artifact(task["task_id"], "commit-v2", second["revision"], second["reservation_id"], second["token"])
        self.engine.commit_artifact(task["task_id"], "commit-v1", v2["revision"], first["reservation_id"], first["token"])
        state = self.engine.get_context(task["task_id"], detail="full")["state"]
        self.assertEqual(2, state["latest"][first["artifact_id"]])
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])

    def test_adoption_allocates_after_reserved_versions_and_late_commit_remains_valid(self):
        task = self.begin()
        first, committed = self.artifact(task)
        reserved = self.engine.reserve_output(task["task_id"], "v2", committed["revision"], "proposal.md")
        Path(reserved["write_path"]).write_text("reserved version two")
        Path(first["final_path"]).write_text("external edit")
        adopted = self.engine.resume_task(task["task_id"], "adopt", reserved["revision"], adopt_changes=True)
        self.assertEqual(3, adopted["adopted"][0]["revision"])
        self.engine.commit_artifact(task["task_id"], "commit-v2", adopted["revision"], reserved["reservation_id"], reserved["token"])
        state = self.engine.get_context(task["task_id"], detail="full")["state"]
        self.assertEqual(3, state["latest"][committed["artifact"]["artifact_id"]])
        self.assertEqual("reserved version two", Path(reserved["final_path"]).read_text())
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])

    def test_commit_recovery_after_file_publication_before_state_save(self):
        task = self.begin()
        reserve = self.engine.reserve_output(task["task_id"], "reserve", 1, "out.txt")
        Path(reserve["write_path"]).write_text("complete bytes")
        real = engine_module.atomic_json
        def interrupted(path, value):
            if Path(path).name == "task.json":
                raise OSError("simulated interruption")
            return real(path, value)
        with patch.object(engine_module, "atomic_json", interrupted):
            with self.assertRaises(OSError):
                self.engine.commit_artifact(task["task_id"], "commit", reserve["revision"], reserve["reservation_id"], reserve["token"])
        self.assertEqual("complete bytes", Path(reserve["final_path"]).read_text())
        self.assertFalse(self.engine.verify(task["task_id"])["ok"])
        done = self.engine.commit_artifact(task["task_id"], "commit", reserve["revision"], reserve["reservation_id"], reserve["token"])
        self.assertTrue(done["replayed"])
        self.assertEqual(1, len(self.engine.get_context(task["task_id"], detail="full")["state"]["artifacts"]))
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])

    def test_wrong_token_and_late_commit_are_rejected(self):
        task = self.begin()
        reserved, committed = self.artifact(task)
        self.assertError("invalid_reservation", self.engine.commit_artifact, task["task_id"], "bad", committed["revision"], reserved["reservation_id"], "wrong")
        self.assertError("reservation_closed", self.engine.commit_artifact, task["task_id"], "late", committed["revision"], reserved["reservation_id"], reserved["token"])

    def test_empty_output_not_committed(self):
        task = self.begin()
        reserved = self.engine.reserve_output(task["task_id"], "reserve", 1, "out.txt")
        Path(reserved["write_path"]).touch()
        self.assertError("empty_artifact", self.engine.commit_artifact, task["task_id"], "commit", reserved["revision"], reserved["reservation_id"], reserved["token"])

    def test_abandoned_output_preserves_partial_bytes_and_allows_other_completion(self):
        task = self.begin()
        partial = self.engine.reserve_output(task["task_id"], "partial", 1, "partial.txt")
        Path(partial["write_path"]).write_text("unfinished bytes")
        _, committed = self.artifact(task)
        self.assertError("pending_output", self.engine.complete_task, task["task_id"], "early", committed["revision"])
        retired = self.engine.abandon_output(task["task_id"], "abandon", committed["revision"], partial["reservation_id"], partial["token"])
        self.assertEqual("unfinished bytes", Path(partial["write_path"]).read_text())
        self.assertError("reservation_closed", self.engine.commit_artifact, task["task_id"], "late", retired["revision"], partial["reservation_id"], partial["token"])
        completed = self.engine.complete_task(task["task_id"], "finish", retired["revision"])
        self.assertEqual("completed", completed["status"])

    def test_traversal_reserved_names_and_symlink_escape(self):
        task = self.begin()
        for name in ("../outside", "/tmp/out", ".workspace/task.json", "WORKSPACE.md", "CON.txt"):
            with self.assertRaises(EngineError):
                self.engine.reserve_output(task["task_id"], name, 1, name)
        root = Path(task["workspace_path"])
        (root / "linked").symlink_to(self.sources, target_is_directory=True)
        with self.assertRaises(EngineError):
            self.engine.reserve_output(task["task_id"], "link", 1, "linked/out.txt")
        self.assertFalse((self.sources / "out-v001.txt").exists())

    def test_destination_created_after_reservation_is_not_overwritten(self):
        task = self.begin()
        reserved = self.engine.reserve_output(task["task_id"], "reserve", 1, "out.txt")
        Path(reserved["write_path"]).write_text("new")
        Path(reserved["final_path"]).write_text("user-created")
        self.assertError("name_conflict", self.engine.commit_artifact, task["task_id"], "commit", reserved["revision"], reserved["reservation_id"], reserved["token"])
        self.assertEqual("user-created", Path(reserved["final_path"]).read_text())

    def test_original_reference_and_copy_preserve_original(self):
        source = self.sources / "original.txt"
        source.write_text("source facts")
        before = (source.read_bytes(), source.stat().st_mtime_ns)
        task = self.begin()
        linked = self.engine.add_source(task["task_id"], "reference", 1, source)
        copied = self.engine.add_source(task["task_id"], "copy", linked["revision"], source, mode="copy")
        self.assertEqual(before, (source.read_bytes(), source.stat().st_mtime_ns))
        self.assertEqual("source facts", (Path(task["workspace_path"]) / copied["source"]["path"]).read_text())
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])

    def test_source_outside_granted_roots_and_symlink_are_rejected(self):
        outside = self.base / "private.txt"
        outside.write_text("private")
        linked = self.sources / "link.txt"
        linked.symlink_to(outside)
        task = self.begin()
        self.assertError("outside_scope", self.engine.add_source, task["task_id"], "o", 1, outside)
        with self.assertRaises(EngineError):
            self.engine.add_source(task["task_id"], "l", 1, linked)

    def test_stale_revision_and_changed_payload_are_rejected(self):
        task = self.begin()
        changed = self.engine.checkpoint(task["task_id"], "change", 1, {"next_action": "Review"})
        self.assertError("revision_conflict", self.engine.checkpoint, task["task_id"], "stale", 1, {"next_action": "Old"})
        self.assertError("idempotency_conflict", self.engine.checkpoint, task["task_id"], "change", changed["revision"], {"next_action": "Different"})

    def test_expansion_preserves_paths_and_guide_is_projection(self):
        task = self.begin()
        reserved, committed = self.artifact(task)
        updated = self.engine.checkpoint(task["task_id"], "expand", committed["revision"], {"mode": "expanded", "next_action": "Review evidence"})
        context = self.engine.get_context(task["task_id"], detail="full")
        self.assertEqual(task["workspace_path"], updated["workspace_path"])
        self.assertTrue(Path(reserved["final_path"]).exists())
        self.assertIn(f"State revision: {updated['revision']}", context["guide"])
        self.assertIn("Review evidence", context["guide"])
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])

    def test_manual_guide_is_never_overwritten_and_blocks_completion(self):
        task = self.begin()
        _, artifact = self.artifact(task)
        expanded = self.engine.checkpoint(task["task_id"], "expand", artifact["revision"], {"mode": "expanded"})
        guide = Path(task["workspace_path"]) / "WORKSPACE.md"
        guide.write_text("My manual guide")
        changed = self.engine.checkpoint(task["task_id"], "next", expanded["revision"], {"next_action": "Another action"})
        self.assertEqual("My manual guide", guide.read_text())
        self.assertIn("guide_manually_modified", changed["warnings"])
        self.assertError("verification_failed", self.engine.complete_task, task["task_id"], "complete", changed["revision"])

    def test_decisions_separate_proposed_confirmed_and_superseded(self):
        task = self.begin()
        self.assertError("invalid_input", self.engine.checkpoint, task["task_id"], "invalid", 1, {"decisions": [{"id": "d1", "text": "Use chart", "status": "confirmed"}]})
        first = self.engine.checkpoint(task["task_id"], "decision", 1, {"decisions": [{"id": "d1", "text": "Use chart", "status": "confirmed", "evidence_ref": "user-message:synthetic-1"}]})
        self.assertError("decision_conflict", self.engine.checkpoint, task["task_id"], "rewrite", first["revision"], {"decisions": [{"id": "d1", "text": "Skip chart", "status": "confirmed", "evidence_ref": "user-message:synthetic-2"}]})
        self.engine.checkpoint(task["task_id"], "replace", first["revision"], {"decisions": [{"id": "d2", "text": "Skip chart", "status": "confirmed", "evidence_ref": "user-message:synthetic-2", "supersedes": "d1"}]})
        decisions = self.engine.get_context(task["task_id"], detail="full")["state"]["decisions"]
        self.assertEqual(["superseded", "confirmed"], [d["status"] for d in decisions])

    def test_approval_preserves_exact_version_and_is_not_inherited(self):
        task = self.begin()
        reserved, first = self.artifact(task)
        artifact = first["artifact"]
        approved = self.engine.record_approval(task["task_id"], "approve", first["revision"], artifact["artifact_id"], 1,
            {"actor": "user", "reference": "synthetic-user-message", "quote": "Approve version one visually", "scope": "visual"})
        snapshot = Path(task["workspace_path"]) / approved["approval"]["snapshot_path"]
        before = snapshot.read_bytes()
        _, second = self.artifact(task, prefix="second", body="A different revision")
        context = self.engine.get_context(task["task_id"], detail="full")["state"]
        self.assertEqual(2, second["artifact"]["revision"])
        self.assertEqual(1, context["approvals"][0]["artifact_revision"])
        self.assertEqual(before, snapshot.read_bytes())
        self.assertEqual("A grounded proposal", Path(reserved["final_path"]).read_text())

    def test_tampered_artifact_cannot_be_approved(self):
        task = self.begin()
        reserved, first = self.artifact(task)
        Path(reserved["final_path"]).write_text("unreviewed changed bytes")
        self.assertError("artifact_changed", self.engine.record_approval, task["task_id"], "approve", first["revision"], first["artifact"]["artifact_id"], 1,
            {"actor": "user", "reference": "x", "quote": "approve", "scope": "visual"})

    def test_resume_other_chat_keeps_task_identity(self):
        task = self.begin(binding={"provider": "codex", "conversation_id": "synthetic-a"})
        resumed = self.engine.resume_task(task["task_id"], "bind-b", 1, {"provider": "another-agent", "conversation_id": "synthetic-b"})
        self.assertEqual(task["workspace_path"], resumed["workspace_path"])
        self.assertEqual(2, len(self.engine.get_context(task["task_id"], detail="full")["state"]["bindings"]))
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))

    def test_external_change_is_reported_then_explicitly_adopted_without_approval(self):
        task = self.begin()
        reserved, first = self.artifact(task)
        Path(reserved["final_path"]).write_text("user-edited document")
        state_before = (Path(task["workspace_path"]) / ".workspace/task.json").read_bytes()
        observed = self.engine.resume_task(task["task_id"])
        self.assertFalse(observed["verification"]["ok"])
        self.assertEqual(state_before, (Path(task["workspace_path"]) / ".workspace/task.json").read_bytes())
        adopted = self.engine.resume_task(task["task_id"], "adopt", first["revision"], adopt_changes=True)
        self.assertEqual("not_inherited", adopted["adopted"][0]["approval"])
        self.assertTrue(adopted["verification"]["ok"])

    def test_existing_repository_code_is_only_hashed_not_moved(self):
        repository = self.sources / "repo"
        repository.mkdir()
        source = repository / "app.py"
        source.write_text("print('hello')\n")
        before = (source.read_bytes(), source.stat().st_mtime_ns)
        task = self.begin(source_root=repository)
        recorded = self.engine.record_external_artifact(task["task_id"], "code", 1, source, verification={"reference": "synthetic-test-output", "result": "passed"})
        self.assertEqual(before, (source.read_bytes(), source.stat().st_mtime_ns))
        self.assertEqual("app.py", recorded["artifact"]["path"])
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])
        self.assertEqual([source], list(repository.iterdir()))

    def test_complete_requires_actual_artifact_and_criteria_evidence(self):
        task = self.begin(criteria=["Document checked"])
        self.assertError("no_artifact", self.engine.complete_task, task["task_id"], "empty", 1)
        _, committed = self.artifact(task)
        self.assertError("criteria_pending", self.engine.complete_task, task["task_id"], "unvalidated", committed["revision"])
        self.assertError("unknown_artifact", self.engine.checkpoint, task["task_id"], "fake", committed["revision"], {"criteria_results": [{"criterion": "Document checked", "status": "passed", "evidence_ref": "made-up"}]})
        checked = self.engine.checkpoint(task["task_id"], "checked", committed["revision"], {"criteria_results": [{"criterion": "Document checked", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"]}]})
        done = self.engine.complete_task(task["task_id"], "done", checked["revision"])
        self.assertEqual("completed", done["status"])
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])

    def test_approval_required_criterion_does_not_accept_latest_filename(self):
        task = self.begin(criteria=[{"id": "approve", "text": "User visual approval", "requires_approval": True}])
        _, committed = self.artifact(task, name="final.md")
        result = self.engine.checkpoint(task["task_id"], "checked", committed["revision"], {"criteria_results": [{"criterion": "approve", "status": "passed", "evidence_ref": committed["artifact"]["artifact_id"]}]})
        self.assertError("criteria_pending", self.engine.complete_task, task["task_id"], "done", result["revision"])

    def test_paused_task_is_not_implicitly_resumed_by_output(self):
        task = self.begin()
        paused = self.engine.checkpoint(task["task_id"], "pause", 1, {"status": "paused"})
        self.assertError("task_not_active", self.engine.reserve_output, task["task_id"], "out", paused["revision"], "out.txt")
        self.assertEqual("paused", self.engine.resume_task(task["task_id"])["state"]["status"])

    def test_completed_task_reopens_only_explicitly_and_keeps_completion_history(self):
        task = self.begin(criteria=["Document exists"])
        _, committed = self.artifact(task)
        artifact_id = committed["artifact"]["artifact_id"]
        checked = self.engine.checkpoint(task["task_id"], "check", committed["revision"], {"criteria_results": [{"criterion": "Document exists", "status": "passed", "evidence_ref": artifact_id}]})
        approved = self.engine.record_approval(task["task_id"], "approve", checked["revision"], artifact_id, 1,
            {"actor": "user", "reference": "synthetic-user", "quote": "approve", "scope": "visual"})
        done = self.engine.complete_task(task["task_id"], "done", approved["revision"])
        self.assertEqual("completed", self.engine.resume_task(task["task_id"])["state"]["status"])
        self.assertError("invalid_input", self.engine.resume_task, task["task_id"], "invalid-reopen", done["revision"], reopen=True)
        reopened = self.engine.resume_task(task["task_id"], "reopen", done["revision"], reopen=True, reason="User requested one more section")
        state = self.engine.get_context(task["task_id"], detail="full")["state"]
        self.assertTrue(reopened["reopened"])
        self.assertEqual("active", state["status"])
        self.assertEqual("pending", state["criteria_results"][0]["status"])
        self.assertEqual("passed", state["completion_history"][0]["criteria_results"][0]["status"])
        self.assertEqual(1, len(state["approvals"]))
        self.assertEqual(task["workspace_path"], reopened["workspace_path"])

    def test_visual_approval_does_not_satisfy_publication_approval(self):
        task = self.begin(criteria=[{"id": "publish", "text": "Publishing approved", "requires_approval": True, "approval_scope": "publication"}])
        _, committed = self.artifact(task)
        artifact_id = committed["artifact"]["artifact_id"]
        result = self.engine.checkpoint(task["task_id"], "checked", committed["revision"], {"criteria_results": [{"criterion": "publish", "status": "passed", "evidence_ref": artifact_id}]})
        approved = self.engine.record_approval(task["task_id"], "visual", result["revision"], artifact_id, 1,
            {"actor": "user", "reference": "synthetic-visual", "quote": "Visual approval only", "scope": "visual"})
        self.assertError("criteria_pending", self.engine.complete_task, task["task_id"], "done", approved["revision"])

    def test_two_processes_share_one_start_and_cas_serializes_changes(self):
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        workers = [context.Process(target=start_concurrently, args=(str(self.home), queue)) for _ in range(2)]
        for process in workers:
            process.start()
        results = [queue.get(timeout=15) for _ in workers]
        for process in workers:
            process.join(timeout=15)
            self.assertEqual(0, process.exitcode)
        self.assertEqual(["ok", "ok"], [r[0] for r in results])
        self.assertEqual(results[0][1], results[1][1])
        task_id = results[0][1]
        workers = [context.Process(target=update_concurrently, args=(str(self.home), task_id, f"op-{i}", queue)) for i in range(2)]
        for process in workers:
            process.start()
        outcomes = [queue.get(timeout=15) for _ in workers]
        for process in workers:
            process.join(timeout=15)
            self.assertEqual(0, process.exitcode)
        self.assertEqual(["ok", "revision_conflict"], sorted(outcomes))

    def test_explicit_path_adoption_keeps_identity_and_approved_snapshot(self):
        task = self.begin()
        reserved, first = self.artifact(task)
        artifact_id = first["artifact"]["artifact_id"]
        approved = self.engine.record_approval(task["task_id"], "approve", first["revision"], artifact_id, 1,
            {"actor": "user", "reference": "synthetic", "quote": "approve visual", "scope": "visual"})
        root = Path(task["workspace_path"])
        moved = root / "deliverables" / "renamed.md"
        moved.parent.mkdir()
        Path(reserved["final_path"]).rename(moved)
        self.assertFalse(self.engine.verify(task["task_id"])["ok"])
        adopted = self.engine.resume_task(task["task_id"], "move", approved["revision"], path_updates={artifact_id: "deliverables/renamed.md"})
        self.assertTrue(adopted["verification"]["ok"])
        state = self.engine.get_context(task["task_id"], detail="full")["state"]
        self.assertEqual(1, state["latest"][artifact_id])
        self.assertEqual("A grounded proposal", (root / state["approvals"][0]["snapshot_path"]).read_text())
        self.assertTrue(moved.exists())

    def test_path_adoption_requires_same_bytes_and_safe_destination(self):
        task = self.begin()
        _, first = self.artifact(task)
        root = Path(task["workspace_path"])
        (root / "other.md").write_text("different")
        artifact_id = first["artifact"]["artifact_id"]
        self.assertError("artifact_changed", self.engine.resume_task, task["task_id"], "move", first["revision"], path_updates={artifact_id: "other.md"})
        self.assertError("unsafe_path", self.engine.resume_task, task["task_id"], "escape", first["revision"], path_updates={artifact_id: "../other.md"})

    def test_attach_moved_workspace_repairs_start_receipt_and_registry(self):
        task = self.begin()
        _, committed = self.artifact(task)
        original = Path(task["workspace_path"])
        moved = self.home / "user-chosen-name"
        original.rename(moved)
        attached = self.engine.attach_workspace(moved)
        self.assertEqual(task["task_id"], attached["task_id"])
        self.assertTrue(attached["moved"])
        retry = self.begin()
        self.assertEqual(str(moved), retry["workspace_path"])
        self.assertFalse(original.exists())
        self.assertEqual(committed["revision"], retry["revision"])
        self.assertTrue(self.engine.verify(task["task_id"])["ok"])

    def test_attach_rebuilds_missing_index_but_rejects_live_duplicate(self):
        task = self.begin()
        original = Path(task["workspace_path"])
        duplicate = self.home / "copied-workspace"
        shutil.copytree(original, duplicate)
        self.assertError("duplicate_task_location", self.engine.attach_workspace, duplicate)
        (self.engine.state_home / "index.json").unlink()
        attached = self.engine.attach_workspace(original)
        self.assertEqual(task["task_id"], attached["task_id"])
        self.assertEqual(1, len(self.engine.list_tasks()["tasks"]))


if __name__ == "__main__":
    unittest.main()
