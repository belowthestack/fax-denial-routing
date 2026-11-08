"""Replay the 10 synthetic payer letters through the full pipeline using payloads
captured from real Bedrock calls (fixtures/bedrock_captured). No network needed."""
from __future__ import annotations

import json

import pytest

from conftest import EXPECT_REVIEW, load_letters

NAMES = sorted(load_letters())


@pytest.mark.parametrize("name", NAMES)
def test_classification_and_routing(pipeline, letters, name):
    data, expected = letters[name]
    r = pipeline.ingest(data, "folder", name)
    assert r.status == "processed"
    doc = pipeline.store.one("SELECT * FROM documents WHERE doc_id = ?", (r.doc_id,))
    assert doc["doc_type"] == expected["doc_type"]
    assert doc["denial_reason"] == expected["reason"]
    assert doc["priority"] == expected["priority"]
    if name in EXPECT_REVIEW:
        assert doc["queue"] == "human_review" and doc["needs_review"] == 1
        assert json.loads(doc["review_reasons"]), "review items must say why"
    else:
        assert doc["queue"] == expected["queue"] and doc["needs_review"] == 0


@pytest.mark.parametrize("name", [n for n in NAMES if n not in EXPECT_REVIEW and not n.startswith("10_")])
def test_unreviewed_documents_have_exactly_the_right_fields(pipeline, letters, name):
    """If a document skipped human review, every field must match ground truth.
    A wrong value that sails through is the failure this whole design exists to prevent."""
    data, expected = letters[name]
    r = pipeline.ingest(data, "folder", name)
    fields = json.loads(pipeline.store.one("SELECT extraction FROM documents WHERE doc_id = ?", (r.doc_id,))["extraction"])["fields"]
    for key, truth in expected["fields"].items():
        got = fields[key]["value"]
        if key == "denial_reason_text" and truth:
            assert got and got.startswith(truth[:40]), key
        else:
            assert got == truth, f"{key}: {got!r} != {truth!r}"


@pytest.mark.parametrize("name", sorted(EXPECT_REVIEW))
def test_review_then_correction_reaches_expected_queue(pipeline, letters, name):
    data, expected = letters[name]
    r = pipeline.ingest(data, "folder", name)
    truth = {k: v for k, v in expected["fields"].items() if v is not None and k != "denial_reason_text" and k != "payer_name"}
    out = pipeline.resolve_review(r.doc_id, truth, reviewer="reviewer.test")
    assert out.queue == expected["queue"]
    doc = pipeline.store.one("SELECT * FROM documents WHERE doc_id = ?", (r.doc_id,))
    assert doc["needs_review"] == 0 and doc["doc_type"] == expected["doc_type"]


def test_every_routing_decision_cites_rules(pipeline, letters):
    for name, (data, _) in letters.items():
        r = pipeline.ingest(data, "folder", name)
        hits = json.loads(pipeline.store.one("SELECT rule_hits FROM documents WHERE doc_id = ?", (r.doc_id,))["rule_hits"])
        ids = {h["rule_id"] for h in hits}
        assert any(i.startswith("Q-") for i in ids), f"{name} has no queue rule"
        assert any(i.startswith("C-") for i in ids), f"{name} has no classification rule"


def test_expected_queue_distribution(pipeline, letters):
    for name, (data, _) in letters.items():
        pipeline.ingest(data, "folder", name)
    counts = {r["queue"]: r["n"] for r in pipeline.store.rows("SELECT queue, COUNT(*) n FROM documents GROUP BY queue")}
    assert counts == {
        "approved_filed": 1, "human_review": 3, "medical_necessity": 2, "missing_documentation": 2,
        "expired_auth": 1, "not_payer_response": 1,
    }
