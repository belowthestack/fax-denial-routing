from __future__ import annotations

import json
import sqlite3

import pytest

from faxrouting import audit
from faxrouting.pipeline import Pipeline
from faxrouting.store import Store


def _all_values(extraction_json: str) -> set[str]:
    fields = json.loads(extraction_json)["fields"]
    return {str(v["value"]) for v in fields.values() if v["value"] is not None}


def test_audit_log_never_contains_extracted_values(pipeline, letters):
    """Run everything, including review, approval and dispatch, then scan the audit
    table for any value that was read from any document."""
    for name, (data, _) in letters.items():
        r = pipeline.ingest(data, "folder", name)
        if pipeline.store.one("SELECT needs_review n FROM documents WHERE doc_id=?", (r.doc_id,))["n"]:
            exp = letters[name][1]["fields"]
            pipeline.resolve_review(r.doc_id, {k: v for k, v in exp.items() if v is not None and k not in ("payer_name", "denial_reason_text")}, reviewer="reviewer.test")
    for a in pipeline.store.rows("SELECT action_id FROM actions"):
        pipeline.decide_action(a["action_id"], True, "approver.test")
        pipeline.dispatch_action(a["action_id"])

    audit_text = "\n".join(f"{r['event']} {r['actor']} {r['detail']}" for r in pipeline.store.rows("SELECT * FROM audit_log"))
    assert audit_text
    leaked = []

    def sensitive(v: str) -> bool:
        # identifiers, codes, dates, payer names and reason text; not generic words like "Approved"
        return (len(v) >= 4 and any(c.isdigit() for c in v)) or len(v) >= 12

    for doc in pipeline.store.rows("SELECT extraction FROM documents"):
        for value in _all_values(doc["extraction"]):
            if sensitive(value) and value.lower() in audit_text.lower():
                leaked.append(value)
    for _, (_, expected) in letters.items():
        for v in expected["fields"].values():
            if isinstance(v, str) and sensitive(v) and v.lower() in audit_text.lower():
                leaked.append(v)
    assert not leaked, f"audit log leaked document values: {leaked}"


def test_audit_rejects_unlisted_keys_and_free_text(pipeline):
    with pipeline.store.tx() as conn:
        with pytest.raises(ValueError):
            audit.log(pipeline.store, conn, "x", member_id="PMH12345678")
        with pytest.raises(ValueError):
            audit.log(pipeline.store, conn, "x", stage="Member PMH12345678 was denied for medical necessity")


def test_audit_log_is_append_only(pipeline, letters):
    pipeline.ingest(letters["01_approved_aba_assessment"][0], "folder", "a")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with pipeline.store.tx() as conn:
            conn.execute("UPDATE audit_log SET event = 'tampered'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with pipeline.store.tx() as conn:
            conn.execute("DELETE FROM audit_log")


def test_actions_need_a_person_before_dispatch(pipeline, letters):
    r = pipeline.ingest(letters["03_denial_medical_necessity"][0], "folder", "a")
    act = pipeline.store.one("SELECT * FROM actions WHERE doc_id = ?", (r.doc_id,))
    assert act["status"] == "pending_approval"
    with pytest.raises(PermissionError):
        pipeline.dispatch_action(act["action_id"])
    with pytest.raises(ValueError):
        pipeline.decide_action(act["action_id"], True, "  ")
    pipeline.decide_action(act["action_id"], True, "approver.test")
    pipeline.dispatch_action(act["action_id"])
    assert pipeline.store.one("SELECT status FROM actions WHERE action_id = ?", (act["action_id"],))["status"] == "dispatched"
    assert pipeline.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 1


def test_rejected_action_cannot_be_dispatched(pipeline, letters):
    r = pipeline.ingest(letters["03_denial_medical_necessity"][0], "folder", "a")
    act = pipeline.store.one("SELECT * FROM actions WHERE doc_id = ?", (r.doc_id,))
    pipeline.decide_action(act["action_id"], False, "approver.test")
    with pytest.raises(PermissionError):
        pipeline.dispatch_action(act["action_id"])


def test_no_action_is_drafted_until_a_person_confirms_a_review_item(pipeline, letters):
    r = pipeline.ingest(letters["05_denial_eligibility"][0], "folder", "a")
    assert pipeline.store.one("SELECT COUNT(*) n FROM actions WHERE doc_id = ?", (r.doc_id,))["n"] == 0
    pipeline.resolve_review(r.doc_id, {"member_id": "PMH61130742"}, reviewer="reviewer.test")
    assert pipeline.store.one("SELECT COUNT(*) n FROM actions WHERE doc_id = ?", (r.doc_id,))["n"] == 1


def test_review_rejects_values_that_fail_the_shared_validation(pipeline, letters):
    r = pipeline.ingest(letters["05_denial_eligibility"][0], "folder", "a")
    with pytest.raises(ValueError):
        pipeline.resolve_review(r.doc_id, {"member_id": "!!"}, reviewer="reviewer.test")
    with pytest.raises(ValueError):
        pipeline.resolve_review(r.doc_id, {"approved_units": "lots"}, reviewer="reviewer.test")
