from __future__ import annotations

from fastapi.testclient import TestClient

from faxrouting.api import create_app
from faxrouting.extractors import LocalTextExtractor
from faxrouting.pipeline import Pipeline
from faxrouting.sources import FolderSource, parse_listfax, poll
from faxrouting.store import Store

from conftest import LETTERS


def test_api_end_to_end(pipeline, letters):
    client = TestClient(create_app(pipeline=pipeline))
    assert client.get("/healthz").json()["status"] == "ok"
    data = letters["09_partial_dates_missing_documentation"][0]
    r = client.post("/api/intake", files={"file": ("x.pdf", data, "application/pdf")}).json()
    assert r["status"] == "processed" and r["queue"] == "missing_documentation"
    assert client.post("/api/intake", files={"file": ("x.pdf", data, "application/pdf")}).json()["status"] == "duplicate"
    assert client.get("/api/summary").json()["queues"] == {"missing_documentation": 1}
    doc = client.get(f"/api/documents/{r['doc_id']}").json()
    assert doc["priority"] == "high" and any(h["rule_id"] == "C-PART-02" for h in doc["rule_hits"])
    assert client.get(f"/api/documents/{r['doc_id']}/file").content.startswith(b"%PDF")
    act = client.get("/api/actions", params={"status": "pending_approval"}).json()[0]
    assert client.post(f"/api/actions/{act['action_id']}/dispatch").status_code == 409
    assert client.post(f"/api/actions/{act['action_id']}/decide", json={"approve": True, "approver": "approver.test"}).status_code == 200
    assert client.post(f"/api/actions/{act['action_id']}/dispatch").status_code == 200
    assert client.get("/api/audit").json()[0]["event"] == "action_dispatched"


def test_api_rejects_non_pdf_and_enforces_token(pipeline, monkeypatch):
    client = TestClient(create_app(pipeline=pipeline))
    assert client.post("/api/intake", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 415
    monkeypatch.setenv("API_TOKEN", "test-token")
    secured = TestClient(create_app(pipeline=pipeline))
    assert secured.get("/api/summary").status_code == 401
    assert secured.get("/api/summary", headers={"Authorization": "Bearer test-token"}).status_code == 200


def test_api_review_flow(pipeline, letters):
    client = TestClient(create_app(pipeline=pipeline))
    r = client.post("/api/intake", files={"file": ("x.pdf", letters["05_denial_eligibility"][0], "application/pdf")}).json()
    assert r["queue"] == "human_review"
    assert client.post(f"/api/documents/{r['doc_id']}/review", json={"corrections": {"member_id": "bad!"}, "reviewer": "r"}).status_code == 422
    ok = client.post(f"/api/documents/{r['doc_id']}/review", json={"corrections": {"member_id": "PMH61130742"}, "reviewer": "reviewer.test"}).json()
    assert ok["queue"] == "eligibility"


def test_folder_polling_twice_creates_no_duplicates(extractor):
    p = Pipeline(Store(), extractor, alert=lambda m: None)
    src = FolderSource(LETTERS)
    first = poll(src, p)
    second = poll(src, p)
    assert [r.status for r in first].count("processed") == 10
    assert [r.status for r in second] == ["duplicate"] * 10
    assert p.store.one("SELECT COUNT(*) n FROM documents")["n"] == 10


def test_local_backend_agrees_with_ground_truth_on_text_layer_letters(letters):
    """The offline backend is a cross-check for clean digital faxes; scanned ones dead-letter."""
    p = Pipeline(Store(), LocalTextExtractor(), alert=lambda m: None)
    for name, (data, expected) in letters.items():
        r = p.ingest(data, "folder", name)
        if expected["scanned"]:
            assert r.status == "dead_letter"
        else:
            assert r.status == "processed" and r.queue == expected["queue"], name


def test_faxage_listfax_parser():
    body = "F1001\t2026-11-03 09:14:00\t5550100122\t5550100142\nF1002\t2026-11-03 09:20:11\t5550100123\t5550100142\n"
    rows = parse_listfax(body)
    assert [r["faxid"] for r in rows] == ["F1001", "F1002"] and rows[0]["destination"] == "5550100142"
    assert parse_listfax("") == [] and parse_listfax("ERR02: Login incorrect") == []


def test_raw_body_intake_for_workflow_engines(pipeline, letters):
    client = TestClient(create_app(pipeline=pipeline))
    data = letters["03_denial_medical_necessity"][0]
    h = {"Content-Type": "application/pdf"}
    first = client.post("/api/intake/raw", params={"source": "faxprovider", "source_id": "F1001"}, content=data, headers=h).json()
    again = client.post("/api/intake/raw", params={"source": "faxprovider", "source_id": "F1001"}, content=data, headers=h).json()
    assert first["status"] == "processed" and again["status"] == "duplicate"
    assert client.post("/api/intake/raw", content=b"nope", headers=h).status_code == 415


def test_exported_n8n_workflows_are_sanitized():
    import json
    import re
    from pathlib import Path

    for p in sorted((Path(__file__).resolve().parents[1] / "n8n").glob("*.json")):
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        assert data["name"].startswith("[lab-ops-command-center] WF-")
        assert all("credentials" not in n for n in data["nodes"]), p.name
        assert not re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", text), "IP address in export"
        assert "/webhook/" not in text, "real webhook URL path in export"
        for url in re.findall(r'https?://[^"\s]+', text):
            assert "YOUR-" in url, f"non-placeholder URL in {p.name}: {url}"
