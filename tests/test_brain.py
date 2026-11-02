import copy
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import anyio

from retulo.brain import BrainBridge, BrainError, canonical, payload_digest


class EngineStub:
    def __init__(self, root):
        self.state_home = root
        self.revision = 1
        self.artifacts = []
        self.files_ok = True

    def get_context(self, task_id, detail="compact"):
        if task_id != "task-1":
            raise ValueError("unknown task")
        return {"task_id": task_id, "revision": self.revision, "state": {"decisions": [],
            "artifacts": copy.deepcopy(self.artifacts),
            "latest": {a["artifact_id"]: a["revision"] for a in self.artifacts}}}

    def verify(self, task_id):
        self.get_context(task_id)
        return {"task_id": task_id, "ok": self.files_ok, "revision": self.revision}


class FakePeer:
    def __init__(self):
        self.rows = {}
        self.capture_calls = 0
        self.drop_response = False
        self.has_receipt = True
        self.prepare_response = None
        self.evidence_quote = ""

    @asynccontextmanager
    async def session(self):
        yield self

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in
            (["capture", "capture_receipt"] if self.has_receipt else ["capture"])])

    async def call(self, session, name, arguments):
        if name == "capture_receipt":
            receipt = self.rows.get(arguments["event_id"])
            return {"found": receipt is not None, "receipt": copy.deepcopy(receipt)}
        if name == "capture":
            self.capture_calls += 1
            if arguments["evidence"][0]["source_id"] == "unknown":
                raise BrainError("brain_rejected", "unknown source")
            identity = arguments["idempotency"]
            receipt = {**identity, "scope": arguments["scope"], "memory_id": "memory-1",
                       "evidence_ids": ["evidence-1"], "status": "accepted", "revision": 1}
            self.rows[identity["event_id"]] = receipt
            if self.drop_response:
                self.drop_response = False
                raise TimeoutError("reply lost after commit")
            return {"idempotency_receipt": receipt, "replayed": False}
        if name == "read_evidence":
            return {"id": arguments["evidence_id"], "current": True, "stale": False, "quote": self.evidence_quote}
        if name == "prepare":
            return self.prepare_response or {"results": [], "scope": arguments["scope"]}
        raise AssertionError(name)


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = EngineStub(Path(self.temp.name))
        self.peer = FakePeer()
        self.bridge = BrainBridge(self.engine, {"scope": "/synthetic/workspaces"}, self.peer)
        self.args = dict(task_id="task-1", event_id="verified-1", title="Observed result",
                         body='{"ok":true}', source_id="source-1", quote='{"ok":true}')

    def test_digest_wire_contract(self):
        payload = dict(kind="task", title=" Example ", body=" exact ", scope="/test", evidence=[], metadata=None)
        semantic = dict(kind="task", title="Example", body="exact", scope="/test", evidence=[], metadata={}, supersedes=None)
        expected = hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertEqual(expected, payload_digest(payload))

    def test_replay_uses_remote_receipt_and_original_revision(self):
        first = anyio.run(lambda: self.bridge.capture(**self.args))
        self.engine.revision = 4
        second = anyio.run(lambda: self.bridge.capture(**self.args))
        self.assertEqual(first["receipt"], second["receipt"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.peer.capture_calls, 1)
        record = json.loads(next(self.bridge.outbox.rglob("*.json")).read_text())
        self.assertEqual(record["payload"]["metadata"]["workspace_revision"], 1)

    def test_lost_response_reconciles_without_duplicate_capture(self):
        self.peer.drop_response = True
        with self.assertRaises(TimeoutError):
            anyio.run(lambda: self.bridge.capture(**self.args))
        record = json.loads(next(self.bridge.outbox.rglob("*.json")).read_text())
        self.assertEqual(record["status"], "sync_unknown")
        replay = anyio.run(lambda: self.bridge.sync("task-1", "verified-1"))
        self.assertEqual(replay["brain_sync"], "synced")
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.peer.capture_calls, 1)

    def test_nested_transport_timeout_keeps_cause_and_reconciles_same_event(self):
        original_call = self.peer.call
        self.peer.drop_response = True

        async def grouped(session, name, arguments):
            try:
                return await original_call(session, name, arguments)
            except Exception as exc:
                raise ExceptionGroup("stdio", [ExceptionGroup("session", [exc])])

        self.peer.call = grouped
        with self.assertRaises(TimeoutError):
            anyio.run(lambda: self.bridge.capture(**self.args))
        record = json.loads(next(self.bridge.outbox.rglob("*.json")).read_text())
        self.assertEqual(record["status"], "sync_unknown")
        self.assertEqual(record["error"], "TimeoutError")
        replay = anyio.run(lambda: self.bridge.sync("task-1", "verified-1"))
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.peer.capture_calls, 1)

    def test_nested_receiver_rejection_does_not_become_an_internal_success(self):
        original_call = self.peer.call

        async def grouped(session, name, arguments):
            try:
                return await original_call(session, name, arguments)
            except Exception as exc:
                raise ExceptionGroup("stdio", [ExceptionGroup("session", [exc])])

        self.peer.call = grouped
        with self.assertRaises(BrainError) as caught:
            anyio.run(lambda: self.bridge.capture(**{**self.args, "source_id": "unknown"}))
        self.assertEqual(caught.exception.code, "brain_rejected")
        record = json.loads(next(self.bridge.outbox.rglob("*.json")).read_text())
        self.assertEqual(record["error"], "brain_rejected")
        self.assertNotIn("receipt", record)

    def test_conflict_rejected_without_overwriting_event(self):
        anyio.run(lambda: self.bridge.capture(**self.args))
        before = next(self.bridge.outbox.rglob("*.json")).read_bytes()
        with self.assertRaises(BrainError) as caught:
            anyio.run(lambda: self.bridge.capture(**{**self.args, "body": "different"}))
        self.assertEqual(caught.exception.code, "idempotency_conflict")
        self.assertEqual(before, next(self.bridge.outbox.rglob("*.json")).read_bytes())

    def test_pending_receiver_effects_are_repaired_on_sync(self):
        first = anyio.run(lambda: self.bridge.capture(**self.args))
        event = first["receipt"]["event_id"]
        self.peer.rows[event]["postprocessing"] = {"status": "pending"}
        original_call = self.peer.call

        async def repair(session, name, arguments):
            result = await original_call(session, name, arguments)
            if name == "capture":
                result["idempotency_receipt"]["postprocessing"] = {"status": "complete"}
            return result

        self.peer.call = repair
        second = anyio.run(lambda: self.bridge.sync("task-1", "verified-1"))
        self.assertEqual(self.peer.capture_calls, 2)
        self.assertTrue(second["replayed"])
        self.assertEqual(second["postprocessing_status"], "complete")
        self.assertEqual(second["receipt"]["memory_id"], first["receipt"]["memory_id"])
        self.assertEqual(second["receipt"]["revision"], 1)
        anyio.run(lambda: self.bridge.sync("task-1", "verified-1"))
        self.assertEqual(self.peer.capture_calls, 2)

    def test_degraded_receiver_effects_remain_visible_after_retry(self):
        first = anyio.run(lambda: self.bridge.capture(**self.args))
        event = first["receipt"]["event_id"]
        self.peer.rows[event]["postprocessing"] = {"status": "degraded"}
        original_call = self.peer.call

        async def still_degraded(session, name, arguments):
            result = await original_call(session, name, arguments)
            if name == "capture":
                result["idempotency_receipt"]["postprocessing"] = {"status": "degraded"}
            return result

        self.peer.call = still_degraded
        second = anyio.run(lambda: self.bridge.sync("task-1", "verified-1"))
        self.assertEqual(second["brain_sync"], "synced")
        self.assertEqual(second["postprocessing_status"], "degraded")
        record = json.loads(next(self.bridge.outbox.rglob("*.json")).read_text())
        self.assertEqual(record["receipt"]["postprocessing"]["status"], "degraded")

    def test_source_rejection_does_not_claim_synced(self):
        with self.assertRaises(BrainError):
            anyio.run(lambda: self.bridge.capture(**{**self.args, "source_id": "unknown"}))
        record = json.loads(next(self.bridge.outbox.rglob("*.json")).read_text())
        self.assertEqual(record["status"], "sync_unknown")
        self.assertNotIn("receipt", record)

    def test_missing_receiver_capability_does_not_send_capture(self):
        self.peer.has_receipt = False
        with self.assertRaises(BrainError) as caught:
            anyio.run(lambda: self.bridge.capture(**self.args))
        self.assertEqual(caught.exception.code, "receipt_capability_missing")
        self.assertEqual(self.peer.capture_calls, 0)

    def test_modified_outbox_is_rejected(self):
        anyio.run(lambda: self.bridge.capture(**self.args))
        path = next(self.bridge.outbox.rglob("*.json"))
        record = json.loads(path.read_text()); record["payload"]["body"] = "forged"
        path.write_text(canonical(record))
        with self.assertRaises(BrainError) as caught:
            anyio.run(lambda: self.bridge.sync("task-1", "verified-1"))
        self.assertEqual(caught.exception.code, "outbox_modified")

    def test_review_returns_actual_evidence_and_does_not_mark_inferences(self):
        anyio.run(lambda: self.bridge.capture(**self.args))
        report = anyio.run(lambda: self.bridge.review_context("task-1"))
        self.assertTrue(report["filesystem_verification"]["ok"])
        self.assertEqual(len(report["evidence"]), 1)
        self.assertEqual(report["past_events"][0]["body_excerpt"]["text"], self.args["body"])

    def test_current_source_does_not_revalidate_a_new_artifact_version(self):
        self.engine.artifacts = [{"artifact_id": "a", "revision": 1, "sha256": "first"}]
        anyio.run(lambda: self.bridge.capture(**self.args))
        path = next(self.bridge.outbox.rglob("*.json")); before = path.read_bytes()
        self.engine.artifacts = [{"artifact_id": "a", "revision": 2, "sha256": "second"}]
        self.engine.revision = 4
        report = anyio.run(lambda: self.bridge.review_context("task-1"))
        event = report["past_events"][0]
        self.assertTrue(report["evidence"][0]["current"])
        self.assertEqual(event["captured_workspace_revision"], 1)
        self.assertEqual(event["current_workspace_revision"], 4)
        self.assertFalse(event["registered_artifacts_match"])
        self.assertEqual(event["applicability"], "requires_revalidation")
        self.assertFalse(event["user_decision_revoked"])
        self.assertEqual(before, path.read_bytes())

    def test_unregistered_physical_edit_still_requires_revalidation(self):
        anyio.run(lambda: self.bridge.capture(**self.args))
        self.engine.files_ok = False
        report = anyio.run(lambda: self.bridge.review_context("task-1"))
        self.assertTrue(report["past_events"][0]["registered_artifacts_match"])
        self.assertEqual(report["past_events"][0]["applicability"], "requires_revalidation")

    def test_workspace_change_during_remote_evidence_read_is_not_reported_as_current(self):
        self.engine.artifacts = [{"artifact_id": "a", "revision": 1, "sha256": "first"}]
        anyio.run(lambda: self.bridge.capture(**self.args))
        original_call = self.peer.call

        async def changed_during_read(session, name, arguments):
            if name == "read_evidence":
                self.engine.revision = 2
                self.engine.artifacts = [{"artifact_id": "a", "revision": 2, "sha256": "second"}]
            return await original_call(session, name, arguments)

        self.peer.call = changed_during_read
        report = anyio.run(lambda: self.bridge.review_context("task-1"))
        self.assertEqual(report["workspace"]["revision"], 2)
        self.assertEqual(report["filesystem_verification"]["revision"], 2)
        self.assertEqual(report["past_events"][0]["current_workspace_revision"], 2)
        self.assertEqual(report["past_events"][0]["applicability"], "requires_revalidation")
        self.assertFalse(report["past_events"][0]["registered_artifacts_match"])

    def test_legacy_event_has_unknown_artifact_applicability_without_mutation(self):
        anyio.run(lambda: self.bridge.capture(**self.args))
        path = next(self.bridge.outbox.rglob("*.json")); record = json.loads(path.read_text())
        del record["payload"]["metadata"]["workspace_snapshot"]
        record["payload"]["idempotency"]["payload_digest"] = payload_digest(record["payload"])
        path.write_text(canonical(record)); before = path.read_bytes()
        report = anyio.run(lambda: self.bridge.review_context("task-1"))
        self.assertIsNone(report["past_events"][0]["registered_artifacts_match"])
        self.assertEqual(report["past_events"][0]["applicability"], "requires_revalidation")
        self.assertEqual(path.read_bytes(), before)

    def test_large_remote_scaffold_has_bounded_projection_and_full_recovery(self):
        self.peer.prepare_response = {"memory_context": "기록" * 100000,
            "results": [{"id": "memory-1", "body": "원문" * 30000}],
            "task_spec": {"evidence": [{"memory_id": "memory-1", "evidence_ids": ["evidence-1"]}]}}
        compact = anyio.run(lambda: self.bridge.prepare("task-1"))
        full = anyio.run(lambda: self.bridge.prepare("task-1", detail="full"))
        self.assertLessEqual(len(canonical(compact).encode()), 49152)
        self.assertNotIn("memory_context", compact["brain"])
        self.assertEqual(compact["brain"]["evidence_refs"][0]["evidence_ids"], ["evidence-1"])
        self.assertEqual(full["brain"], self.peer.prepare_response)

    def test_large_event_history_is_bounded_and_exact_event_can_be_read(self):
        body = "실패 근거를 보존합니다. " * 1500
        self.peer.evidence_quote = "출처" * 20000
        for index in range(12):
            anyio.run(lambda: self.bridge.capture(**{**self.args, "event_id": f"event-{index}", "body": body}))
        compact = anyio.run(lambda: self.bridge.review_context("task-1"))
        self.assertLessEqual(len(canonical(compact).encode()), 49152)
        self.assertEqual(compact["event_count"], 12)
        self.assertEqual(compact["returned_events"], 10)
        self.assertTrue(compact["past_events"][0]["body_excerpt"]["truncated"])
        full = anyio.run(lambda: self.bridge.review_context("task-1", detail="full", event_id="event-0"))
        self.assertEqual(len(full["past_events"]), 1)
        self.assertEqual(full["past_events"][0]["body"], body)
        self.assertEqual(full["evidence"][0]["quote"], self.peer.evidence_quote)
        with self.assertRaises(BrainError):
            anyio.run(lambda: self.bridge.review_context("task-1", event_id="missing"))

    def test_invalid_detail_rejected_without_creating_records(self):
        with self.assertRaises(BrainError):
            anyio.run(lambda: self.bridge.prepare("task-1", detail="guess"))
        with self.assertRaises(BrainError):
            anyio.run(lambda: self.bridge.review_context("task-1", detail="guess"))
        self.assertFalse(self.bridge.outbox.exists())

    def test_unknown_task_and_readonly_receipt_create_no_outbox(self):
        result = anyio.run(lambda: self.bridge.receipt("task-1", "nothing"))
        self.assertFalse(result["found"])
        self.assertFalse(self.bridge.outbox.exists())
        with self.assertRaises(ValueError):
            anyio.run(lambda: self.bridge.capture(**{**self.args, "task_id": "other"}))
        self.assertFalse(self.bridge.outbox.exists())

    def test_review_does_not_read_another_tasks_corrupt_outbox(self):
        anyio.run(lambda: self.bridge.capture(**self.args))
        unrelated = self.bridge._task_outbox("other-task")
        unrelated.mkdir()
        (unrelated / "broken.json").write_text("invalid json")
        report = anyio.run(lambda: self.bridge.review_context("task-1"))
        self.assertEqual(len(report["past_events"]), 1)
        self.assertEqual(report["past_events"][0]["event_id"], "verified-1")

    def test_task_outbox_symlink_is_rejected_before_writing(self):
        self.bridge.outbox.mkdir()
        outside = Path(self.temp.name) / "outside-task"
        outside.mkdir()
        self.bridge._task_outbox("task-1").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BrainError):
            anyio.run(lambda: self.bridge.capture(**self.args))
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_outbox_rejected(self):
        other = Path(self.temp.name)/"outside"; other.mkdir()
        self.bridge.outbox.symlink_to(other, target_is_directory=True)
        with self.assertRaises(BrainError):
            anyio.run(lambda: self.bridge.capture(**self.args))
        self.assertEqual(list(other.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
