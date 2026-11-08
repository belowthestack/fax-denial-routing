"""Deterministic rules layer. The AI proposes; this file decides.

Every decision carries the IDs of the rules that fired, with a plain-English
detail line. There is no model call in this file and no randomness, so the
same extraction always yields the same decision. Rule ID prefixes:

  V  validation        C  classification       R  denial reason
  G  confidence gate   Q  queue assignment     P  priority
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

from .schema import (
    CONFIDENCE_FLOOR,
    FIELD_SPECS,
    REASON_TO_QUEUE,
    DenialReason,
    DocType,
    Extraction,
    Queue,
    validate_cross_field,
    validate_field,
)


@dataclass
class RuleHit:
    rule_id: str
    detail: str


@dataclass
class Decision:
    doc_type: DocType
    denial_reason: DenialReason | None
    queue: Queue
    needs_review: bool
    tentative_queue: Queue | None  # where it would go if a person confirms the extraction
    priority: str  # normal | high
    hits: list[RuleHit] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["doc_type"] = self.doc_type.value
        d["denial_reason"] = self.denial_reason.value if self.denial_reason else None
        d["queue"] = self.queue.value
        d["tentative_queue"] = self.tentative_queue.value if self.tentative_queue else None
        return d


# Reason keywords. Order does not matter: if more than one reason matches,
# rule R-MULTI sends the document to a person rather than guessing.
REASON_PATTERNS: dict[DenialReason, re.Pattern[str]] = {
    DenialReason.MISSING_DOCUMENTATION: re.compile(
        r"missing documentation|additional documentation|documentation (was |were )?not received|"
        r"records? (was |were )?not received|progress notes?|treatment plan (was )?not|insufficient documentation|"
        r"supporting documents?|requested (records|information)"
    ),
    DenialReason.MEDICAL_NECESSITY: re.compile(
        r"medical(ly)? necess|not medically|clinical criteria|does not meet (the )?(criteria|guidelines)|"
        r"level of care|not clinically (indicated|appropriate)"
    ),
    DenialReason.ELIGIBILITY: re.compile(
        r"not eligible|ineligible|eligibility|coverage (was )?(terminated|ended|inactive)|no active coverage|"
        r"not covered under|member (is )?not enrolled|inactive (member|policy)"
    ),
    DenialReason.EXPIRED_AUTH: re.compile(
        r"expired|authorization period (has )?ended|outside (of )?the authorized|after the authorization end|"
        r"retro(active)? (authorization|request)|dates of service (are )?outside"
    ),
    DenialReason.CODING: re.compile(
        r"invalid (code|modifier|procedure)|incorrect (code|modifier)|coding error|modifier|"
        r"unbundl|not a (valid|covered) (cpt|hcpcs|procedure code)|code (is )?not valid"
    ),
}

_DENIAL_WORDS = re.compile(r"\b(denied|denial|not approved|adverse determination|unable to approve)\b")
_APPROVE_WORDS = re.compile(r"\b(approved|authorized|certified)\b")
_PARTIAL_WORDS = re.compile(r"(in part|partial)")


RULE_CATALOG: dict[str, str] = {
    "V-FIELD-01": "A field value fails the shared format check (ID shape, code shape, range, date sanity).",
    "V-CROSS-01": "Two fields contradict each other (approved above requested, end date before start date).",
    "C-NPR-01": "No payer plus member/auth identifier and no decision wording: not a payer response.",
    "C-NPR-02": "No decision wording and no unit counts: not a payer response.",
    "C-NPR-03": "Payer-like letter with no usable decision evidence: not a payer response.",
    "C-DEN-01": "Approved units are 0: full denial.",
    "C-DEN-02": "Decision wording says denied and no approved units are stated: full denial.",
    "C-PART-01": "Approved units are fewer than requested units: partial denial.",
    "C-PART-02": "Approved dates cover less than the requested dates: partial denial.",
    "C-APP-01": "Units and dates approved as requested: authorization approved.",
    "C-APP-02": "Decision wording says approved with no unit counts: authorization approved.",
    "C-CONFLICT-01": "The model's label disagrees with what the numbers say: human review.",
    "C-CONFLICT-02": "The printed decision wording disagrees with the numbers: human review.",
    "R-MISSING_DOCUMENTATION": "Reason text matches the missing documentation keywords.",
    "R-MEDICAL_NECESSITY": "Reason text matches the medical necessity keywords.",
    "R-ELIGIBILITY": "Reason text matches the eligibility keywords.",
    "R-EXPIRED_AUTH": "Reason text matches the expired authorization keywords.",
    "R-CODING": "Reason text matches the coding keywords.",
    "R-MULTI-01": "Reason text matches more than one category: a person decides.",
    "R-NONE-01": "A denial with no stated reason: human review.",
    "R-NONE-02": "Reason text matches no category: human review.",
    "G-MISSING-01": "A field required for this document type is missing.",
    "G-CONF-01": "A required field is below the 0.80 confidence floor.",
    "G-DISAGREE-01": "Two independent model readers disagree on a high-stakes field.",
    "Q-APP-01": "Approvals are filed with no denial action.",
    "Q-NPR-01": "Documents that are not payer responses are filed with no action.",
    "Q-MISSING_DOCUMENTATION": "Route to the missing documentation queue.",
    "Q-MEDICAL_NECESSITY": "Route to the medical necessity queue.",
    "Q-ELIGIBILITY": "Route to the eligibility queue.",
    "Q-EXPIRED_AUTH": "Route to the expired authorization queue.",
    "Q-CODING": "Route to the coding queue.",
    "Q-REVIEW-01": "Any failed check routes the document to human review with reasons attached.",
    "P-DEADLINE-01": "Appeal window of 30 days or fewer from the letter date: high priority.",
}


def _present(ex: Extraction, name: str) -> bool:
    return ex.get(name) is not None


def validate(ex: Extraction) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for name, fv in ex.fields.items():
        problem = validate_field(name, fv.value)
        if problem:
            hits.append(RuleHit("V-FIELD-01", problem))
    for problem in validate_cross_field({k: v.value for k, v in ex.fields.items()}):
        hits.append(RuleHit("V-CROSS-01", problem))
    return hits


def classify(ex: Extraction) -> tuple[DocType, list[RuleHit]]:
    """Decide the document type from the evidence, not from the model's label."""
    hits: list[RuleHit] = []
    payer = _present(ex, "payer_name")
    ids = _present(ex, "member_id") or _present(ex, "auth_number")
    decision = str(ex.get("decision_text") or "").lower()
    has_numbers = _present(ex, "approved_units") or _present(ex, "requested_units")

    if not (payer and ids) and not decision:
        hits.append(RuleHit("C-NPR-01", "No payer plus member/auth identifier and no decision wording: not a payer response."))
        return DocType.NOT_PAYER_RESPONSE, hits
    if not decision and not has_numbers:
        hits.append(RuleHit("C-NPR-02", "No decision wording and no unit counts: not a payer response."))
        return DocType.NOT_PAYER_RESPONSE, hits

    ru, au = ex.get("requested_units"), ex.get("approved_units")
    rs, re_ = ex.get("requested_start"), ex.get("requested_end")
    as_, ae = ex.get("approved_start"), ex.get("approved_end")

    if isinstance(au, int) and au == 0:
        hits.append(RuleHit("C-DEN-01", "approved_units is 0: nothing approved, full denial."))
        return DocType.FULL_DENIAL, hits
    if isinstance(au, int) and isinstance(ru, int):
        if au < ru:
            hits.append(RuleHit("C-PART-01", f"approved_units {au} is less than requested_units {ru}: partial denial."))
            return DocType.PARTIAL_DENIAL, hits
    if rs and re_ and as_ and ae and (as_ > rs or ae < re_):
        hits.append(RuleHit("C-PART-02", "Approved dates cover less than the requested dates: partial denial."))
        return DocType.PARTIAL_DENIAL, hits
    if isinstance(au, int) and au > 0:
        hits.append(RuleHit("C-APP-01", "Units and dates approved as requested."))
        return DocType.AUTH_APPROVED, hits
    if _DENIAL_WORDS.search(decision) and not isinstance(au, int):
        hits.append(RuleHit("C-DEN-02", "Decision wording says denied and no approved units are stated."))
        return DocType.FULL_DENIAL, hits
    if _APPROVE_WORDS.search(decision):
        hits.append(RuleHit("C-APP-02", "Decision wording says approved; no unit counts to confirm."))
        return DocType.AUTH_APPROVED, hits
    hits.append(RuleHit("C-NPR-03", "Payer letter but no usable decision evidence."))
    return DocType.NOT_PAYER_RESPONSE, hits


def crosscheck(ex: Extraction, doc_type: DocType) -> list[RuleHit]:
    """Flag disagreement between the model's label, the printed decision, and the numbers."""
    hits: list[RuleHit] = []
    if ex.doc_type_hint and ex.doc_type_hint != doc_type:
        hits.append(RuleHit("C-CONFLICT-01", f"Model labelled this {ex.doc_type_hint.value}; the numbers say {doc_type.value}."))
    decision = str(ex.get("decision_text") or "").lower()
    if decision:
        says_denied = bool(_DENIAL_WORDS.search(decision)) and not _PARTIAL_WORDS.search(decision)
        says_partial = bool(_PARTIAL_WORDS.search(decision))
        says_approved = bool(_APPROVE_WORDS.search(decision)) and not says_denied and not says_partial
        if says_approved and doc_type in (DocType.FULL_DENIAL, DocType.PARTIAL_DENIAL):
            hits.append(RuleHit("C-CONFLICT-02", f"Letter says '{decision}' but the numbers show a denial."))
        if says_denied and doc_type == DocType.AUTH_APPROVED:
            hits.append(RuleHit("C-CONFLICT-02", f"Letter says '{decision}' but the numbers show an approval."))
    return hits


def reason_for(ex: Extraction) -> tuple[DenialReason | None, list[RuleHit], list[str]]:
    text = str(ex.get("denial_reason_text") or "").lower()
    if not text:
        return None, [RuleHit("R-NONE-01", "Denial letter states no reason.")], ["no denial reason on a denial"]
    matched = [r for r, pat in REASON_PATTERNS.items() if pat.search(text)]
    if len(matched) == 1:
        r = matched[0]
        return r, [RuleHit(f"R-{r.name}", f"Reason text matches {r.value}.")], []
    if not matched:
        return None, [RuleHit("R-NONE-02", "Reason text matches no known category.")], ["denial reason not recognized"]
    names = ", ".join(sorted(m.value for m in matched))
    return None, [RuleHit("R-MULTI-01", f"Reason text matches several categories ({names}); a person decides.")], [f"ambiguous reason: {names}"]


def confidence_gate(ex: Extraction, doc_type: DocType) -> tuple[list[RuleHit], list[str]]:
    hits: list[RuleHit] = []
    reasons: list[str] = []
    for name in ex.disagreements:
        hits.append(RuleHit("G-DISAGREE-01", f"Two independent readers disagree on {name}."))
        reasons.append(f"readers disagree on {name}")
    for name, spec in FIELD_SPECS.items():
        if doc_type not in spec.required_for:
            continue
        fv = ex.fields.get(name)
        if fv is None or fv.value is None:
            hits.append(RuleHit("G-MISSING-01", f"Required field {name} is missing for {doc_type.value}."))
            reasons.append(f"{name} missing")
        elif fv.confidence < CONFIDENCE_FLOOR:
            hits.append(RuleHit("G-CONF-01", f"{name} confidence {fv.confidence:.2f} is below {CONFIDENCE_FLOOR:.2f}."))
            reasons.append(f"{name} low confidence")
    return hits, reasons


def priority_for(ex: Extraction) -> tuple[str, list[RuleHit]]:
    deadline, sent = ex.get("appeal_deadline"), ex.get("letter_date")
    if deadline and sent:
        days = (dt.date.fromisoformat(str(deadline)) - dt.date.fromisoformat(str(sent))).days
        if days <= 30:
            return "high", [RuleHit("P-DEADLINE-01", f"Appeal window is {days} days from the letter date (30 or fewer).")]
    return "normal", []


def decide(ex: Extraction) -> Decision:
    """Run every layer in a fixed order and return one auditable decision."""
    hits: list[RuleHit] = []
    review: list[str] = []

    v_hits = validate(ex)
    hits += v_hits
    review += [h.detail for h in v_hits]

    doc_type, c_hits = classify(ex)
    hits += c_hits
    x_hits = crosscheck(ex, doc_type)
    hits += x_hits
    review += [h.detail for h in x_hits]

    reason: DenialReason | None = None
    if doc_type in (DocType.FULL_DENIAL, DocType.PARTIAL_DENIAL):
        reason, r_hits, r_review = reason_for(ex)
        hits += r_hits
        review += r_review

    g_hits, g_review = confidence_gate(ex, doc_type)
    hits += g_hits
    review += g_review

    priority, p_hits = priority_for(ex)
    hits += p_hits

    if doc_type == DocType.NOT_PAYER_RESPONSE:
        tentative = Queue.NOT_PAYER
        hits.append(RuleHit("Q-NPR-01", "Not a payer response: filed, no action."))
    elif doc_type == DocType.AUTH_APPROVED:
        tentative = Queue.APPROVED_FILED
        hits.append(RuleHit("Q-APP-01", "Approval: filed, no denial action."))
    elif reason is not None:
        tentative = REASON_TO_QUEUE[reason]
        hits.append(RuleHit(f"Q-{reason.name}", f"Routed to the {reason.value} queue."))
    else:
        tentative = None

    needs_review = bool(review)
    if needs_review:
        queue = Queue.HUMAN_REVIEW
        hits.append(RuleHit("Q-REVIEW-01", "At least one check failed: routed to human review with the reasons attached."))
    else:
        queue = tentative or Queue.HUMAN_REVIEW

    return Decision(
        doc_type=doc_type,
        denial_reason=reason,
        queue=queue,
        needs_review=needs_review,
        tentative_queue=tentative,
        priority=priority,
        hits=hits,
        review_reasons=sorted(set(review)),
    )
