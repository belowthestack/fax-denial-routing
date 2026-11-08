"""Build the static dashboard's data by running the real pipeline.

The dashboard is a static site (it runs on Cloudflare Pages with no backend), so
it cannot call the API. To avoid re-implementing any rule in JavaScript, this
script runs the Python pipeline over the fixtures and records, for every button
the dashboard offers, the exact database rows that action would change
("patches"). The browser only applies those rows. Nothing is decided in JS.

Run:  python scripts/export_demo.py
Writes dashboard/data/demo.json, dashboard/data/img/*.png, dashboard/data/pdf/*.pdf
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faxrouting.config import CAPTURED  # noqa: E402
from faxrouting.extractors import ConsensusExtractor, ReplayExtractor, content_hash  # noqa: E402
from faxrouting.pipeline import Pipeline  # noqa: E402
from faxrouting.rules import RULE_CATALOG  # noqa: E402
from faxrouting.schema import CONSENSUS_FIELDS, FIELD_SPECS  # noqa: E402
from faxrouting.store import Store  # noqa: E402

LETTERS = ROOT / "fixtures" / "letters"
OUT = ROOT / "dashboard" / "data"
OUTAGE_FIXTURE = "06_denial_expired_auth"  # arrives while the model API is "down"

JSON_COLS = {"documents": ("review_reasons", "extraction", "rule_hits")}
PKS = {"faxes": "fax_id", "documents": "doc_id", "actions": "action_id", "dead_letter": "dl_id", "audit_log": "seq", "outbox": "outbox_id"}


class FailOnce:
    """Simulates a Bedrock outage for one specific document, once."""

    name = "outage-sim"

    def __init__(self, inner, bad_hash: str):
        self.inner, self.bad_hash, self.armed = inner, bad_hash, True

    def extract(self, data: bytes):
        if self.armed and content_hash(data) == self.bad_hash:
            self.armed = False
            raise ConnectionError("simulated Bedrock outage")
        return self.inner.extract(data)


def snapshot(store: Store) -> dict[str, dict]:
    snap: dict[str, dict] = {}
    for table, pk in PKS.items():
        rows = store.rows(f"SELECT * FROM {table}")
        out = {}
        for r in rows:
            r.pop("blob", None)
            for col in JSON_COLS.get(table, ()):
                r[col] = json.loads(r[col])
            if table == "audit_log":
                r["detail"] = json.loads(r["detail"])
            if table == "faxes":
                r["fixture"] = (r["source_key"] or "").split(":", 1)[-1].removesuffix(".pdf")
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
    alerts: list[str] = []
    pipe = Pipeline(Store(), FailOnce(consensus, content_hash(letters[OUTAGE_FIXTURE])), alert=alerts.append)

    # Arrival order: the outage fax first, so the dead letter is visible at the top of the incoming queue.
    order = [OUTAGE_FIXTURE] + [n for n in letters if n != OUTAGE_FIXTURE]
    for n in order:
        pipe.ingest(letters[n], "folder", f"{n}.pdf")
    # One duplicate arrival, so the audit trail shows suppression.
    pipe.ingest(letters["03_denial_medical_necessity"], "folder", "03_denial_medical_necessity.pdf")

    base = snapshot(pipe.store)
    patches: dict[str, dict] = {"duplicate": {}, "retry": {}, "review": {}, "action": {}}

    # Re-sending any fax: the engine's real duplicate handling.
    for fx in base["faxes"].values():
        c = clone(pipe.store)
        cp = Pipeline(c, consensus, alert=lambda m: None)
        cp.ingest(letters[fx["fixture"]], "folder", fx["fixture"] + ".pdf")
        patches["duplicate"][fx["fax_id"]] = patch_between(base, snapshot(c))

    # Retrying the dead letter after the "outage" ends.
    for dl in base["dead_letter"].values():
        c = clone(pipe.store)
        cp = Pipeline(c, consensus, alert=lambda m: None)
        cp.retry_dead_letter(dl["dl_id"])
        after = snapshot(c)
        patches["retry"][dl["dl_id"]] = patch_between(base, after)
        # approval chain for the action the retry created
        for act in [a for a in after["actions"].values() if a["action_id"] not in base["actions"]]:
            patches["action"][act["action_id"]] = action_patches(c, consensus, after, act["action_id"])

    # Human review: apply the values a person would read off the page.
    for doc in base["documents"].values():
        if not doc["needs_review"]:
            continue
        fx = base["faxes"][doc["fax_id"]]
        t = truth[fx["fixture"]]["fields"]
        corrections = {k: v for k, v in t.items() if v is not None and k not in ("payer_name", "denial_reason_text")}
        c = clone(pipe.store)
        cp = Pipeline(c, consensus, alert=lambda m: None)
        cp.resolve_review(doc["doc_id"], corrections, reviewer="reviewer.demo")
        after = snapshot(c)
        patches["review"][doc["doc_id"]] = {"corrections": corrections, "patch": patch_between(base, after)}
        for act in [a for a in after["actions"].values() if a["action_id"] not in base["actions"]]:
            patches["action"][act["action_id"]] = action_patches(c, consensus, after, act["action_id"])

    # Approvals for actions already open in the base state.
    for act in base["actions"].values():
        c = clone(pipe.store)
        patches["action"][act["action_id"]] = action_patches(c, consensus, base, act["action_id"])

    # Per-reader values for the document viewer (so a disagreement can be shown side by side).
    readers: dict[str, dict] = {}
    for n, data in letters.items():
        a, b = haiku.extract(data), sonnet.extract(data)
        readers[n] = {f: {"haiku": a.get(f), "sonnet": b.get(f)} for f in CONSENSUS_FIELDS}

    # Images and PDFs of the faxes.
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    (OUT / "pdf").mkdir(parents=True, exist_ok=True)
    for n, data in letters.items():
        page = fitz.open(stream=data, filetype="pdf")[0]
        page.get_pixmap(dpi=110).save(OUT / "img" / f"{n}.png")
        (OUT / "pdf" / f"{n}.pdf").write_bytes(data)

    captured = [json.loads(p.read_text()) for p in sorted(CAPTURED.glob("*/*.json"))]
    demo = {
        "meta": {
            "synthetic": True,
            "readers": sorted({c["model_id"] for c in captured}),
            "captured_at": max(c["captured_at"] for c in captured)[:10],
            "outage_fixture": OUTAGE_FIXTURE,
            "alerts": alerts,
            "fixture_count": len(letters),
        },
        "base": base,
        "patches": patches,
        "readers": readers,
        "truth": {n: t["fields"] for n, t in truth.items()},
        "rules": RULE_CATALOG,
        "field_specs": {k: {"kind": v.kind, "description": v.description, "required_for": [d.value for d in v.required_for]} for k, v in FIELD_SPECS.items()},
    }
    (OUT / "demo.json").write_text(json.dumps(demo, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"documents={len(base['documents'])} faxes={len(base['faxes'])} audit={len(base['audit_log'])} dead_letters={len(base['dead_letter'])} actions={len(base['actions'])}")
    print("review docs:", sum(1 for d in base["documents"].values() if d["needs_review"]))
    print("bytes:", (OUT / "demo.json").stat().st_size)


def action_patches(store: Store, extractor, state: dict, action_id: str) -> dict:
    """Patches for approve, reject, and approve-then-dispatch on one action, from a given state."""
    out = {}
    for label, approve in (("approve", True), ("reject", False)):
        c = clone(store)
        Pipeline(c, extractor, alert=lambda m: None).decide_action(action_id, approve, "approver.demo")
        out[label] = patch_between(state, snapshot(c))
        if approve:
            decided = snapshot(c)
            c2 = clone(c)
            Pipeline(c2, extractor, alert=lambda m: None).dispatch_action(action_id)
            out["dispatch"] = patch_between(decided, snapshot(c2))
    return out


if __name__ == "__main__":
    main()
