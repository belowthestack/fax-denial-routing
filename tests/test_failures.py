"""What happens when the model is down, the document is unreadable, or the alert itself fails."""
from __future__ import annotations

from faxrouting.extractors import LocalTextExtractor
from faxrouting.pipeline import Pipeline
from faxrouting.store import Store


class Down:
    name = "down"

    def extract(self, data):
        raise ConnectionError("simulated Bedrock outage")


class Flaky:
    """Fails once, then delegates."""

    name = "flaky"

    def __init__(self, inner):
        self.inner, self.failed = inner, False

    def extract(self, data):
        if not self.failed:
            self.failed = True
            raise TimeoutError("simulated timeout")
        return self.inner.extract(data)


def test_outage_dead_letters_and_alerts_and_keeps_the_fax(letters):
    alerts: list[str] = []
    p = Pipeline(Store(), Down(), alert=alerts.append)
    r = p.ingest(letters["03_denial_medical_necessity"][0], "folder", "a")
    assert r.status == "dead_letter"
    assert p.store.one("SELECT status FROM faxes WHERE fax_id = ?", (r.fax_id,))["status"] == "dead_letter"
    assert p.store.one("SELECT COUNT(*) n FROM documents")["n"] == 0
    dl = p.store.one("SELECT * FROM dead_letter")
    assert dl["error_class"] == "ConnectionError" and dl["stage"] == "extract"
    assert len(alerts) == 1 and r.fax_id in alerts[0]
    assert "dead_lettered" in [x["event"] for x in p.store.rows("SELECT event FROM audit_log")]


def test_alert_failure_does_not_lose_the_dead_letter(letters):
    def broken_alert(_):
        raise RuntimeError("pager is down too")

    p = Pipeline(Store(), Down(), alert=broken_alert)
    r = p.ingest(letters["03_denial_medical_necessity"][0], "folder", "a")
    assert r.status == "dead_letter" and p.store.one("SELECT COUNT(*) n FROM dead_letter")["n"] == 1


def test_retry_after_recovery_processes_the_same_fax_without_duplicating(extractor, letters):
    p = Pipeline(Store(), Flaky(extractor), alert=lambda m: None)
    data = letters["08_partial_units_medical_necessity"][0]
    first = p.ingest(data, "folder", "a")
    assert first.status == "dead_letter"
    dl = p.store.one("SELECT dl_id FROM dead_letter")
    retried = p.retry_dead_letter(dl["dl_id"])
    assert retried.status == "processed" and retried.queue == "medical_necessity"
    assert p.store.one("SELECT COUNT(*) n FROM faxes")["n"] == 1
    assert p.store.one("SELECT state FROM dead_letter")["state"] == "resolved"
    # and the same fax arriving again is still just a duplicate
    assert p.ingest(data, "folder", "a").status == "duplicate"


def test_scanned_page_with_no_text_layer_dead_letters_on_the_local_backend(letters):
    p = Pipeline(Store(), LocalTextExtractor(), alert=lambda m: None)
    r = p.ingest(letters["07_denial_coding_scanned"][0], "folder", "scan")
    assert r.status == "dead_letter"
    assert p.store.one("SELECT error_class FROM dead_letter")["error_class"] == "needs_vision"


def test_persist_bug_is_dead_lettered_not_stranded(extractor, letters, monkeypatch):
    p = Pipeline(Store(), extractor, alert=lambda m: None)

    def boom(*a, **k):
        raise RuntimeError("bug in routing")

    monkeypatch.setattr("faxrouting.pipeline.decide", boom)
    r = p.ingest(letters["01_approved_aba_assessment"][0], "folder", "a")
    assert r.status == "dead_letter"
    assert p.store.one("SELECT stage FROM dead_letter")["stage"] == "route"
