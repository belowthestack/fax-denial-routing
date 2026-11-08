"""Extraction backends. All return the same shared `Extraction`.

bedrock    Claude vision via AWS Bedrock (Converse API, forced tool call). The real path.
replay     Replays payloads captured from real Bedrock calls, keyed by content hash.
           Used by tests so the suite needs no network and no AWS account.
local      Reads the PDF text layer with regexes. Offline and free, but it cannot
           read a scanned page; those raise NeedsVision so they go to the dead letter
           queue instead of being guessed.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol

import fitz  # PyMuPDF

from .schema import (
    CONSENSUS_FIELDS,
    FIELD_NAMES,
    DocType,
    Extraction,
    FieldValue,
    extraction_tool_schema,
    normalize_raw,
)


class NeedsVision(Exception):
    """The document has no readable text layer and the local backend cannot read images."""


class Extractor(Protocol):
    name: str

    def extract(self, data: bytes) -> Extraction: ...


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_pages(data: bytes, dpi: int = 150, max_pages: int = 4) -> list[bytes]:
    doc = fitz.open(stream=data, filetype="pdf")
    return [p.get_pixmap(dpi=dpi).tobytes("png") for p in list(doc)[:max_pages]]


# ------------------------------------------------------------------ bedrock

DEFAULT_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

SYSTEM_PROMPT = (
    "You read faxed insurance payer letters for a healthcare operations team. "
    "Extract the requested fields exactly as printed. If a field is not on the page, or you cannot read it, "
    "return null with low confidence. Never guess a digit. Report confidence honestly: below 0.8 when any character "
    "is smudged, cut off, or ambiguous. Classify the document as auth_approved, full_denial, partial_denial, or "
    "not_payer_response (anything that is not a payer's decision on an authorization request). Do not interpret "
    "clinical content and do not add medical detail to notes."
)


class BedrockVisionExtractor:
    name = "bedrock"

    def __init__(self, model_id: str = DEFAULT_MODEL, region: str = "us-east-1", client=None):
        self.model_id = model_id
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("bedrock-runtime", region_name=region, config=Config(retries={"max_attempts": 3, "mode": "standard"}, read_timeout=60))
        self.client = client

    def call(self, data: bytes) -> tuple[dict, dict, int]:
        """One Converse call. Returns (tool_input, token_usage, page_count)."""
        pages = render_pages(data)
        content = [{"image": {"format": "png", "source": {"bytes": p}}} for p in pages]
        content.append({"text": "Extract the fields from this fax by calling the record_extraction tool."})
        resp = self.client.converse(
            modelId=self.model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": content}],
            inferenceConfig={"maxTokens": 2000, "temperature": 0},
            toolConfig={
                "tools": [{"toolSpec": {"name": "record_extraction", "description": "Record the extracted fields.", "inputSchema": {"json": extraction_tool_schema()}}}],
                "toolChoice": {"tool": {"name": "record_extraction"}},
            },
        )
        for block in resp["output"]["message"]["content"]:
            if "toolUse" in block:
                return block["toolUse"]["input"], resp.get("usage", {}), len(pages)
        raise RuntimeError("model returned no tool call")

    def extract(self, data: bytes) -> Extraction:
        tool_input, _usage, pages = self.call(data)
        return from_tool_input(tool_input, backend=self.name, model_id=self.model_id, page_count=pages)


def from_tool_input(tool_input: dict, *, backend: str, model_id: str | None, page_count: int = 1) -> Extraction:
    hint = tool_input.get("doc_type")
    try:
        hint_enum = DocType(hint) if hint else None
    except ValueError:
        hint_enum = None
    return Extraction(
        fields=normalize_raw(tool_input.get("fields") or {}),
        doc_type_hint=hint_enum,
        backend=backend,
        model_id=model_id,
        page_count=page_count,
        notes=(tool_input.get("notes") or None),
    )


# ------------------------------------------------------------------- replay

class ReplayExtractor:
    """Returns payloads captured from real Bedrock calls (fixtures/bedrock_captured)."""

    name = "replay"

    def __init__(self, directory: str | Path):
        self.by_hash: dict[str, dict] = {}
        self.name = "replay"
        for p in Path(directory).glob("*.json"):
            payload = json.loads(p.read_text(encoding="utf-8"))
            self.by_hash[payload["content_sha256"]] = payload

    def extract(self, data: bytes) -> Extraction:
        payload = self.by_hash.get(content_hash(data))
        if payload is None:
            raise KeyError("no captured payload for this document")
        return from_tool_input(payload["tool_input"], backend="bedrock_replay", model_id=payload.get("model_id"), page_count=payload.get("page_count", 1))


# -------------------------------------------------------------------- local

def _grab(text: str, *labels: str) -> str | None:
    for label in labels:
        m = re.search(rf"^\s*{label}\s*[:\-]\s*(.+?)\s*$", text, re.I | re.M)
        if m:
            return m.group(1).strip()
    return None


class LocalTextExtractor:
    name = "local"

    def extract(self, data: bytes) -> Extraction:
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
        if len(text.strip()) < 40:
            raise NeedsVision("no text layer; a vision model is required")
        raw = {
            "payer_name": _grab(text, "Payer", "Health Plan", "Plan"),
            "member_id": _grab(text, "Member ID", "Subscriber ID", "Member #"),
            "auth_number": _grab(text, "Authorization (?:Number|No\\.?|#)", "Auth (?:Number|No\\.?|#)", "Reference (?:Number|No\\.?|#)"),
            "service_code": _grab(text, "Service Code", "CPT/HCPCS", "Procedure Code"),
            "requested_units": _grab(text, "Units Requested", "Requested Units"),
            "approved_units": _grab(text, "Units Approved", "Approved Units"),
            "requested_start": _grab(text, "Requested Start", "Requested From"),
            "requested_end": _grab(text, "Requested End", "Requested Through"),
            "approved_start": _grab(text, "Approved Start", "Approved From"),
            "approved_end": _grab(text, "Approved End", "Approved Through"),
            "decision_text": _grab(text, "Decision", "Determination"),
            "denial_reason_text": _grab(text, "Reason", "Reason for Denial", "Basis for Determination"),
            "letter_date": _grab(text, "Letter Date", "Date of Letter", "Date"),
            "appeal_deadline": _grab(text, "Appeal Deadline", "Appeal By"),
        }
        fields = normalize_raw({k: {"value": v, "confidence": 0.9} for k, v in raw.items()})
        assert set(fields) == set(FIELD_NAMES)
        hint = None if (raw["decision_text"] or raw["member_id"] or raw["auth_number"]) else DocType.NOT_PAYER_RESPONSE
        return Extraction(fields=fields, doc_type_hint=hint, backend=self.name, model_id=None, page_count=len(doc))


# ---------------------------------------------------------------- consensus

class ConsensusExtractor:
    """Two independent readers. The primary's values are used; any disagreement on a
    high-stakes field zeroes that field's confidence and is recorded, so the rules
    layer sends the document to a person. One model's self-reported confidence is
    not trusted on its own: in our own test run a single reader dropped a digit
    from a member ID while reporting 0.99.
    """

    def __init__(self, primary: Extractor, secondary: Extractor):
        self.primary, self.secondary = primary, secondary
        self.name = f"{primary.name}+{secondary.name}"

    def extract(self, data: bytes) -> Extraction:
        a = self.primary.extract(data)
        b = self.secondary.extract(data)  # if the second reader fails, the whole fax dead-letters
        disagree = [f for f in CONSENSUS_FIELDS if a.get(f) != b.get(f)]
        for f in disagree:
            a.fields[f] = FieldValue(value=a.get(f), confidence=0.0)
        a.disagreements = disagree
        a.backend = self.name
        a.model_id = f"{a.model_id}|{b.model_id}"
        return a
