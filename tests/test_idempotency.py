from __future__ import annotations

import threading

from faxrouting.extractors import ConsensusExtractor
from faxrouting.pipeline import Pipeline
from faxrouting.store import Store


class CountingExtractor:
    name = "counting"

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def extract(self, data):
        self.calls += 1
        return self.inner.extract(data)


def test_same_fax_three_times_is_one_record(pipeline, letters):
    data, _ = letters["03_denial_medical_necessity"]
    results = [pipeline.ingest(data, "folder", "a.pdf") for _ in range(3)]
    assert [r.status for r in results] == ["processed", "duplicate", "duplicate"]
    assert len({r.doc_id for r in results}) == 1
    assert pipeline.store.one("SELECT COUNT(*) n FROM faxes")["n"] == 1
    assert pipeline.store.one("SELECT COUNT(*) n FROM documents")["n"] == 1
    assert pipeline.store.one("SELECT COUNT(*) n FROM actions")["n"] == 1


def test_duplicate_never_reaches_the_model(extractor, letters):
    counting = CountingExtractor(extractor)
    p = Pipeline(Store(), counting)
    data, _ = letters["04_denial_missing_documentation"]
    for _ in range(3):
        p.ingest(data, "folder", "a.pdf")
    assert counting.calls == 1, "idempotency must be checked BEFORE the AI call"


def test_same_content_from_a_different_source_id_is_still_a_duplicate(pipeline, letters):
    data, _ = letters["06_denial_expired_auth"]
    pipeline.ingest(data, "upload", "first-name.pdf")
    r = pipeline.ingest(data, "faxage", "FAX-OTHER-ID")
    assert r.status == "duplicate"
    assert pipeline.store.one("SELECT COUNT(*) n FROM documents")["n"] == 1


def test_duplicates_are_audited(pipeline, letters):
    data, _ = letters["01_approved_aba_assessment"]
    pipeline.ingest(data, "folder", "x.pdf")
    pipeline.ingest(data, "folder", "x.pdf")
    events = [r["event"] for r in pipeline.store.rows("SELECT event FROM audit_log ORDER BY seq")]
    assert events.count("duplicate_suppressed") == 1 and events.count("document_routed") == 1


def test_concurrent_arrivals_create_one_record(extractor, letters, tmp_path):
    counting = CountingExtractor(extractor)
    p = Pipeline(Store(tmp_path / "t.db"), counting)
    data, _ = letters["05_denial_eligibility"]
    results = []

    def go():
        results.append(p.ingest(data, "folder", "same.pdf").status)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("processed") == 1 and results.count("duplicate") == 7
    assert p.store.one("SELECT COUNT(*) n FROM documents")["n"] == 1
    assert counting.calls == 1
