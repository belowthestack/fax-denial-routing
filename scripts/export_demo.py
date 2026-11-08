"""Build the static dashboard's data by playing the real pipeline.

The dashboard is a static site (Cloudflare Pages, no backend), so it cannot call
the API. To avoid re-implementing any rule or any wording in JavaScript, this
script plays the Python pipeline over the fixtures and records, for every button
the dashboard offers, the exact rows that action would change ("patches"). The
browser only applies those rows and draws the cards. Nothing is decided in JS.

Start of the demo: 7 of the 10 letters have arrived. The other 3 are held back so
"Simulate a new fax" and "Simulate an outage" have something real to bring in.

Play it:  python scripts/export_demo.py
Writes dashboard/data/demo.json, dashboard/data/img/*.png, dashboard/data/pdf/*.pdf
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faxrouting import board, messages  # noqa: E402
from faxrouting.config import CAPTURED  # noqa: E402
from faxrouting.extractors import ConsensusExtractor, ReplayExtractor, content_hash  # noqa: E402
from faxrouting.pipeline import Pipeline  # noqa: E402
from faxrouting.rules import RULE_CATALOG, decide  # noqa: E402
from faxrouting.schema import CONSENSUS_FIELDS  # noqa: E402
from faxrouting.store import Store  # noqa: E402

LETTERS = ROOT / "fixtures" / "letters"
OUT = ROOT / "dashboard" / "data"
REPO_URL = "https://github.com/belowthestack/fax-denial-routing"

# Held back at the start; "Simulate" brings them in, in this order.
POOL = ["08_partial_units_medical_necessity", "06_denial_expired_auth", "10_not_a_payer_vendor_flyer"]

JSON_COLS = {"documents": ("review_reasons", "extraction", "rule_hits"), "cards": ("check_fields", "fields")}
PKS = {"faxes": "fax_id", "documents": "doc_id", "actions": "action_id", "dead_letter": "dl_id", "audit_log": "seq",
       "outbox": "outbox_id", "cards": "card_id", "summary": "id"}

# What each letter is, in plain words, for the recorded-pass table on the "How it works" page.
LETTER_DESC = {
    "01_approved_aba_assessment": "Clean approval",
    "02_approved_scanned_blurry": "Approval, blurred scan (units unreadable)",
    "03_denial_medical_necessity": "Denial: medical necessity",
    "04_denial_missing_documentation": "Denial: missing documentation, 30-day appeal window",
    "05_denial_eligibility": "Denial: eligibility",
    "06_denial_expired_auth": "Denial: expired authorization",
    "07_denial_coding_scanned": "Denial: coding, skewed scan",
    "08_partial_units_medical_necessity": "Partial denial: fewer units approved",
    "09_partial_dates_missing_documentation": "Partial denial: shorter dates approved, 30-day appeal window",
    "10_not_a_payer_vendor_flyer": "Vendor flyer, not an insurance letter",
}

PATIENTS = {k: v for k, v in json.loads((ROOT / "fixtures" / "patients.json").read_text()).items() if not k.startswith("_")}


class FailOnce:
    """Simulates a model outage for one document, once."""

    name = "outage-sim"

    def __init__(self, inner, bad_hash: str):
        self.inner, self.bad_hash, self.armed = inner, bad_hash, True

    def extract(self, data: bytes):
        if self.armed and content_hash(data) == self.bad_hash:
            self.armed = False
            raise ConnectionError("simulated outage")
        return self.inner.extract(data)


def snapshot(store: Store, readers: dict) -> dict[str, dict]:
    snap: dict[str, dict] = {}
    for table, pk in PKS.items():
        if table == "cards":
            rows = board.build_cards(store, PATIENTS, readers)
        elif table == "summary":
            rows = [{"id": "s", "text": board.summary_line(board.build_cards(store, PATIENTS, readers))}]
        else:
            rows = store.rows(f"SELECT * FROM {table}")
        out = {}
        for r in rows:
            r.pop("blob", None)
            if table in JSON_COLS and table != "cards":
                for col in JSON_COLS[table]:
                    r[col] = json.loads(r[col])
            if table == "audit_log":
                r["detail"] = json.loads(r["detail"])
            if table == "faxes":
                r["fixture"] = board.fixture_of(r["source_key"])
            out[str(r[pk])] = r
        snap[table] = out
    return snap


def clone(store: Store) -> Store:
    new = Store()
    store.conn.backup(new.conn)
    return new


def patch_between(before: dict, after: dict) -> dict:
    """Rows in `after` that are new or changed versus `before`, per table."""
    patch: dict[str, list] = {}
    for table, rows in after.items():
        changed = [row for k, row in rows.items() if before[table].get(k) != row]
        if changed:
            patch[table] = changed
    return patch


def main() -> None:
    letters = {p.stem: p.read_bytes() for p in sorted(LETTERS.glob("*.pdf"))}
    truth = {n: json.loads((LETTERS / f"{n}.expected.json").read_text()) for n in letters}
    haiku = ReplayExtractor(CAPTURED / "haiku-4-5")
    sonnet = ReplayExtractor(CAPTURED / "sonnet-4-6")
    consensus = ConsensusExtractor(haiku, sonnet)
    readers: dict[str, dict] = {}
    for n, data in letters.items():
        a, b = haiku.extract(data), sonnet.extract(data)
        readers[n] = {f: {"haiku": a.get(f), "sonnet": b.get(f)} for f in CONSENSUS_FIELDS}

    def pipe(store: Store, extractor=consensus) -> Pipeline:
        return Pipeline(store, extractor, alert=lambda m: None)

    # ---- the starting board: 7 letters have arrived
    base_store = Store()
    start = [n for n in letters if n not in POOL]
    for n in start:
        pipe(base_store).ingest(letters[n], "folder", f"{n}.pdf")
    base = snapshot(base_store, readers)

    patches: dict[str, dict] = {"arrive": {}, "outage": {}, "retry": {}, "duplicate": {}, "review": {}, "action": {}}

    def downstream(store: Store, state: dict) -> None:
        """From a given state, record what 'send again', 'confirm' and 'approve' would change, for each card."""
        for card in state["cards"].values():
            fixture = card["fixture"]
            fax_id = card["fax_id"]
            if fax_id not in patches["duplicate"] and card["doc_id"]:
                c = clone(store)
                pipe(c).ingest(letters[fixture], "folder", f"{fixture}.pdf")
                patches["duplicate"][fax_id] = patch_between(state, snapshot(c, readers))
            if card["column"] == "needs_you" and card["doc_id"] not in patches["review"]:
                fixes = {k: truth[fixture]["fields"][k] for k in card["check_fields"] if truth[fixture]["fields"][k] is not None}
                c = clone(store)
                pipe(c).resolve_review(card["doc_id"], fixes, reviewer="you")
                after = snapshot(c, readers)
                patches["review"][card["doc_id"]] = {"fields": sorted(fixes), "answers": fixes, "patch": patch_between(state, after)}
                downstream(c, after)
            if card["action_id"] and card["action_status"] == "pending_approval" and card["action_id"] not in patches["action"]:
                c = clone(store)
                p = pipe(c)
                p.decide_action(card["action_id"], True, "you")
                p.dispatch_action(card["action_id"])
                patches["action"][card["action_id"]] = patch_between(state, snapshot(c, readers))

    downstream(base_store, base)

    # ---- the held-back letters: arrive normally, or arrive during an outage and be retried
    for fixture in POOL:
        c = clone(base_store)
        pipe(c).ingest(letters[fixture], "folder", f"{fixture}.pdf")
        arrived = snapshot(c, readers)
        patches["arrive"][fixture] = patch_between(base, arrived)
        downstream(c, arrived)

        o = clone(base_store)
        outage_pipe = pipe(o, FailOnce(consensus, content_hash(letters[fixture])))
        res = outage_pipe.ingest(letters[fixture], "folder", f"{fixture}.pdf")
        down = snapshot(o, readers)
        patches["outage"][fixture] = patch_between(base, down)
        dl = next(iter(v for v in down["dead_letter"].values() if v["fax_id"] == res.fax_id))
        pipe(o).retry_dead_letter(dl["dl_id"])
        retried = snapshot(o, readers)
        patches["retry"][res.fax_id] = patch_between(down, retried)
        downstream(o, retried)

    # ---- the recorded pass over all 10 letters (for the "How it works" page)
    recorded = []
    full = Store()
    fp = pipe(full)
    for n in letters:
        r = fp.ingest(letters[n], "folder", f"{n}.pdf")
        doc = full.one("SELECT * FROM documents WHERE doc_id = ?", (r.doc_id,))
        hits = json.loads(doc["rule_hits"])
        reasons = json.loads(doc["review_reasons"])
        recorded.append({
            "n": int(n[:2]), "letter": LETTER_DESC[n], "type": doc["doc_type"], "queue": doc["queue"],
            "needs_review": bool(doc["needs_review"]), "why_review": messages.review_why(reasons) if doc["needs_review"] else "",
            "after_review": truth[n]["queue"] if doc["needs_review"] else "",
            "rules": sorted({h["rule_id"] for h in hits}),
        })
    stats = {}
    for label, ext in (("haiku", haiku), ("sonnet", sonnet)):
        ok = total = 0
        for n, data in letters.items():
            if n.startswith("10_"):
                continue
            e = ext.extract(data)
            for f in CONSENSUS_FIELDS:
                total += 1
                ok += e.get(f) == truth[n]["fields"][f]
        stats[label] = {"correct": ok, "total": total}
    collected = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], capture_output=True, text=True, cwd=ROOT).stdout
    m_ = re.search(r"(\d+) tests? collected", collected)

    # ---- fax images and PDFs
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    (OUT / "pdf").mkdir(parents=True, exist_ok=True)
    for n, data in letters.items():
        page = fitz.open(stream=data, filetype="pdf")[0]
        page.get_pixmap(dpi=110).save(OUT / "img" / f"{n}.png")
        (OUT / "pdf" / f"{n}.pdf").write_bytes(data)

    captured = [json.loads(p.read_text()) for p in sorted(CAPTURED.glob("*/*.json"))]
    demo = {
        "meta": {
            "demo_today": board.DEMO_TODAY, "soon_days": board.SOON_DAYS, "repo_url": REPO_URL,
            "captured_at": max(c["captured_at"] for c in captured)[:10], "tests_collected": int(m_.group(1)) if m_ else None,
            "reader_stats": stats, "letters": len(letters),
        },
        "base": base,
        "pool": POOL,
        "patches": patches,
        "rules": RULE_CATALOG,
        "recorded": recorded,
        "labels": {
            "types": {"auth_approved": "Approved", "full_denial": "Denied", "partial_denial": "Partly approved", "not_payer_response": "Not insurance"},
            "queues": {"approved_filed": "Filed", "missing_documentation": "Missing paperwork", "medical_necessity": "Medical necessity",
                       "eligibility": "Coverage problem", "expired_auth": "Approval expired", "coding": "Billing code",
                       "human_review": "Needs a person", "not_payer_response": "Ignored"},
        },
    }
    (OUT / "demo.json").write_text(json.dumps(demo, indent=1, default=str) + "\n", encoding="utf-8")
    cards = base["cards"].values()
    print("start cards:", {c: sum(1 for x in cards if x["column"] == c) for c in ("new", "needs_you", "ready", "done")}, "|", base["summary"]["s"]["text"])
    print("patches:", {k: len(v) for k, v in patches.items()}, "| bytes", (OUT / "demo.json").stat().st_size, "| tests", demo["meta"]["tests_collected"], "| readers", stats)


if __name__ == "__main__":
    main()
