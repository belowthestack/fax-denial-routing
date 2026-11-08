"""The intake pipeline. Order of operations is the design:

  1. claim    - hash the bytes, insert into `faxes` under UNIQUE keys.   (idempotency BEFORE any AI call)
  2. extract  - call the backend. Any failure goes to the dead letter queue and raises an alert.
  3. decide   - deterministic rules over the extraction.
  4. persist  - document, rule hits, proposed action, audit rows, all in one transaction.

Nothing leaves the building without a person: outbound actions are created as
`pending_approval` and `dispatch` refuses anything that is not `approved`.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Callable

from . import audit
from .extractors import Extractor, NeedsVision, content_hash
from . import messages
from .rules import Decision, decide
from .schema import DenialReason, DocType, Extraction, FieldValue, Queue
from .store import Store, now_iso

ACTION_KIND: dict[Queue, str] = {
    Queue.MISSING_DOCUMENTATION: "send_records",
    Queue.MEDICAL_NECESSITY: "appeal_or_peer_review",
    Queue.ELIGIBILITY: "verify_eligibility",
    Queue.EXPIRED_AUTH: "request_reauthorization",
    Queue.CODING: "correct_and_resubmit",
}


@dataclass
class IngestResult:
    status: str  # processed | duplicate | dead_letter
    fax_id: str
    doc_id: str | None = None
    queue: str | None = None
    duplicate_of: str | None = None


def _alert_noop(message: str) -> None:  # replaced in production by a webhook/pager call
    return None


class Pipeline:
    def __init__(self, store: Store, extractor: Extractor, alert: Callable[[str], None] = _alert_noop):
        self.store = store
        self.extractor = extractor
        self.alert = alert

    # ------------------------------------------------------------ intake
    def ingest(self, data: bytes, source: str, source_id: str | None = None) -> IngestResult:
        digest = content_hash(data)
        source_key = f"{source}:{source_id}" if source_id else None
        fax_id = f"fax_{uuid.uuid4().hex[:12]}"

        # Step 1: claim. The UNIQUE constraints are the idempotency guarantee.
        try:
            with self.store.tx() as conn:
                conn.execute(
                    "INSERT INTO faxes (fax_id, content_hash, source, source_key, received_at, status, blob) VALUES (?,?,?,?,?,?,?)",
                    (fax_id, digest, source, source_key, now_iso(), "received", data),
                )
                audit.log(self.store, conn, "fax_received", fax_id=fax_id, source=source)
        except sqlite3.IntegrityError:
            existing = self.store.one(
                "SELECT fax_id FROM faxes WHERE content_hash = ? OR (source_key IS NOT NULL AND source_key = ?)", (digest, source_key)
            )
            assert existing is not None
            with self.store.tx() as conn:
                audit.log(self.store, conn, "duplicate_suppressed", fax_id=existing["fax_id"], source=source, duplicate_of=existing["fax_id"])
            doc = self.store.one("SELECT doc_id, queue FROM documents WHERE fax_id = ?", (existing["fax_id"],))
            return IngestResult("duplicate", existing["fax_id"], doc["doc_id"] if doc else None, doc["queue"] if doc else None, existing["fax_id"])

        return self._process(fax_id, data)

    # ----------------------------------------------------------- process
    def _process(self, fax_id: str, data: bytes, retry_of: str | None = None) -> IngestResult:
        try:
            extraction = self.extractor.extract(data)
        except Exception as exc:  # any backend failure: park it, tell a human, never lose the fax
            return self._dead_letter(fax_id, "extract", exc)
        try:
            decision = decide(extraction)
            doc_id = self._persist(fax_id, extraction, decision, retry_of)
        except Exception as exc:  # a bug after extraction must not strand the fax in 'received'
            return self._dead_letter(fax_id, "route", exc)
        return IngestResult("processed", fax_id, doc_id, decision.queue.value)

    def _dead_letter(self, fax_id: str, stage: str, exc: Exception) -> IngestResult:
        error_class = "needs_vision" if isinstance(exc, NeedsVision) else type(exc).__name__[:40]
        with self.store.tx() as conn:
            conn.execute("UPDATE faxes SET status = 'dead_letter' WHERE fax_id = ?", (fax_id,))
            conn.execute(
                "INSERT INTO dead_letter (dl_id, fax_id, stage, error_class, created_at) VALUES (?,?,?,?,?)",
                (f"dl_{uuid.uuid4().hex[:10]}", fax_id, stage, error_class, now_iso()),
            )
            audit.log(self.store, conn, "dead_lettered", fax_id=fax_id, stage=stage, error_class=error_class)
        try:
            self.alert(f"fax {fax_id} dead-lettered at {stage}: {error_class}")
        except Exception:
            pass  # an alert failure must not mask the dead letter row, which is already committed
        return IngestResult("dead_letter", fax_id)

    def _persist(self, fax_id: str, ex: Extraction, d: Decision, retry_of: str | None) -> str:
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"
        confs = [fv.confidence for fv in ex.fields.values() if fv.value is not None]
        with self.store.tx() as conn:
            conn.execute(
                "INSERT INTO documents (doc_id, fax_id, doc_type, denial_reason, queue, tentative_queue, priority, needs_review, review_reasons, extraction, rule_hits, state, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    doc_id, fax_id, d.doc_type.value, d.denial_reason.value if d.denial_reason else None, d.queue.value,
                    d.tentative_queue.value if d.tentative_queue else None, d.priority, int(d.needs_review),
                    json.dumps(d.review_reasons), ex.model_dump_json(), json.dumps([h.__dict__ for h in d.hits]),
                    "open", now_iso(),
                ),
            )
            conn.execute("UPDATE faxes SET status = 'processed', page_count = ? WHERE fax_id = ?", (ex.page_count, fax_id))
            detail = dict(
                backend=ex.backend, doc_type=d.doc_type.value, queue=d.queue.value, priority=d.priority,
                rule_ids=sorted({h.rule_id for h in d.hits}), needs_review=d.needs_review, page_count=ex.page_count,
                min_confidence=round(min(confs), 2) if confs else 0.0,
            )
            if ex.model_id:
                detail["model_id"] = ex.model_id
            if d.tentative_queue:
                detail["tentative_queue"] = d.tentative_queue.value
            if d.needs_review:
                detail["review_reason_codes"] = audit.review_reason_codes(d.review_reasons)
            if retry_of:
                detail["retry_of"] = retry_of
            audit.log(self.store, conn, "document_routed", fax_id=fax_id, doc_id=doc_id, **detail)
            self._propose_action(conn, doc_id, ex, d)
        return doc_id

    def _propose_action(self, conn: sqlite3.Connection, doc_id: str, ex: Extraction, d: Decision) -> None:
        queue = d.tentative_queue
        if queue not in ACTION_KIND or d.needs_review:
            return  # a person must confirm the extraction first; actions are drafted after resolve_review
        draft = messages.next_step(d.doc_type, d.denial_reason, {k: v.value for k, v in ex.fields.items()})
        assert draft, "every denial queue has a next-step template"
        kind = ACTION_KIND[queue]
        action_id = f"act_{uuid.uuid4().hex[:10]}"
        conn.execute(
            "INSERT INTO actions (action_id, doc_id, kind, draft, status, created_at) VALUES (?,?,?,?,?,?)",
            (action_id, doc_id, kind, draft, "pending_approval", now_iso()),
        )
        audit.log(self.store, conn, "action_proposed", doc_id=doc_id, action_id=action_id, action_kind=kind)

    # ------------------------------------------------------- dead letters
    def retry_dead_letter(self, dl_id: str) -> IngestResult:
        dl = self.store.one("SELECT * FROM dead_letter WHERE dl_id = ? AND state = 'open'", (dl_id,))
        if not dl:
            raise KeyError("no open dead letter with that id")
        fax = self.store.one("SELECT blob FROM faxes WHERE fax_id = ?", (dl["fax_id"],))
        assert fax is not None
        result = self._process(dl["fax_id"], fax["blob"], retry_of=dl_id)
        if result.status == "processed":
            with self.store.tx() as conn:
                conn.execute("UPDATE dead_letter SET state = 'resolved' WHERE dl_id = ?", (dl_id,))
        return result

    # ------------------------------------------------------- human review
    def resolve_review(self, doc_id: str, corrections: dict[str, str | int | None], reviewer: str) -> IngestResult:
        """A person confirms or corrects fields. Confirmed values get confidence 1.0, then the same rules re-run."""
        row = self.store.one("SELECT * FROM documents WHERE doc_id = ? AND needs_review = 1 AND state = 'open'", (doc_id,))
        if not row:
            raise KeyError("document is not awaiting review")
        ex = Extraction.model_validate_json(row["extraction"])
        from .schema import coerce, validate_field  # shared coercion, same as every intake path

        changed: list[str] = []
        for name, raw in corrections.items():
            if name not in ex.fields:
                raise ValueError(f"unknown field {name}")
            value, problem = coerce(name, raw)
            if problem or validate_field(name, value):
                raise ValueError(problem or validate_field(name, value))
            ex.fields[name] = FieldValue(value=value, confidence=1.0)
            changed.append(name)
        # Fields the reviewer looked at and left alone are confirmed too.
        for fv in ex.fields.values():
            if fv.value is not None:
                fv.confidence = 1.0
        ex.disagreements = []  # a person looked at the page; the readers' disagreement is settled
        ex.doc_type_hint = None  # a person has looked at it; the model's label no longer counts as a second opinion
        d = decide(ex)
        with self.store.tx() as conn:
            conn.execute(
                "UPDATE documents SET doc_type=?, denial_reason=?, queue=?, tentative_queue=?, priority=?, needs_review=?, review_reasons=?, extraction=?, rule_hits=?, resolved_at=? WHERE doc_id=?",
                (
                    d.doc_type.value, d.denial_reason.value if d.denial_reason else None, d.queue.value,
                    d.tentative_queue.value if d.tentative_queue else None, d.priority, int(d.needs_review),
                    json.dumps(d.review_reasons), ex.model_dump_json(), json.dumps([h.__dict__ for h in d.hits]),
                    now_iso(), doc_id,
                ),
            )
            audit.log(
                self.store, conn, "review_resolved", fax_id=row["fax_id"], doc_id=doc_id, actor=reviewer,
                fields_changed=sorted(changed), queue=d.queue.value, doc_type=d.doc_type.value,
                rule_ids=sorted({h.rule_id for h in d.hits}), needs_review=d.needs_review,
            )
            self._propose_action(conn, doc_id, ex, d)
        return IngestResult("processed", row["fax_id"], doc_id, d.queue.value)

    # ------------------------------------------------------- approvals
    def decide_action(self, action_id: str, approve: bool, approver: str) -> None:
        act = self.store.one("SELECT * FROM actions WHERE action_id = ?", (action_id,))
        if not act or act["status"] != "pending_approval":
            raise KeyError("no pending action with that id")
        if not approver.strip():
            raise ValueError("an approver is required")
        status = "approved" if approve else "rejected"
        with self.store.tx() as conn:
            conn.execute("UPDATE actions SET status=?, approved_by=?, decided_at=? WHERE action_id=?", (status, approver, now_iso(), action_id))
            audit.log(self.store, conn, f"action_{status}", doc_id=act["doc_id"], actor=approver, action_id=action_id, action_kind=act["kind"])

    def dispatch_action(self, action_id: str) -> None:
        """Simulated send. Refuses anything a person has not approved."""
        act = self.store.one("SELECT * FROM actions WHERE action_id = ?", (action_id,))
        if not act or act["status"] != "approved" or not act["approved_by"]:
            raise PermissionError("action is not approved")
        with self.store.tx() as conn:
            conn.execute("INSERT INTO outbox (action_id, channel, created_at) VALUES (?,?,?)", (action_id, "simulated", now_iso()))
            conn.execute("UPDATE actions SET status='dispatched' WHERE action_id=?", (action_id,))
            audit.log(self.store, conn, "action_dispatched", doc_id=act["doc_id"], action_id=action_id, action_kind=act["kind"], outcome="simulated")
