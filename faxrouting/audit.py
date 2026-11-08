"""Audit logging with a whitelist.

The audit trail records WHAT happened and WHICH rule fired, never the values
read from a document. Only these keys may appear in `detail`, and string values
must be short machine tokens (rule IDs, queue names, field NAMES), not free
text. Anything else raises, so a future change cannot quietly start logging
member IDs, units, dates or reason text.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from .store import Store

ALLOWED_DETAIL_KEYS = frozenset(
    {
        "source", "backend", "model_id", "doc_type", "queue", "tentative_queue", "priority",
        "rule_ids", "review_reason_codes", "fields_changed", "stage", "error_class",
        "action_kind", "action_id", "duplicate_of", "page_count", "retry_of", "needs_review",
        "min_confidence", "outcome",
    }
)
_TOKEN = re.compile(r"^[A-Za-z0-9_.:+|\-]{1,120}$")


def _check(key: str, value: Any) -> None:
    if key not in ALLOWED_DETAIL_KEYS:
        raise ValueError(f"audit detail key not allowed: {key}")
    if isinstance(value, (list, tuple)):
        for v in value:
            _check(key, v)
    elif isinstance(value, str):
        if not _TOKEN.match(value):
            raise ValueError(f"audit detail value for {key} is not a short token")
    elif not isinstance(value, (int, float, bool)) and value is not None:
        raise ValueError(f"audit detail value for {key} has an unsupported type")


def log(store: Store, conn: sqlite3.Connection, event: str, *, fax_id: str | None = None, doc_id: str | None = None, actor: str = "system", **detail: Any) -> None:
    for k, v in detail.items():
        _check(k, v)
    store.append_audit(conn, event, fax_id, doc_id, actor, detail)


# Short, fixed review reason codes so the audit trail never holds free text.
def review_reason_codes(reasons: list[str]) -> list[str]:
    codes = []
    for r in reasons:
        r = r.lower()
        if r.endswith("low confidence"):
            codes.append("low_confidence:" + r.split()[0])
        elif r.endswith("missing") and " " in r and len(r.split()) == 2:
            codes.append("missing:" + r.split()[0])
        elif r.startswith("readers disagree on"):
            codes.append("disagree:" + r.split()[-1])
        elif "ambiguous reason" in r:
            codes.append("ambiguous_reason")
        elif "reason not recognized" in r or "no denial reason" in r:
            codes.append("reason_unrecognized")
        elif "exceeds" in r or "before" in r:
            codes.append("cross_field_invalid")
        elif "model labelled" in r or "letter says" in r:
            codes.append("label_conflict")
        else:
            codes.append("validation")
    return sorted(set(c[:48] for c in codes))
