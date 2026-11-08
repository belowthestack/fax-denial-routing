"""The board: one plain card per fax, computed from the database.

Which column a fax is in, what its card says, and which fields a person should
double-check are decided here in Python, from the same rows the pipeline wrote.
The dashboard only draws these cards.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from . import messages as m
from .schema import CONFIDENCE_FLOOR, FIELD_SPECS, DenialReason, DocType, Extraction
from .store import Store

DEMO_TODAY = "2026-11-06"
SOON_DAYS = 30


def fixture_of(source_key: str | None) -> str:
    return (source_key or "").split(":", 1)[-1].removesuffix(".pdf")


def check_fields(ex: Extraction, doc_type: DocType) -> list[str]:
    """Fields a person should look at: the readers disagree, confidence is low, or a needed value is missing."""
    out = []
    for name, spec in FIELD_SPECS.items():
        fv = ex.fields[name]
        if name in ex.disagreements:
            out.append(name)
        elif fv.value is not None and fv.confidence < CONFIDENCE_FLOOR:
            out.append(name)
        elif fv.value is None and doc_type in spec.required_for:
            out.append(name)
    return out


PANEL_ORDER = ("payer_name", "member_id", "auth_number", "service_code", "requested_units", "approved_units",
               "requested_start", "requested_end", "approved_start", "approved_end", "decision_text",
               "denial_reason_text", "letter_date", "appeal_deadline")


def _show(name: str, value: Any) -> str:
    if value is None:
        return "Not on the page"
    if FIELD_SPECS[name].kind == "date":
        return m.fmt_date(str(value), year=True)
    return str(value)


def panel_fields(ex: Extraction, check: list[str], readers: dict[str, dict] | None) -> list[dict[str, Any]]:
    """Rows for the side panel: each field marked 'sure' or 'check'. Shown if it has a value or needs a look."""
    rows = []
    for name in PANEL_ORDER:
        fv = ex.fields[name]
        if fv.value is None and name not in check:
            continue
        row: dict[str, Any] = {"name": name, "label": m.label(name), "value": _show(name, fv.value), "status": "check" if name in check else "sure", "note": ""}
        if name in ex.disagreements and readers and name in readers:
            a, b = readers[name]["haiku"], readers[name]["sonnet"]
            row["note"] = f"The system read this two ways: {_show(name, a)} and {_show(name, b)}."
            row["value"] = "Unsure"
        elif name in check and fv.value is None:
            row["value"] = "Could not read"
        rows.append(row)
    return rows


def _due(deadline: str | None) -> tuple[str, bool]:
    if not deadline:
        return "", False
    days = (dt.date.fromisoformat(deadline) - dt.date.fromisoformat(DEMO_TODAY)).days
    return f"Due {m.fmt_date(deadline)}", days <= SOON_DAYS


def build_cards(store: Store, patients: dict[str, str | None], readers: dict[str, dict] | None = None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    faxes = store.rows("SELECT * FROM faxes ORDER BY rowid")
    for i, fx in enumerate(faxes, start=1):
        fixture = fixture_of(fx["source_key"])
        card: dict[str, Any] = {
            "card_id": fx["fax_id"], "fax_id": fx["fax_id"], "doc_id": None, "fixture": fixture,
            "patient": None, "sender": "Sender not identified", "column": m.COLUMN_NEW,
            "headline": "", "why": "", "step": None, "state_text": "", "reason_label": "",
            "deadline": None, "deadline_text": "", "due_soon": False, "check_fields": [],
            "action_id": None, "action_status": None, "order": i, "fields": [],
        }
        doc = store.one("SELECT * FROM documents WHERE fax_id = ?", (fx["fax_id"],))
        if doc is None:
            card.update(
                headline="Couldn't read this yet.", why="The fax is kept safely. We will try again.",
                state_text="Waiting to retry" if fx["status"] == "dead_letter" else "Reading",
            )
            cards.append(card)
            continue
        ex = Extraction.model_validate_json(doc["extraction"])
        f = {k: v.value for k, v in ex.fields.items()}
        doc_type = DocType(doc["doc_type"])
        reason = DenialReason(doc["denial_reason"]) if doc["denial_reason"] else None
        needs_review = bool(doc["needs_review"])
        reasons = json.loads(doc["review_reasons"])
        action = store.one("SELECT * FROM actions WHERE doc_id = ? ORDER BY created_at DESC LIMIT 1", (doc["doc_id"],))
        deadline_text, due_soon = _due(f.get("appeal_deadline"))
        card.update(
            patient=patients.get(fixture), doc_id=doc["doc_id"], sender=f.get("payer_name") or "Sender not identified",
            headline=m.headline(doc_type, reason, f, needs_review), why=m.why_here(doc_type, reason, f, needs_review, reasons),
            reason_label=m.REASON_LABEL[reason] if reason and not needs_review else "",
            deadline=f.get("appeal_deadline"), deadline_text=deadline_text, due_soon=due_soon,
            check_fields=check_fields(ex, doc_type) if needs_review else [],
        )
        card["fields"] = panel_fields(ex, card["check_fields"], (readers or {}).get(fixture))
        if needs_review:
            card.update(column=m.COLUMN_NEEDS_YOU, step="Open the fax and confirm what it says.", state_text="Needs you")
        elif doc_type in (DocType.AUTH_APPROVED, DocType.NOT_PAYER_RESPONSE):
            card.update(column=m.COLUMN_DONE, state_text=m.done_text(doc_type))
        elif action is None:
            card.update(column=m.COLUMN_READY, step=m.next_step(doc_type, reason, f), state_text="Ready to send")
        elif action["status"] == "pending_approval":
            card.update(column=m.COLUMN_READY, step=action["draft"], state_text="Ready to send", action_id=action["action_id"], action_status=action["status"])
        else:
            sent = action["status"] in ("approved", "dispatched")
            card.update(column=m.COLUMN_DONE, step=action["draft"], state_text="Approved and sent (demo)" if sent else "Not sent",
                        action_id=action["action_id"], action_status=action["status"])
        cards.append(card)
    return cards


def summary_line(cards: list[dict[str, Any]]) -> str:
    """The sentence at the top of the board."""
    need = sum(1 for c in cards if c["column"] == m.COLUMN_NEEDS_YOU)
    soon = sum(1 for c in cards if c["column"] in (m.COLUMN_NEEDS_YOU, m.COLUMN_READY) and c["due_soon"])
    parts = []
    parts.append(f"{need} need{'s' if need == 1 else ''} you today" if need else "Nothing needs you right now")
    if soon:
        parts.append(f"{soon} {'has' if soon == 1 else 'have'} a deadline within {SOON_DAYS} days")
    return " · ".join(parts)
