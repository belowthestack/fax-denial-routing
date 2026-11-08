"""Unit tests for the deterministic layer, using hand-built extractions (no documents, no model)."""
from __future__ import annotations

import pytest

from faxrouting.rules import RULE_CATALOG, decide
from faxrouting.schema import (
    FIELD_NAMES,
    FIELD_SPECS,
    DocType,
    Extraction,
    FieldValue,
    coerce,
    extraction_tool_schema,
    normalize_raw,
    validate_cross_field,
    validate_field,
)


def ex(hint=None, conf=0.95, disagree=(), **vals) -> Extraction:
    fields = {n: FieldValue(value=None, confidence=0.0) for n in FIELD_NAMES}
    base = dict(payer_name="Test Plan (SAMPLE)", member_id="TST12345678", auth_number="AUTH-100001", requested_units=100,
                letter_date="2026-11-01", decision_text="Denied", approved_units=0)
    base.update(vals)
    for k, v in base.items():
        fields[k] = FieldValue(value=v, confidence=conf if v is not None else 0.0)
    return Extraction(fields=fields, doc_type_hint=hint, backend="test", disagreements=list(disagree))


def ids(d):
    return {h.rule_id for h in d.hits}


def test_zero_approved_is_full_denial_and_routes_by_reason():
    d = decide(ex(denial_reason_text="Additional documentation was not received."))
    assert d.doc_type == DocType.FULL_DENIAL and d.queue.value == "missing_documentation"
    assert {"C-DEN-01", "R-MISSING_DOCUMENTATION", "Q-MISSING_DOCUMENTATION"} <= ids(d)


def test_fewer_units_is_partial():
    d = decide(ex(approved_units=40, decision_text="Approved in part", denial_reason_text="Does not meet medical necessity criteria."))
    assert d.doc_type == DocType.PARTIAL_DENIAL and "C-PART-01" in ids(d) and d.queue.value == "medical_necessity"


def test_narrower_dates_is_partial_even_when_units_match():
    d = decide(ex(approved_units=100, decision_text="Approved in part", requested_start="2026-11-01", requested_end="2027-04-30",
                  approved_start="2026-11-01", approved_end="2027-01-15", denial_reason_text="Additional documentation is required."))
    assert d.doc_type == DocType.PARTIAL_DENIAL and "C-PART-02" in ids(d)


def test_full_match_is_approved():
    d = decide(ex(approved_units=100, decision_text="Approved"))
    assert d.doc_type == DocType.AUTH_APPROVED and d.queue.value == "approved_filed" and not d.needs_review


def test_not_a_payer_response():
    d = decide(ex(payer_name=None, member_id=None, auth_number=None, requested_units=None, approved_units=None, decision_text=None))
    assert d.doc_type == DocType.NOT_PAYER_RESPONSE and d.queue.value == "not_payer_response"


def test_two_reasons_in_one_letter_go_to_a_person():
    d = decide(ex(denial_reason_text="Coverage was terminated and additional documentation was not received."))
    assert d.queue.value == "human_review" and "R-MULTI-01" in ids(d)
    assert d.tentative_queue is None


def test_unknown_reason_and_missing_reason_go_to_a_person():
    assert "R-NONE-02" in ids(decide(ex(denial_reason_text="Plan policy.")))
    assert "R-NONE-01" in ids(decide(ex(denial_reason_text=None)))


def test_low_confidence_on_a_required_field_goes_to_review_but_keeps_tentative_route():
    d = decide(ex(conf=0.6, denial_reason_text="Coverage was terminated."))
    assert d.needs_review and d.queue.value == "human_review" and d.tentative_queue.value == "eligibility"
    assert "G-CONF-01" in ids(d)


def test_reader_disagreement_goes_to_review():
    d = decide(ex(denial_reason_text="Coverage was terminated.", disagree=["member_id"]))
    assert d.needs_review and "G-DISAGREE-01" in ids(d)


def test_model_label_conflict_goes_to_review():
    d = decide(ex(hint=DocType.AUTH_APPROVED, denial_reason_text="Coverage was terminated."))
    assert d.needs_review and "C-CONFLICT-01" in ids(d)


def test_printed_decision_contradicting_numbers_goes_to_review():
    d = decide(ex(approved_units=0, decision_text="Approved", denial_reason_text="Coverage was terminated."))
    assert "C-CONFLICT-02" in ids(d) and d.needs_review


def test_short_appeal_window_is_high_priority():
    assert decide(ex(denial_reason_text="Coverage was terminated.", appeal_deadline="2026-11-30")).priority == "high"
    assert decide(ex(denial_reason_text="Coverage was terminated.", appeal_deadline="2027-02-01")).priority == "normal"


def test_decisions_are_deterministic():
    a = decide(ex(denial_reason_text="Coverage was terminated.")).to_dict()
    b = decide(ex(denial_reason_text="Coverage was terminated.")).to_dict()
    assert a == b


def test_every_rule_id_that_can_fire_is_documented():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "faxrouting" / "rules.py").read_text()
    used = set(re.findall(r'RuleHit\("([A-Z]-[A-Z0-9\-]+)"', src))
    assert used <= set(RULE_CATALOG), used - set(RULE_CATALOG)


# ---- the shared schema


def test_coerce_and_validate():
    assert coerce("requested_units", "1,200 units") == (1200, None)
    assert coerce("letter_date", "November 3, 2026") == ("2026-11-03", None)
    assert coerce("letter_date", "13/45/2026")[1]
    assert coerce("member_id", " pmh 123 45678 ") == ("PMH12345678", None)
    assert validate_field("member_id", "!!") and validate_field("service_code", "9715") and validate_field("approved_units", -1)
    assert validate_field("letter_date", "1999-01-01")
    assert validate_cross_field({"requested_units": 10, "approved_units": 12})
    assert validate_cross_field({"approved_start": "2026-02-01", "approved_end": "2026-01-01"})


def test_bad_values_get_zero_confidence_never_silently_accepted():
    out = normalize_raw({"member_id": {"value": "??", "confidence": 0.99}, "approved_units": {"value": "lots", "confidence": 0.99}})
    assert out["member_id"].confidence == 0.0 and out["approved_units"].confidence == 0.0


def test_tool_schema_is_derived_from_the_shared_field_specs():
    schema = extraction_tool_schema()
    assert set(schema["properties"]["fields"]["properties"]) == set(FIELD_SPECS)
    assert schema["properties"]["doc_type"]["enum"] == [d.value for d in DocType]


def test_every_intake_path_uses_the_same_normalizer():
    """Bedrock, replay and the local text backend all build fields via schema.normalize_raw."""
    import inspect

    from faxrouting import extractors

    src = inspect.getsource(extractors)
    assert src.count("normalize_raw(") >= 2 and "def coerce" not in src and "def validate" not in src
