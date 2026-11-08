"""The one shared schema.

Every intake path (folder drop, HTTP upload, fax-provider webhook, human
correction in the review queue) goes through the definitions in this file:
the field list, the enums, the type coercion and the validation. Nothing else
in the codebase hard-codes a field name, an enum value or a validation rule.
The Bedrock tool schema, the dashboard export and the tests are all derived
from FIELD_SPECS below.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DocType(StrEnum):
    AUTH_APPROVED = "auth_approved"
    FULL_DENIAL = "full_denial"
    PARTIAL_DENIAL = "partial_denial"
    NOT_PAYER_RESPONSE = "not_payer_response"


class DenialReason(StrEnum):
    MISSING_DOCUMENTATION = "missing_documentation"
    MEDICAL_NECESSITY = "medical_necessity"
    ELIGIBILITY = "eligibility"
    EXPIRED_AUTH = "expired_auth"
    CODING = "coding"


class Queue(StrEnum):
    APPROVED_FILED = "approved_filed"
    MISSING_DOCUMENTATION = "missing_documentation"
    MEDICAL_NECESSITY = "medical_necessity"
    ELIGIBILITY = "eligibility"
    EXPIRED_AUTH = "expired_auth"
    CODING = "coding"
    HUMAN_REVIEW = "human_review"
    NOT_PAYER = "not_payer_response"


REASON_TO_QUEUE: dict[DenialReason, Queue] = {
    DenialReason.MISSING_DOCUMENTATION: Queue.MISSING_DOCUMENTATION,
    DenialReason.MEDICAL_NECESSITY: Queue.MEDICAL_NECESSITY,
    DenialReason.ELIGIBILITY: Queue.ELIGIBILITY,
    DenialReason.EXPIRED_AUTH: Queue.EXPIRED_AUTH,
    DenialReason.CODING: Queue.CODING,
}

# Fields at or above this confidence are trusted without a human look.
CONFIDENCE_FLOOR = 0.80

# Fields where a single wrong digit changes the outcome. Two readers must agree on all of them.
CONSENSUS_FIELDS = ("member_id", "auth_number", "service_code", "requested_units", "approved_units",
                    "requested_start", "requested_end", "approved_start", "approved_end", "letter_date", "appeal_deadline")


@dataclass(frozen=True)
class FieldSpec:
    kind: str  # text | id | code | int | date
    description: str
    # Which document types need this field to be present and confident.
    required_for: tuple[DocType, ...] = ()


_PAYER_RESPONSES = (DocType.AUTH_APPROVED, DocType.FULL_DENIAL, DocType.PARTIAL_DENIAL)

FIELD_SPECS: dict[str, FieldSpec] = {
    "payer_name": FieldSpec("text", "Name of the insurance plan or payer that sent the letter.", _PAYER_RESPONSES),
    "member_id": FieldSpec("id", "Member or subscriber ID as printed on the letter.", _PAYER_RESPONSES),
    "auth_number": FieldSpec(
        "id",
        "Authorization number, or the payer's reference number if no authorization number is printed.",
        (DocType.AUTH_APPROVED,),
    ),
    "service_code": FieldSpec("code", "Service code (CPT or HCPCS), for example 97153."),
    "requested_units": FieldSpec("int", "Units the provider requested.", (DocType.PARTIAL_DENIAL, DocType.FULL_DENIAL)),
    "approved_units": FieldSpec("int", "Units the payer approved. Use 0 when nothing was approved.", (DocType.AUTH_APPROVED, DocType.PARTIAL_DENIAL)),
    "requested_start": FieldSpec("date", "Start of the requested service period."),
    "requested_end": FieldSpec("date", "End of the requested service period."),
    "approved_start": FieldSpec("date", "Start of the approved service period."),
    "approved_end": FieldSpec("date", "End of the approved service period."),
    "decision_text": FieldSpec("text", "The decision wording exactly as printed, for example 'Approved' or 'Denied' or 'Approved in part'.", _PAYER_RESPONSES),
    "denial_reason_text": FieldSpec("text", "The payer's stated reason for the denial, copied as printed. Null if none.", (DocType.FULL_DENIAL, DocType.PARTIAL_DENIAL)),
    "letter_date": FieldSpec("date", "Date printed on the letter."),
    "appeal_deadline": FieldSpec("date", "Last day to appeal, if the letter states one."),
}

FIELD_NAMES = tuple(FIELD_SPECS)

_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]{5,19}$")
_CODE_RE = re.compile(r"^(\d{5}|[A-Z]\d{4})$")


class FieldValue(BaseModel):
    """A normalized value (str, int, ISO date string, or None) plus confidence 0..1."""

    value: str | int | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Extraction(BaseModel):
    """What any extractor must return. The AI proposes; the rules decide."""

    fields: dict[str, FieldValue]
    doc_type_hint: DocType | None = None
    backend: str
    model_id: str | None = None
    page_count: int = 1
    notes: str | None = None
    disagreements: list[str] = Field(default_factory=list)  # field names where two independent readers differ

    def get(self, name: str) -> Any:
        fv = self.fields.get(name)
        return fv.value if fv else None

    def conf(self, name: str) -> float:
        fv = self.fields.get(name)
        return fv.confidence if fv else 0.0


# ---------------------------------------------------------------- coercion

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y")


def parse_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def coerce(name: str, raw: Any) -> tuple[str | int | None, str | None]:
    """Normalize a raw value to the field's type. Returns (value, problem).

    A problem means the value could not be trusted; callers zero the
    confidence so the item lands in human review instead of being guessed.
    """
    spec = FIELD_SPECS[name]
    if raw is None:
        return None, None
    text = str(raw).strip()
    if text == "" or text.lower() in {"null", "none", "n/a", "not stated"}:
        return None, None
    if spec.kind == "int":
        m = re.search(r"-?\d[\d,]*", text)
        if not m:
            return None, f"{name}: not a number"
        return int(m.group(0).replace(",", "")), None
    if spec.kind == "date":
        iso = parse_date(text)
        return (iso, None) if iso else (None, f"{name}: unreadable date")
    if spec.kind in ("id", "code"):
        cleaned = re.sub(r"\s+", "", text).upper()
        return cleaned, None
    return re.sub(r"\s+", " ", text), None


def validate_field(name: str, value: Any) -> str | None:
    """Return a problem description, or None if the value is acceptable."""
    if value is None:
        return None
    kind = FIELD_SPECS[name].kind
    if kind == "id" and not _ID_RE.match(str(value)):
        return f"{name}: unexpected format"
    if kind == "code" and not _CODE_RE.match(str(value)):
        return f"{name}: not a CPT/HCPCS-shaped code"
    if kind == "int" and (not isinstance(value, int) or value < 0 or value > 100000):
        return f"{name}: out of range"
    if kind == "date":
        try:
            d = dt.date.fromisoformat(str(value))
        except ValueError:
            return f"{name}: unreadable date"
        if not (2015 <= d.year <= 2040):
            return f"{name}: implausible year"
    return None


def validate_cross_field(fields: dict[str, Any]) -> list[str]:
    """Rules that span fields. Same function for every intake path."""
    problems: list[str] = []
    ru, au = fields.get("requested_units"), fields.get("approved_units")
    if isinstance(ru, int) and isinstance(au, int) and au > ru:
        problems.append("approved_units exceeds requested_units")
    for a, b in (("requested_start", "requested_end"), ("approved_start", "approved_end")):
        if fields.get(a) and fields.get(b) and fields[b] < fields[a]:
            problems.append(f"{b} is before {a}")
    return problems


def normalize_raw(raw_fields: dict[str, dict[str, Any]]) -> dict[str, FieldValue]:
    """Raw {name: {value, confidence}} from any backend -> validated FieldValues.

    Unknown field names are dropped. Values that fail coercion or validation
    keep their raw presence but get confidence 0 so the gate sends them to a person.
    """
    out: dict[str, FieldValue] = {}
    for name in FIELD_NAMES:
        entry = raw_fields.get(name) or {}
        value, problem = coerce(name, entry.get("value"))
        try:
            conf = float(entry.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = min(max(conf, 0.0), 1.0)
        if value is None:
            conf = conf if entry.get("value") is None else 0.0
        if problem or validate_field(name, value):
            conf = 0.0
        out[name] = FieldValue(value=value, confidence=round(conf, 3))
    return out


def extraction_tool_schema() -> dict[str, Any]:
    """JSON schema handed to Bedrock as a forced tool call. Derived, never hand-written."""
    field_props = {
        name: {
            "type": "object",
            "description": spec.description,
            "properties": {
                "value": {"type": ["string", "null"], "description": "Exactly as printed, or null if absent or unreadable."},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "How sure you are this was read correctly."},
            },
            "required": ["value", "confidence"],
        }
        for name, spec in FIELD_SPECS.items()
    }
    return {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": [d.value for d in DocType], "description": "Your best classification of the document."},
            "fields": {"type": "object", "properties": field_props, "required": list(FIELD_SPECS)},
            "notes": {"type": "string", "description": "Anything unusual about the scan quality or layout. No medical details."},
        },
        "required": ["doc_type", "fields"],
    }
