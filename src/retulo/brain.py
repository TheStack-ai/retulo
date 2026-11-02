"""Optional Main Brain MCP client. No direct Brain database access or AI calls.

Only native/admin-registered evidence can be submitted. Durable outbox requests
are immutable, and an ambiguous send is reconciled through an exact receipt.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class BrainError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _underlying_error(exc):
    """MCP/AnyIO contexts may nest a single rejection inside task-group errors."""
    leaves = []
    def collect(value):
        if isinstance(value, ExceptionGroup):
            for child in value.exceptions:
                collect(child)
        else:
            leaves.append(value)
    collect(exc)
    if len(leaves) == 1:
        return leaves[0]
    return BrainError("brain_transport_error", "Main Brain session failed: " +
                      ", ".join(sorted({type(e).__name__ for e in leaves}))[:300])


def payload_digest(payload):
    semantic = {"kind": payload["kind"], "title": payload["title"].strip(),
                "body": payload["body"].strip(), "scope": payload["scope"],
                "evidence": payload["evidence"], "metadata": payload.get("metadata") or {},
                "supersedes": payload.get("supersedes") or None}
    return hashlib.sha256(canonical(semantic).encode()).hexdigest()


def _detail(value):
    if value not in ("compact", "full"):
        raise BrainError("invalid_detail", "Detail must be compact or full")
    return value


def _excerpt(value, budget=600):
    raw = str(value or "").encode()
    return {"text": raw[:budget].decode(errors="ignore"), "original_bytes": len(raw),
            "truncated": len(raw) > budget}


def _snapshot(context):
    state = context["state"]
    latest = state.get("latest", {})
    artifacts = sorted(({"artifact_id": a["artifact_id"], "revision": a["revision"],
                         "sha256": a["sha256"]} for a in state.get("artifacts", [])
                        if latest.get(a["artifact_id"]) == a["revision"]),
                       key=lambda a: (a["artifact_id"], a["revision"]))
    return {"workspace_revision": context["revision"], "artifact_count": len(artifacts),
            "artifacts_digest": hashlib.sha256(canonical(artifacts).encode()).hexdigest()}


def _evidence_card(item):
    card = {k: item[k] for k in ("id", "evidence_id", "source_id", "stale", "current", "role",
                                "source_role", "valid", "status") if k in item}
    card["quote_excerpt"] = _excerpt(item.get("quote", item.get("excerpt", "")), 240)
    card["meaning"] = "Source validity is not proof this evidence verifies the current artifact version"
    return card


def _prepare_card(memory):
    results = memory.get("results", [])
    refs = memory.get("references", [])
    evidence = memory.get("task_spec", {}).get("evidence", [])
    def result_card(row):
        out = {k: _excerpt(row[k], 350)["text"] for k in
               ("id", "title", "scope", "review_status", "evidence_status", "evidence_id") if k in row}
        out["excerpt"] = _excerpt(row.get("excerpt", row.get("body", "")), 500)
        return out
    return {"decision": memory.get("decision"), "degraded": memory.get("degraded"),
            "results": [result_card(r) for r in results[:8]],
            "references": [result_card(r) for r in refs[:4]],
            "evidence_refs": [{k: row[k] for k in ("memory_id", "evidence_ids", "source_ids") if k in row}
                              for row in evidence[:8]],
            "counts": {"results": len(results), "references": len(refs), "evidence_refs": len(evidence)},
            "omitted": "Full memory bodies, task_spec, workflow and memory_context; read relevant evidence before acting",
            "recover": "brain_prepare(detail='full') or Main Brain read_evidence(evidence_id)"}


def _compact_finish(result, original_bytes):
    """Bound model-facing content, never the durable record or the receiver's API."""
    result["response_projection"] = {"detail": "compact", "original_bytes": original_bytes,
        "budget_bytes": 49152, "complete": False,
        "recover": "Request detail='full'; review_context(event_id=..., detail='full') reads one exact event"}
    # Nested workspace is separately capped at 24 KiB. Drop low-priority previews
    # only if unusually large remote fields exhaust the remaining envelope.
    for key in ("evidence", "past_events"):
        while len(canonical(result).encode()) > 49000 and result.get(key):
            result[key].pop()
            omitted = result["response_projection"].setdefault("omitted_items", {})
            omitted[key] = omitted.get(key, 0) + 1
    if len(canonical(result).encode()) > 49000 and "brain" in result:
        result["brain"] = {"omitted": "Remote scaffold exceeded compact budget",
                           "recover": "brain_prepare(detail='full')"}
    if len(canonical(result).encode()) > 49000:
        raise BrainError("compact_budget_exceeded", "Workspace projection exceeds the compact contract; request full detail")
    if "past_events" in result:
        result["returned_events"] = len(result["past_events"])
    result["response_projection"]["response_bytes"] = 0
    for _ in range(4):
        size = len(canonical(result).encode())
        if result["response_projection"]["response_bytes"] == size:
            break
        result["response_projection"]["response_bytes"] = size
    return result


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise BrainError("unsafe_outbox", "Outbox must be a regular file")
    if path.stat().st_size > 262144:
        raise BrainError("outbox_too_large", "Outbox exceeds bounded record size")
    return json.loads(path.read_text())


def _save(path, value):
    if path.is_symlink():
        raise BrainError("unsafe_outbox", "Refusing a symlink outbox")
    raw = canonical(value).encode()
    if len(raw) > 262144:
        raise BrainError("outbox_too_large", "Outbox exceeds bounded record size")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class MCPPeer:
    def __init__(self, settings):
        if not settings or not settings.get("command") or not settings.get("scope"):
            raise BrainError("brain_not_configured", "Main Brain command and scope must be configured")
        self.settings = settings

    @asynccontextmanager
    async def session(self):
        params = StdioServerParameters(command=self.settings["command"],
            args=self.settings.get("args", []), cwd=self.settings.get("cwd"))
        with anyio.fail_after(self.settings.get("timeout_seconds", 30)):
            async with stdio_client(params) as streams:
                async with ClientSession(*streams, read_timeout_seconds=25) as session:
                    await session.initialize()
                    yield session

    @staticmethod
    async def call(session, name, arguments):
        result = await session.call_tool(name, arguments)
        if result.is_error:
            text = " ".join(c.text for c in result.content if getattr(c, "type", None) == "text")
            raise BrainError("brain_rejected", text[:2000])
        if getattr(result, "structured_content", None):
            return result.structured_content
        for content in result.content:
            if getattr(content, "type", None) == "text":
                return json.loads(content.text)
        raise BrainError("invalid_brain_response", "No structured Main Brain result")


class BrainBridge:
    def __init__(self, engine, settings, peer=None):
        self.engine = engine
        self.settings = settings or {}
        self.peer = peer or MCPPeer(self.settings)
        self.scope = self.settings["scope"]
        self.producer = self.settings.get("producer_id", "retulo")
        self.outbox = Path(engine.state_home) / "brain-outbox"

    def _path(self, task_id, event_id):
        # Validation of task identity/root happens before any outbox read/write.
        self.engine.get_context(task_id)
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 200:
            raise BrainError("invalid_event_id", "Event ID must be a nonempty bounded string")
        key = hashlib.sha256(canonical([self.scope, self.producer, task_id, event_id]).encode()).hexdigest()
        return self._task_outbox(task_id) / (key + ".json")

    def _task_outbox(self, task_id):
        key = hashlib.sha256(canonical([self.scope, self.producer, task_id]).encode()).hexdigest()
        directory = self.outbox / key
        if self.outbox.is_symlink() or directory.is_symlink():
            raise BrainError("unsafe_outbox", "Outbox directory cannot be a symlink")
        return directory

    @asynccontextmanager
    async def _lock(self, path):
        # MVP: local POSIX filesystem; never silently claim NAS/Windows locking.
        import fcntl
        if self.outbox.is_symlink():
            raise BrainError("unsafe_outbox", "Outbox directory cannot be a symlink")
        self.outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.is_symlink():
            raise BrainError("unsafe_outbox", "Task outbox directory cannot be a symlink")
        path.parent.mkdir(exist_ok=True, mode=0o700)
        lock_path = path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            with anyio.fail_after(35):
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        await anyio.sleep(0.03)
                yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _validate_record(self, record, task_id, event_id):
        payload = record["payload"]
        if (record["task_id"] != task_id or record["event_id"] != event_id or
            payload["scope"] != self.scope or
            payload["metadata"]["workspace_task_id"] != task_id or
            payload["idempotency"]["producer_id"] != self.producer or
            payload["idempotency"]["event_id"] != task_id + ":" + event_id or
            payload_digest(payload) != payload["idempotency"]["payload_digest"]):
            raise BrainError("outbox_modified", "Stored request identity or digest differs")

    async def prepare(self, task_id, prompt="Review prior decisions for this task", detail="compact"):
        _detail(detail)
        context = self.engine.get_context(task_id, detail=detail)
        async with self.peer.session() as session:
            memory = await self.peer.call(session, "prepare", {"prompt": f"{task_id} {prompt}",
                "scope": self.scope, "detail": detail})
        result = {"task_id": task_id, "workspace": context, "brain": memory,
                  "authority": "Current task state and user instructions take precedence"}
        if detail == "full":
            return result
        size = len(canonical(result).encode())
        result["brain"] = _prepare_card(memory)
        return _compact_finish(result, size)

    async def capture(self, task_id, event_id, title, body, source_id, quote,
                      kind="task", metadata=None):
        if kind not in ("task", "user", "knowledge"):
            raise BrainError("invalid_kind", "Unsupported memory kind")
        if not all(isinstance(v, str) and v.strip() for v in (title, body, source_id, quote)):
            raise BrainError("evidence_required", "Actual registered source and exact quote are required")
        if len(body.encode()) > 65536 or len(quote.encode()) > 65536:
            raise BrainError("capture_too_large", "Capture exceeds bounded input size")
        context = self.engine.get_context(task_id, detail="full")
        path = self._path(task_id, event_id)
        request = {"title": title, "body": body, "source_id": source_id,
                   "quote": quote, "kind": kind, "metadata": metadata or {}}
        async with self._lock(path):
            if path.exists():
                record = _read(path)
                self._validate_record(record, task_id, event_id)
                if record["request"] != request:
                    raise BrainError("idempotency_conflict", "Event ID already has another request")
            else:
                info = dict(metadata or {})
                info.update(workspace_task_id=task_id, workspace_revision=context["revision"],
                            workspace_event_id=event_id, workspace_snapshot=_snapshot(context))
                payload = {"kind": kind, "title": f"{title} [{task_id}]", "body": body,
                           "scope": self.scope,
                           "evidence": [{"source_id": source_id, "quote": quote}], "metadata": info}
                payload["idempotency"] = {"producer_id": self.producer,
                    "event_id": task_id + ":" + event_id, "payload_digest": payload_digest(payload)}
                record = {"schema_version": 1, "task_id": task_id, "event_id": event_id,
                          "request": request, "payload": payload, "status": "pending",
                          "created_at": datetime.now(timezone.utc).isoformat()}
                _save(path, record)
            return await self._send(path, record)

    async def sync(self, task_id, event_id):
        path = self._path(task_id, event_id)
        async with self._lock(path):
            if not path.exists():
                raise BrainError("unknown_event", "No saved event to synchronize")
            record = _read(path)
            self._validate_record(record, task_id, event_id)
            return await self._send(path, record)

    async def _send(self, path, record):
        payload = record["payload"]
        identity = {"scope": self.scope, "producer_id": self.producer,
                    "event_id": payload["idempotency"]["event_id"]}
        try:
            async with self.peer.session() as session:
                available = {tool.name for tool in (await session.list_tools()).tools}
                if "capture_receipt" not in available:
                    raise BrainError("receipt_capability_missing", "Safe synchronization requires capture_receipt")
                result = await self.peer.call(session, "capture_receipt", identity)
                if result.get("found"):
                    receipt = result["receipt"]
                    if receipt["payload_digest"] != payload["idempotency"]["payload_digest"]:
                        raise BrainError("idempotency_conflict", "Remote event content differs")
                    replayed = True
                    effects = receipt.get("postprocessing")
                    if effects is not None and effects.get("status") != "complete":
                        # The receiver repairs only unfinished idempotent effects;
                        # this does not change the saved memory revision.
                        repaired = await self.peer.call(session, "capture", payload)
                        receipt = repaired["idempotency_receipt"]
                else:
                    result = await self.peer.call(session, "capture", payload)
                    receipt = result["idempotency_receipt"]
                    replayed = result.get("replayed", False)
                # Stored receipt is a historical acknowledgement, not proof of current source validity.
                record.update(status="synced", receipt=receipt,
                              synced_at=datetime.now(timezone.utc).isoformat())
                record.pop("error", None)
                _save(path, record)
                return {"task_id": record["task_id"], "event_id": record["event_id"],
                        "brain_sync": "synced", "receipt": receipt, "replayed": replayed,
                        "postprocessing_status": receipt.get("postprocessing", {}).get("status", "unknown")}
        except Exception as exc:
            cause = _underlying_error(exc)
            record["status"] = "sync_unknown"
            record["error"] = getattr(cause, "code", type(cause).__name__)
            _save(path, record)
            # Keep outputs safe; never turn a failed sync into task completion/acceptance.
            if cause is not exc:
                raise cause from exc
            raise

    async def receipt(self, task_id, event_id):
        path = self._path(task_id, event_id)
        if not path.exists():
            return {"found": False, "brain_sync": "not_queued"}
        record = _read(path)
        self._validate_record(record, task_id, event_id)
        async with self.peer.session() as session:
            result = await self.peer.call(session, "capture_receipt", {"scope": self.scope,
                "producer_id": self.producer, "event_id": record["payload"]["idempotency"]["event_id"]})
        return {**result, "local_status": record["status"]}

    async def review_context(self, task_id, detail="compact", event_id=None):
        _detail(detail)
        # Establish task identity before touching its outbox. Current filesystem
        # facts are deliberately sampled after the remote evidence read below.
        self.engine.get_context(task_id, detail="compact")
        events = []
        directory = self._task_outbox(task_id)
        total = 0
        if event_id is not None:
            path = self._path(task_id, event_id)
            if not path.exists():
                raise BrainError("unknown_event", "No saved event with this ID")
            item = _read(path)
            self._validate_record(item, task_id, event_id)
            events, total = [item], 1
        elif directory.exists():
            # Only this task's outbox, never other tasks or a user-directory search.
            for path in sorted(directory.glob("*.json")):
                item = _read(path)
                self._validate_record(item, task_id, item["event_id"])
                events.append(item)
                total += 1
                # Retain ten bodies in memory, regardless of ledger length.
                events = sorted(events, key=lambda e: (e.get("created_at", ""), e["event_id"]))[-10:]
        evidence = []
        seen_evidence = set()
        async with self.peer.session() as session:
            for item in events:
                for evidence_id in item.get("receipt", {}).get("evidence_ids", [])[:4]:
                    if evidence_id not in seen_evidence:
                        evidence.append(await self.peer.call(session, "read_evidence", {"evidence_id": evidence_id}))
                        seen_evidence.add(evidence_id)
        full_context = self.engine.get_context(task_id, detail="full")
        context = full_context if detail == "full" else self.engine.get_context(task_id, detail="compact")
        verification = self.engine.verify(task_id)
        current_snapshot = _snapshot(full_context)
        observed_at = datetime.now(timezone.utc).isoformat()
        past = []
        for item in events:
            metadata = item["payload"]["metadata"]
            snapshot = metadata.get("workspace_snapshot")
            captured_revision = metadata.get("workspace_revision")
            same_artifacts = (snapshot["artifacts_digest"] == current_snapshot["artifacts_digest"]
                              if snapshot and "artifacts_digest" in snapshot else None)
            same_revision = captured_revision == full_context["revision"]
            stable_read = context["revision"] == full_context["revision"] == verification.get("revision")
            recheck = not (same_revision and same_artifacts and verification.get("ok") and stable_read)
            row = {"event_id": item["event_id"], "status": item["status"],
                   "kind": item["payload"]["kind"], "recorded_at": item.get("created_at"),
                   "captured_workspace_revision": captured_revision,
                   "current_workspace_revision": full_context["revision"],
                   "registered_artifacts_match": same_artifacts,
                   "workspace_snapshot": snapshot,
                   "applicability": "requires_revalidation" if recheck else "same_workspace_snapshot",
                   "evidence_coverage": "Not inferred from capture-time workspace metadata",
                   "user_decision_revoked": False,
                   "authority": "Historical record; source freshness and present applicability are separate",
                   "body": item["payload"]["body"], "receipt": item.get("receipt")}
            past.append(row)
        result = {"task_id": task_id, "workspace": context, "filesystem_verification": verification,
                  "workspace_observed_at": observed_at,
                  "past_events": past, "event_count": total, "returned_events": len(past),
                  "evidence": evidence,
                  "review_instructions": "Compare confirmed decisions, observed results and unknowns. Failed reviews remain failures. Propose at most three corrections; validate them before promoting a rule. Source freshness does not revalidate a changed artifact. No automatic execution, memory promotion, notification or user-decision revocation."}
        if detail == "full":
            return result
        size = len(canonical(result).encode())
        for row in past:
            row["body_excerpt"] = _excerpt(row.pop("body"))
            receipt = row.pop("receipt") or {}
            row["receipt_summary"] = {"memory_id": receipt.get("memory_id"), "revision": receipt.get("revision"),
                "status": receipt.get("status"), "evidence_ids": receipt.get("evidence_ids", [])[:4],
                "postprocessing_status": receipt.get("postprocessing", {}).get("status", "unknown")}
            row["recover"] = {"event_id": row["event_id"], "detail": "full"}
        result["evidence"] = [_evidence_card(e) for e in evidence]
        result["filesystem_verification"] = {k: verification[k] for k in
            ("schema_version", "task_id", "revision", "ok") if k in verification}
        mismatches = verification.get("mismatches", [])
        result["filesystem_verification"].update(mismatch_count=len(mismatches),
            mismatches=[_excerpt(canonical(m), 400) for m in mismatches[:12]],
            recover="retulo.verify(task_id) or review_context(detail='full')")
        return _compact_finish(result, size)
