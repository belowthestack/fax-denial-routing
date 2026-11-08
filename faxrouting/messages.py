"""Plain-language wording, in one place.

Everything a billing coordinator reads on the board comes from these templates:
the one-line summary of a letter, why it landed where it did, and the drafted
next step. They are fixed templates keyed by document type and denial reason,
not text written per card, so the same letter always produces the same words.
The pipeline stores `next_step` as the draft that waits for approval, and the
dashboard shows that same text, so the two cannot drift apart.
"""
from __future__ import annotations

import datetime as dt
import re

from .rules import REASON_PATTERNS
from .schema import DenialReason, DocType

DEMO_YEAR = 2026

FIELD_LABELS = {
    "payer_name": "Insurance company",
    "member_id": "Member ID",
    "auth_number": "Authorization or reference number",
    "service_code": "Service code",
    "requested_units": "Units asked for",
    "approved_units": "Units approved",
    "requested_start": "Start date asked for",
    "requested_end": "End date asked for",
    "approved_start": "Approved from",
    "approved_end": "Approved through",
    "decision_text": "Decision on the letter",
    "denial_reason_text": "Reason on the letter",
    "letter_date": "Letter date",
    "appeal_deadline": "Appeal deadline",
}

# Column names on the board.
COLUMN_NEW, COLUMN_NEEDS_YOU, COLUMN_READY, COLUMN_DONE = "new", "needs_you", "ready", "done"

REASON_LABEL = {
    DenialReason.MISSING_DOCUMENTATION: "Missing paperwork",
    DenialReason.MEDICAL_NECESSITY: "Medical necessity",
    DenialReason.ELIGIBILITY: "Coverage problem",
    DenialReason.EXPIRED_AUTH: "Approval expired",
    DenialReason.CODING: "Billing code",
}

# How the denial reads in "Denied: ..." and "Rest denied: ..."
_REASON_PHRASE = {
    DenialReason.MISSING_DOCUMENTATION: "missing paperwork",
    DenialReason.MEDICAL_NECESSITY: "they say it is not medically necessary",
    DenialReason.ELIGIBILITY: "a coverage problem",
    DenialReason.EXPIRED_AUTH: "the approval had already ended",
    DenialReason.CODING: "a billing code problem",
}

_NEXT_STEP = {
    DenialReason.MISSING_DOCUMENTATION: "Send the paperwork they asked for{by}.",
    DenialReason.MEDICAL_NECESSITY: "Ask the clinical team to write an appeal{by}.",
    DenialReason.ELIGIBILITY: "Call the insurance company to confirm coverage, then fix the patient's insurance record.",
    DenialReason.EXPIRED_AUTH: "Ask the insurance company for a new approval before more visits.",
    DenialReason.CODING: "Fix the billing code and send the request again.",
}

# Review reasons come from the rules layer as short strings; these turn them into one plain sentence.
_LOW = re.compile(r"^(?P<f>\w+) low confidence$")
_MISSING = re.compile(r"^(?P<f>\w+) missing$")
_DISAGREE = re.compile(r"^readers disagree on (?P<f>\w+)$")


_INLINE = {
    "member_id": "Member ID", "auth_number": "authorization number", "payer_name": "insurance company name",
    "approved_units": "approved units", "requested_units": "requested units", "service_code": "service code",
    "letter_date": "letter date", "appeal_deadline": "appeal deadline",
}


def inline(field: str) -> str:
    """Field name for the middle of a sentence."""
    return _INLINE.get(field, label(field).lower())


def label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " ").capitalize())


def fmt_date(iso: str | None, year: bool = False) -> str:
    """'2026-12-05' -> 'Dec 5' (cards) or 'Dec 5, 2026' (side panel)."""
    if not iso:
        return ""
    d = dt.date.fromisoformat(str(iso))
    base = f"{d.strftime('%b')} {d.day}"
    return f"{base}, {d.year}" if year else base


def _units(n: int | None) -> str:
    return f"{n} unit{'s' if n != 1 else ''}"


def partial_cuts(f: dict) -> list[str]:
    """What was actually cut from a partly approved request: units, dates, or both."""
    cuts = []
    ru, au = f.get("requested_units"), f.get("approved_units")
    if isinstance(ru, int) and isinstance(au, int) and au < ru:
        cuts.append(f"{au} of {ru} units")
    rs, re_, as_, ae = f.get("requested_start"), f.get("requested_end"), f.get("approved_start"), f.get("approved_end")
    if ae and re_ and ae < re_:
        cuts.append(f"only through {fmt_date(ae)}, not {fmt_date(re_)}")
    if as_ and rs and as_ > rs:
        cuts.append(f"only from {fmt_date(as_)}, not {fmt_date(rs)}")
    return cuts


def headline(doc_type: DocType, reason: DenialReason | None, f: dict, needs_review: bool = False) -> str:
    """One line saying what the letter says."""
    if needs_review:
        return {
            DocType.AUTH_APPROVED: "Looks like an approval.",
            DocType.FULL_DENIAL: "Looks like a denial.",
            DocType.PARTIAL_DENIAL: "Looks like a partial approval.",
            DocType.NOT_PAYER_RESPONSE: "Might not be from an insurance company.",
        }[doc_type]
    if doc_type == DocType.AUTH_APPROVED:
        end = f.get("approved_end") or f.get("requested_end")
        through = f" through {fmt_date(end)}" if end else ""
        return f"Approved: {_units(f['approved_units'])}{through}." if isinstance(f.get("approved_units"), int) else "Approved."
    if doc_type == DocType.PARTIAL_DENIAL:
        cuts = partial_cuts(f)
        return "Partly approved: " + " and ".join(cuts) + "." if cuts else "Partly approved."
    if doc_type == DocType.FULL_DENIAL:
        return f"Denied: {_REASON_PHRASE[reason]}." if reason else "Denied."
    return "Not insurance, ignored."


def next_step(doc_type: DocType, reason: DenialReason | None, f: dict) -> str | None:
    """The drafted next step, or None when there is nothing to do."""
    if doc_type in (DocType.FULL_DENIAL, DocType.PARTIAL_DENIAL) and reason:
        deadline = f.get("appeal_deadline")
        return _NEXT_STEP[reason].format(by=f" by {fmt_date(deadline)}" if deadline else "")
    return None


def done_text(doc_type: DocType) -> str:
    return {
        DocType.AUTH_APPROVED: "Filed. Nothing to do.",
        DocType.NOT_PAYER_RESPONSE: "Not insurance, ignored.",
    }.get(doc_type, "")


def review_why(reasons: list[str]) -> str:
    """One or two plain sentences on why a person needs to look."""
    disagree = {m["f"] for r in reasons if (m := _DISAGREE.match(r))}
    sentences: list[str] = []
    for r in reasons:
        if m := _DISAGREE.match(r):
            s = f"The system read the {inline(m['f'])} two different ways. Check it."
        elif m := _LOW.match(r):
            if m["f"] in disagree:
                continue  # already covered by the disagreement sentence
            s = f"The {inline(m['f'])} is hard to read. Check it."
        elif m := _MISSING.match(r):
            s = f"The {inline(m['f'])} could not be read. Check it."
        elif "ambiguous reason" in r:
            s = "The letter gives more than one reason. Decide where it goes."
        elif "reason not recognized" in r or "no denial reason" in r:
            s = "The reason for the denial is unclear. Check the letter."
        elif "exceeds" in r or "before" in r:
            s = "Two numbers on the letter do not add up. Check them."
        elif "model labelled" in r or "letter says" in r:
            s = "The wording and the numbers on the letter disagree. Check it."
        else:
            s = "Something on the letter looks off. Check it."
        if s not in sentences:
            sentences.append(s)
    return " ".join(sentences[:2])


def matched_phrase(reason: DenialReason, text: str | None) -> str:
    """The words on the letter that triggered the keyword match, for 'why it is here'."""
    m = REASON_PATTERNS[reason].search((text or "").lower())
    return m.group(0).strip() if m else ""


def why_here(doc_type: DocType, reason: DenialReason | None, f: dict, needs_review: bool, review_reasons: list[str]) -> str:
    if needs_review:
        return review_why(review_reasons)
    if doc_type == DocType.AUTH_APPROVED:
        return "The letter approves what was asked for, so it is filed."
    if doc_type == DocType.NOT_PAYER_RESPONSE:
        return "This is not a decision from an insurance company, so it is ignored."
    if reason:
        phrase = matched_phrase(reason, f.get("denial_reason_text"))
        quote = f"The letter says \"{phrase}\", so" if phrase else "The reason on the letter points to this, so"
        return f"{quote} it went to {REASON_LABEL[reason]}."
    return "A person needs to decide where this goes."
