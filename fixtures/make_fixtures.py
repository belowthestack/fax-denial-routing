"""Generate the synthetic payer-letter fixtures.

Everything here is invented: payer names carry "(SAMPLE)", member and
authorization numbers are made up, phone numbers use the reserved 555-01xx
range. Ten letters cover approvals, five denial reasons, two partial denials
and one document that is not from a payer. Two are image-only "scans" with
noise and a slight skew, which only a vision model can read.

Run:  python fixtures/make_fixtures.py
Writes fixtures/letters/<name>.pdf and <name>.expected.json (the ground truth
the tests compare against).
"""
from __future__ import annotations

import io
import json
import random
from pathlib import Path

import fitz
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "letters"

LETTERS = [
    dict(
        name="01_approved_aba_assessment", style="A", scan=False, payer="Peachtree Mutual Health (SAMPLE)",
        member="PMH48201937", ref_label="Authorization Number", ref="AUTH-2026-77412", code="97153",
        req_units=480, app_units=480, req=("2026-11-01", "2027-04-30"), app=("2026-11-01", "2027-04-30"),
        decision="Approved", reason=None, letter_date="2026-10-28", appeal=None,
        body="Your request for adaptive behavior treatment by protocol has been reviewed and approved as submitted.",
        expect=dict(doc_type="auth_approved", queue="approved_filed", reason=None, priority="normal"),
    ),
    dict(
        name="02_approved_scanned_blurry", style="B", scan="blurry", payer="Blue Heron Benefits (SAMPLE)",
        member="BHB99320415", ref_label="Authorization No.", ref="BH-5520817", code="97151",
        req_units=16, app_units=16, req=("2026-11-09", "2027-02-08"), app=("2026-11-09", "2027-02-08"),
        decision="Approved", reason=None, letter_date="2026-11-02", appeal=None,
        body="The behavior identification assessment you requested is authorized for the dates below.",
        expect=dict(doc_type="auth_approved", queue="approved_filed", reason=None, priority="normal"),
    ),
    dict(
        name="03_denial_medical_necessity", style="A", scan=False, payer="Magnolia State Health Plan (SAMPLE)",
        member="MSH30481226", ref_label="Reference Number", ref="REF-2026-0091823", code="97153",
        req_units=600, app_units=0, req=("2026-11-01", "2027-04-30"), app=(None, None),
        decision="Denied", reason="The requested level of care does not meet medical necessity criteria for the service requested.",
        letter_date="2026-11-03", appeal="2027-01-02",
        body="We are unable to approve the authorization request described below.",
        expect=dict(doc_type="full_denial", queue="medical_necessity", reason="medical_necessity", priority="normal"),
    ),
    dict(
        name="04_denial_missing_documentation", style="B", scan=False, payer="Summit Care Alliance (SAMPLE)",
        member="SCA71049388", ref_label="Reference No.", ref="SC-8841270", code="92507",
        req_units=48, app_units=0, req=("2026-11-15", "2027-02-14"), app=(None, None),
        decision="Denied", reason="Authorization could not be completed: additional documentation was not received (current treatment plan and progress notes).",
        letter_date="2026-11-05", appeal="2026-12-05",
        body="This request was denied because the information needed to complete the review was not provided.",
        expect=dict(doc_type="full_denial", queue="missing_documentation", reason="missing_documentation", priority="high"),
    ),
    dict(
        name="05_denial_eligibility", style="A", scan=False, payer="Peachtree Mutual Health (SAMPLE)",
        member="PMH61130742", ref_label="Reference Number", ref="PM-3390184", code="97153",
        req_units=320, app_units=0, req=("2026-11-01", "2027-01-31"), app=(None, None),
        decision="Denied", reason="Member coverage was terminated effective 2026-09-30; the member is not eligible for the requested dates of service.",
        letter_date="2026-11-05", appeal="2027-01-04",
        body="Our records show a change in the member's enrollment status.",
        expect=dict(doc_type="full_denial", queue="eligibility", reason="eligibility", priority="normal"),
    ),
    dict(
        name="06_denial_expired_auth", style="B", scan=False, payer="Magnolia State Health Plan (SAMPLE)",
        member="MSH11928834", ref_label="Authorization No.", ref="AUTH-2026-20155", code="97155",
        req_units=80, app_units=0, req=("2026-11-01", "2026-12-31"), app=(None, None),
        decision="Denied", reason="The authorization period has ended. The requested dates of service are outside the authorized period (expired 2026-08-31).",
        letter_date="2026-11-02", appeal="2026-12-17",
        body="The request below cannot be applied to the authorization on file.",
        expect=dict(doc_type="full_denial", queue="expired_auth", reason="expired_auth", priority="normal"),
    ),
    dict(
        name="07_denial_coding_scanned", style="A", scan="skew", payer="Blue Heron Benefits (SAMPLE)",
        member="BHB20817364", ref_label="Reference Number", ref="BH-6671902", code="92507",
        req_units=24, app_units=0, req=("2026-11-10", "2027-01-31"), app=(None, None),
        decision="Denied", reason="Invalid modifier submitted with procedure code 92507. Resubmit with the corrected code and modifier.",
        letter_date="2026-11-04", appeal="2027-01-03",
        body="The request was returned because of an error in how the service was submitted.",
        expect=dict(doc_type="full_denial", queue="coding", reason="coding", priority="normal"),
    ),
    dict(
        name="08_partial_units_medical_necessity", style="B", scan=False, payer="Summit Care Alliance (SAMPLE)",
        member="SCA55208816", ref_label="Authorization Number", ref="SC-AUTH-2026-3380", code="97153",
        req_units=600, app_units=360, req=("2026-11-01", "2027-04-30"), app=("2026-11-01", "2027-04-30"),
        decision="Approved in part", reason="Remaining units are not approved. The additional units do not meet medical necessity guidelines.",
        letter_date="2026-11-04", appeal="2026-12-30",
        body="A portion of the requested units has been authorized. See the details below.",
        expect=dict(doc_type="partial_denial", queue="medical_necessity", reason="medical_necessity", priority="normal"),
    ),
    dict(
        name="09_partial_dates_missing_documentation", style="A", scan=False, payer="Peachtree Mutual Health (SAMPLE)",
        member="PMH82776104", ref_label="Authorization Number", ref="AUTH-2026-80391", code="97153",
        req_units=480, app_units=480, req=("2026-11-01", "2027-04-30"), app=("2026-11-01", "2027-01-15"),
        decision="Approved in part", reason="Approval is limited to 75 days. Additional documentation is required to authorize the remaining period.",
        letter_date="2026-11-05", appeal="2026-12-05",
        body="The full number of units is approved, but only for a shorter service period than requested.",
        expect=dict(doc_type="partial_denial", queue="missing_documentation", reason="missing_documentation", priority="high"),
    ),
    dict(
        name="10_not_a_payer_vendor_flyer", style="X", scan=False, payer="Lakeshore Therapy Supply (SAMPLE)",
        member=None, ref_label=None, ref=None, code=None, req_units=None, app_units=None, req=(None, None), app=(None, None),
        decision=None, reason=None, letter_date="2026-11-03", appeal=None, body="",
        expect=dict(doc_type="not_payer_response", queue="not_payer_response", reason=None, priority="normal"),
    ),
]


def _line(c: canvas.Canvas, y: float, label: str, value: str) -> float:
    c.setFont("Helvetica-Bold", 10)
    c.drawString(60, y, f"{label}:")
    c.setFont("Helvetica", 10)
    c.drawString(200, y, value)
    return y - 16


def _wrap(c: canvas.Canvas, text: str, x: float, y: float, width: float = 92) -> float:
    import textwrap

    c.setFont("Helvetica", 10)
    for ln in textwrap.wrap(text, width):
        c.drawString(x, y, ln)
        y -= 14
    return y


def render_pdf(L: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    c.setFont("Courier", 7.5)
    d = L["letter_date"][5:7] + "/" + L["letter_date"][8:10] + "/" + L["letter_date"][:4]
    c.drawString(40, h - 24, f"FAX RECEIVED {d} 09:14 AM   FROM +1 (555) 010-0{random.Random(L['name']).randint(100,199)}   TO +1 (555) 010-0142   PAGE 1 OF 1")
    c.line(40, h - 30, w - 40, h - 30)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(60, h - 70, L["payer"])
    c.setFont("Helvetica", 9)
    c.drawString(60, h - 84, "Utilization Management Department  |  Synthetic sample document")
    c.line(60, h - 92, w - 60, h - 92)
    y = h - 120
    if L["style"] == "X":
        c.setFont("Helvetica-Bold", 20)
        c.drawString(60, y, "FALL THERAPY EQUIPMENT SALE")
        y -= 30
        y = _wrap(c, "Weighted blankets, sensory swings and visual schedule kits. Order by November 30 and save 15 percent on any clinic order over 500 dollars. Free shipping to licensed providers.", 60, y, 80)
        y -= 14
        y = _wrap(c, "To be removed from our fax list, call 555-0198.", 60, y, 80)
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(60, 50, "SAMPLE DOCUMENT. SYNTHETIC DATA. NOT A REAL ORGANIZATION.")
        c.save()
        return buf.getvalue()
    A = L["style"] == "A"
    c.setFont("Helvetica-Bold", 12)
    c.drawString(60, y, "Notice of Authorization Determination")
    y -= 24
    y = _line(c, y, "Letter Date" if A else "Date of Letter", L["letter_date"])
    y = _line(c, y, "Payer" if A else "Health Plan", L["payer"])
    y = _line(c, y, "Member ID" if A else "Subscriber ID", L["member"])
    y = _line(c, y, L["ref_label"], L["ref"])
    y = _line(c, y, "Service Code" if A else "CPT/HCPCS", L["code"])
    y = _line(c, y, "Units Requested" if A else "Requested Units", str(L["req_units"]))
    y = _line(c, y, "Units Approved" if A else "Approved Units", str(L["app_units"]))
    y = _line(c, y, "Requested Start" if A else "Requested From", L["req"][0])
    y = _line(c, y, "Requested End" if A else "Requested Through", L["req"][1])
    if L["app"][0]:
        y = _line(c, y, "Approved Start" if A else "Approved From", L["app"][0])
        y = _line(c, y, "Approved End" if A else "Approved Through", L["app"][1])
    y = _line(c, y, "Decision" if A else "Determination", L["decision"])
    if L["reason"]:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(60, y, "Reason:" if A else "Basis for Determination:")
        c.setFont("Helvetica", 10)
        y = _wrap(c, "", 60, y - 14)
        y = _wrap(c, L["reason"], 60, y, 88)
        y -= 6
    y -= 8
    y = _wrap(c, L["body"], 60, y)
    if L["appeal"]:
        y -= 10
        y = _line(c, y, "Appeal Deadline" if A else "Appeal By", L["appeal"])
        y = _wrap(c, "You may request a reconsideration by submitting a written appeal before the deadline above.", 60, y)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(60, 50, "SAMPLE DOCUMENT. SYNTHETIC DATA. NOT A REAL ORGANIZATION. NO REAL MEMBER OR PATIENT.")
    c.save()
    return buf.getvalue()


def to_scan(pdf: bytes, mode: str, seed: str) -> bytes:
    """Rasterize, degrade like a fax machine would, and re-wrap with no text layer."""
    rnd = random.Random(seed)
    page = fitz.open(stream=pdf, filetype="pdf")[0]
    img = Image.open(io.BytesIO(page.get_pixmap(dpi=140).tobytes("png"))).convert("L")
    img = img.rotate(rnd.uniform(-1.4, 1.4) if mode == "skew" else rnd.uniform(-0.8, 0.8), resample=Image.BICUBIC, fillcolor=255)
    if mode == "blurry":
        # blur the upper-middle band (member and auth lines) harder than the rest
        w, h = img.size
        # smear only the member-ID and authorization lines, and only the right-hand value column
        box = (int(w * 0.32), int(h * 0.245), int(w * 0.62), int(h * 0.305))
        img.paste(img.crop(box).filter(ImageFilter.GaussianBlur(1.6)), box[:2])
    px = img.load()
    for _ in range(img.size[0] * img.size[1] // 500):
        x, y = rnd.randrange(img.size[0]), rnd.randrange(img.size[1])
        px[x, y] = 0 if rnd.random() < 0.5 else 255
    img = img.point(lambda v: 255 if v > 150 else 0).convert("1")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    out = fitz.open()
    p = out.new_page(width=612, height=792)
    p.insert_image(p.rect, stream=buf.getvalue())
    return out.tobytes()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for L in LETTERS:
        pdf = render_pdf(L)
        if L["scan"]:
            pdf = to_scan(pdf, L["scan"], L["name"])
        (OUT / f"{L['name']}.pdf").write_bytes(pdf)
        truth = dict(
            payer_name=L["payer"], member_id=L["member"], auth_number=L["ref"], service_code=L["code"],
            requested_units=L["req_units"], approved_units=L["app_units"],
            requested_start=L["req"][0], requested_end=L["req"][1], approved_start=L["app"][0], approved_end=L["app"][1],
            decision_text=L["decision"], denial_reason_text=L["reason"], letter_date=L["letter_date"], appeal_deadline=L["appeal"],
        )
        (OUT / f"{L['name']}.expected.json").write_text(
            json.dumps({"scanned": bool(L["scan"]), **L["expect"], "fields": truth}, indent=2) + "\n", encoding="utf-8"
        )
        print(L["name"], "scan" if L["scan"] else "text", len(pdf))


if __name__ == "__main__":
    main()
