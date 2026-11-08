"""FastAPI service. Thin: every decision lives in rules.py and pipeline.py.

Run with the recorded Bedrock payloads (no AWS needed):
    FAX_BACKEND=replay uvicorn faxrouting.api:app
Run against real Bedrock (needs AWS credentials with model access):
    FAX_BACKEND=bedrock uvicorn faxrouting.api:app
"""
from __future__ import annotations

import json
import os
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import config
from .pipeline import Pipeline
from .rules import RULE_CATALOG
from .store import Store


class ReviewBody(BaseModel):
    corrections: dict[str, str | int | None] = {}
    reviewer: str


class DecideBody(BaseModel):
    approve: bool
    approver: str


def create_app(store: Store | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    store = store or (pipeline.store if pipeline else Store(os.environ.get("FAX_DB", ":memory:")))
    pipeline = pipeline or Pipeline(store, config.build_extractor(), alert=config.build_alert())
    app = FastAPI(title="Fax denial routing", version="1.0")

    token = os.environ.get("API_TOKEN")

    def auth(authorization: str | None = Header(default=None)) -> None:
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "missing or wrong bearer token")

    def doc_view(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["review_reasons"] = json.loads(row["review_reasons"])
        row["extraction"] = json.loads(row["extraction"])
        row["rule_hits"] = json.loads(row["rule_hits"])
        return row

    def count(sql: str) -> int:
        return store.one(sql)["n"]  # type: ignore[index]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "backend": pipeline.extractor.name}

    @app.post("/api/intake", dependencies=[Depends(auth)])
    async def intake(file: UploadFile = File(...), source_id: str | None = Form(default=None)) -> dict[str, Any]:
        data = await file.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(415, "only PDF faxes are accepted")
        if len(data) > 15_000_000:
            raise HTTPException(413, "file too large")
        return pipeline.ingest(data, "upload", source_id or file.filename).__dict__

    @app.post("/api/intake/raw", dependencies=[Depends(auth)])
    async def intake_raw(request: Request, source: str = "workflow", source_id: str | None = None) -> dict[str, Any]:
        """Same as /api/intake but takes the PDF as the raw request body (what n8n's HTTP node sends)."""
        data = await request.body()
        if not data.startswith(b"%PDF"):
            raise HTTPException(415, "only PDF faxes are accepted")
        if len(data) > 15_000_000:
            raise HTTPException(413, "file too large")
        return pipeline.ingest(data, source[:24], source_id).__dict__

    @app.get("/api/summary", dependencies=[Depends(auth)])
    def summary() -> dict[str, Any]:
        by_queue = store.rows("SELECT queue, COUNT(*) n FROM documents WHERE state='open' GROUP BY queue")
        return {
            "queues": {r["queue"]: r["n"] for r in by_queue},
            "faxes": count("SELECT COUNT(*) n FROM faxes"),
            "dead_letters_open": count("SELECT COUNT(*) n FROM dead_letter WHERE state='open'"),
            "actions_pending": count("SELECT COUNT(*) n FROM actions WHERE status='pending_approval'"),
        }

    @app.get("/api/documents", dependencies=[Depends(auth)])
    def documents(queue: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows("SELECT * FROM documents WHERE (? IS NULL OR queue = ?) ORDER BY created_at, doc_id", (queue, queue))
        return [doc_view(r) for r in rows]

    @app.get("/api/documents/{doc_id}", dependencies=[Depends(auth)])
    def document(doc_id: str) -> dict[str, Any]:
        row = store.one("SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
        if not row:
            raise HTTPException(404, "not found")
        return doc_view(row)

    @app.get("/api/documents/{doc_id}/file", dependencies=[Depends(auth)])
    def document_file(doc_id: str) -> Response:
        row = store.one("SELECT f.blob FROM documents d JOIN faxes f ON f.fax_id = d.fax_id WHERE d.doc_id = ?", (doc_id,))
        if not row:
            raise HTTPException(404, "not found")
        return Response(row["blob"], media_type="application/pdf", headers={"Content-Disposition": "inline"})

    @app.post("/api/documents/{doc_id}/review", dependencies=[Depends(auth)])
    def review(doc_id: str, body: ReviewBody) -> dict[str, Any]:
        try:
            return pipeline.resolve_review(doc_id, body.corrections, body.reviewer).__dict__
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/actions", dependencies=[Depends(auth)])
    def actions(status: str | None = None) -> list[dict[str, Any]]:
        return store.rows("SELECT * FROM actions WHERE (? IS NULL OR status = ?) ORDER BY created_at", (status, status))

    @app.post("/api/actions/{action_id}/decide", dependencies=[Depends(auth)])
    def decide_action(action_id: str, body: DecideBody) -> dict[str, str]:
        try:
            pipeline.decide_action(action_id, body.approve, body.approver)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"status": "approved" if body.approve else "rejected"}

    @app.post("/api/actions/{action_id}/dispatch", dependencies=[Depends(auth)])
    def dispatch(action_id: str) -> dict[str, str]:
        try:
            pipeline.dispatch_action(action_id)
        except PermissionError as e:
            raise HTTPException(409, str(e))
        return {"status": "dispatched"}

    @app.get("/api/dead-letters", dependencies=[Depends(auth)])
    def dead_letters() -> list[dict[str, Any]]:
        return store.rows("SELECT * FROM dead_letter ORDER BY created_at")

    @app.post("/api/dead-letters/{dl_id}/retry", dependencies=[Depends(auth)])
    def retry(dl_id: str) -> dict[str, Any]:
        try:
            return pipeline.retry_dead_letter(dl_id).__dict__
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/audit", dependencies=[Depends(auth)])
    def audit_trail(limit: int = 200) -> list[dict[str, Any]]:
        rows = store.rows("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (min(limit, 1000),))
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return rows

    @app.get("/api/rules")
    def rules() -> dict[str, str]:
        return RULE_CATALOG

    return app


def app_factory() -> FastAPI:
    """For `uvicorn faxrouting.api:app_factory --factory`."""
    return create_app()
