"""The plain-language layer: fixed templates, the four board columns, and a banned-words guard."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import EXPECT_REVIEW
from faxrouting import board, messages as m
from faxrouting.schema import DenialReason, DocType

ROOT = Path(__file__).resolve().parents[1]
PATIENTS = json.loads((ROOT / "fixtures" / "patients.json").read_text())
PATIENTS.pop("_note")

# Words that must never reach the main board or the side panel.
BANNED = [
    r"hash", r"dead.?letter", r"idempot", r"pipeline", r"extract", r"schema", r"\b[CRGQVP]-[A-Z0-9_\-]+\b",
    r"haiku", r"sonnet", r"bedrock", r"claude", r"connectionerror", r"\.pdf", r"\bmodel\b", r"\brule", chr(0x2014), chr(0x2013),
]


def assert_plain(text: str) -> None:
    for pat in BANNED:
        assert not re.search(pat, text, re.I), f"{pat!r} in {text!r}"


def test_every_reason_has_headline_next_step_and_label():
    f = {"appeal_deadline": "2026-12-05", "approved_units": 0, "requested_units": 100}
    for reason in DenialReason:
        step = m.next_step(DocType.FULL_DENIAL, reason, f)
        assert step and m.REASON_LABEL[reason] and m.headline(DocType.FULL_DENIAL, reason, f).startswith("Denied: ")
        assert_plain(step)


def test_deadline_appears_only_when_the_letter_has_one():
    with_d = m.next_step(DocType.FULL_DENIAL, DenialReason.MISSING_DOCUMENTATION, {"appeal_deadline": "2026-12-05"})
    without = m.next_step(DocType.FULL_DENIAL, DenialReason.MISSING_DOCUMENTATION, {})
    assert with_d == "Send the paperwork they asked for by Dec 5."
    assert without == "Send the paperwork they asked for."


def test_templates_are_fixed_per_reason_not_per_card():
    a = m.next_step(DocType.FULL_DENIAL, DenialReason.CODING, {"payer_name": "A", "member_id": "X1"})
    b = m.next_step(DocType.PARTIAL_DENIAL, DenialReason.CODING, {"payer_name": "B", "member_id": "Y2"})
    assert a == b


def test_partial_denial_shows_what_was_cut_never_480_of_480():
    dates = dict(requested_units=480, approved_units=480, requested_start="2026-11-01", requested_end="2027-04-30",
                 approved_start="2026-11-01", approved_end="2027-01-15")
    h = m.headline(DocType.PARTIAL_DENIAL, DenialReason.MISSING_DOCUMENTATION, dates)
    assert "Jan 15" in h and "Apr 30" in h and "480 of 480" not in h
    units = dict(requested_units=600, approved_units=360, requested_end="2027-04-30", approved_end="2027-04-30")
    assert m.headline(DocType.PARTIAL_DENIAL, DenialReason.MEDICAL_NECESSITY, units) == "Partly approved: 360 of 600 units."


def test_review_sentences_are_plain():
    s = m.review_why(["member_id low confidence", "readers disagree on member_id"])
    assert s == "The system read the Member ID two different ways. Check it."
    assert m.review_why(["approved_units missing"]) == "The approved units could not be read. Check it."
    assert_plain(m.review_why(["ambiguous reason: coding, eligibility", "approved_units exceeds requested_units"]))


def test_board_columns_for_the_recorded_pass(pipeline, letters):
    for name, (data, _) in letters.items():
        pipeline.ingest(data, "folder", name + ".pdf")
    cards = {c["fixture"]: c for c in board.build_cards(pipeline.store, PATIENTS)}
    assert {n for n, c in cards.items() if c["column"] == "needs_you"} == EXPECT_REVIEW
    assert cards["01_approved_aba_assessment"]["column"] == "done"
    assert cards["10_not_a_payer_vendor_flyer"]["column"] == "done"
    assert cards["10_not_a_payer_vendor_flyer"]["state_text"] == "Not insurance, ignored."
    ready = {n for n, c in cards.items() if c["column"] == "ready"}
    assert ready == {"03_denial_medical_necessity", "04_denial_missing_documentation", "06_denial_expired_auth",
                     "08_partial_units_medical_necessity", "09_partial_dates_missing_documentation"}
    assert cards["09_partial_dates_missing_documentation"]["headline"] == "Partly approved: only through Jan 15, not Apr 30."
    assert cards["04_denial_missing_documentation"]["step"] == "Send the paperwork they asked for by Dec 5."
    assert cards["04_denial_missing_documentation"]["due_soon"] and cards["04_denial_missing_documentation"]["deadline_text"] == "Due Dec 5"
    assert cards["05_denial_eligibility"]["check_fields"] == ["member_id"]
    assert cards["05_denial_eligibility"]["why"] == "The system read the Member ID two different ways. Check it."


def test_every_visible_card_string_is_plain(pipeline, letters):
    for name, (data, _) in letters.items():
        pipeline.ingest(data, "folder", name + ".pdf")
    for c in board.build_cards(pipeline.store, PATIENTS):
        for key in ("headline", "why", "step", "state_text", "deadline_text", "reason_label"):
            if c[key]:
                assert_plain(c[key])


def test_approving_moves_the_card_to_done(pipeline, letters):
    r = pipeline.ingest(letters["03_denial_medical_necessity"][0], "folder", "03_denial_medical_necessity.pdf")
    card = next(c for c in board.build_cards(pipeline.store, PATIENTS) if c["doc_id"] == r.doc_id)
    assert card["column"] == "ready" and card["action_status"] == "pending_approval"
    pipeline.decide_action(card["action_id"], True, "approver.test")
    pipeline.dispatch_action(card["action_id"])
    card = next(c for c in board.build_cards(pipeline.store, PATIENTS) if c["doc_id"] == r.doc_id)
    assert card["column"] == "done" and card["state_text"] == "Approved and sent (demo)"


def test_dead_lettered_fax_shows_as_new_with_a_plain_message(letters):
    from faxrouting.pipeline import Pipeline
    from faxrouting.store import Store

    class Down:
        name = "down"

        def extract(self, data):
            raise ConnectionError("x")

    p = Pipeline(Store(), Down(), alert=lambda msg: None)
    p.ingest(letters["03_denial_medical_necessity"][0], "folder", "03_denial_medical_necessity.pdf")
    (card,) = board.build_cards(p.store, PATIENTS)
    assert card["column"] == "new" and card["headline"] == "Couldn't read this yet."
    assert card["patient"] is None, "an unread fax cannot know the patient yet"
    assert_plain(card["headline"] + card["why"] + card["state_text"])


def test_summary_line():
    base = {"column": "needs_you", "due_soon": False}
    assert board.summary_line([base, base, {"column": "ready", "due_soon": True}]) == "2 need you today · 1 has a deadline within 30 days"
    assert board.summary_line([]) == "Nothing needs you right now"


def test_every_fixture_has_a_patient_entry():
    letters = {p.stem for p in (ROOT / "fixtures" / "letters").glob("*.pdf")}
    assert letters == set(PATIENTS)
