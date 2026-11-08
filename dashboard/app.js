/* Static dashboard. It decides nothing: every row it shows, and every change a button makes,
   comes from data/demo.json, which scripts/export_demo.py produced by running the Python pipeline. */
"use strict";

const LABELS = {
  doc: { auth_approved: "Approved", full_denial: "Full denial", partial_denial: "Partial denial", not_payer_response: "Not a payer letter" },
  queue: {
    approved_filed: "Approved, filed", missing_documentation: "Missing documentation", medical_necessity: "Medical necessity",
    eligibility: "Eligibility", expired_auth: "Expired authorization", coding: "Coding",
    human_review: "Human review", not_payer_response: "Not a payer letter",
  },
};
const BOARD = ["missing_documentation", "medical_necessity", "eligibility", "expired_auth", "coding"];
const PK = { faxes: "fax_id", documents: "doc_id", actions: "action_id", dead_letter: "dl_id", audit_log: "seq", outbox: "outbox_id" };
const CONF_FLOOR = 0.8;

let DEMO, S;
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));

function applyPatch(patch) {
  for (const [table, rows] of Object.entries(patch || {})) for (const row of rows) S[table][String(row[PK[table]])] = clone(row);
}
const docs = () => Object.values(S.documents);
const faxOf = (d) => S.faxes[d.fax_id];
const pretty = (fixture) => fixture.replace(/^\d+_/, "").replace(/_/g, " ");
const time = (iso) => (iso || "").slice(11, 19);
const q = (k) => LABELS.queue[k] || k;

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toast.h);
  toast.h = setTimeout(() => t.classList.remove("on"), 4200);
}

function ruleChips(hits, only) {
  const ids = [...new Set(hits.map((h) => h.rule_id))].filter((i) => !only || only.some((p) => i.startsWith(p)));
  return ids.map((i) => `<span class="chip rule" title="${esc(DEMO.rules[i] || "")}">${esc(i)}</span>`).join("");
}

/* ---------------------------------------------------------------- nav */
function nav(route) {
  const open = (t) => Object.values(S[t]).filter((r) => (r.state ? r.state === "open" : true));
  const review = docs().filter((d) => d.needs_review).length;
  const board = docs().filter((d) => BOARD.includes(d.queue)).length;
  const items = [
    ["incoming", "Incoming", Object.keys(S.faxes).length, false],
    ["review", "Review queue", review, review > 0],
    ["board", "Denial board", board, false],
    ["audit", "Audit trail", Object.keys(S.audit_log).length, false],
    ["rules", "Rules", Object.keys(DEMO.rules).length, false],
  ];
  $("#nav").innerHTML = items
    .map(([id, label, n, warn]) => `<a href="#/${id}" class="${route === id ? "on" : ""}"><span>${label}</span><span class="n ${warn ? "warn" : ""}">${n}</span></a>`)
    .join("");
}

/* ---------------------------------------------------------------- incoming */
function viewIncoming() {
  const faxes = Object.values(S.faxes).sort((a, b) => a.received_at.localeCompare(b.received_at));
  const dups = Object.values(S.audit_log).filter((a) => a.event === "duplicate_suppressed").length;
  const dl = Object.values(S.dead_letter).filter((d) => d.state === "open");
  const inReview = docs().filter((d) => d.needs_review).length;
  const routed = docs().filter((d) => !d.needs_review).length;
  const dlHtml = dl.length
    ? `<div class="panel"><div class="panel-h"><div><div class="eyebrow">Dead letter queue</div><h2>${dl.length} fax could not be processed</h2></div></div>
        <table><thead><tr><th>Fax</th><th>Stage</th><th>Error class</th><th>Alert</th><th></th></tr></thead><tbody>
        ${dl.map((d) => `<tr><td class="mono">${esc(pretty(S.faxes[d.fax_id].fixture))}</td><td class="mono">${esc(d.stage)}</td><td class="mono">${esc(d.error_class)}</td>
          <td class="dim">${esc(DEMO.meta.alerts[0] || "Alert sent")}</td>
          <td class="right"><button class="btn sm" data-retry="${d.dl_id}">Retry after outage</button></td></tr>`).join("")}
        </tbody></table>
        <div class="panel-b dim">The model API was simulated as down when this fax arrived. The fax is kept, an alert fired, and nothing was guessed. Retrying re-runs extraction on the stored copy.</div></div>`
    : "";
  return `
  <div class="head"><div class="eyebrow">Fax intake</div><h1>Incoming faxes</h1>
  <p class="lede">Each fax is claimed by its content hash before any model is called, so the same fax can arrive any number of times and still produce one record. Click a row to open the document.</p></div>
  <div class="stats">
    <div class="stat"><div class="eyebrow">Received</div><div class="v">${faxes.length}</div></div>
    <div class="stat"><div class="eyebrow">Routed</div><div class="v">${routed}</div></div>
    <div class="stat"><div class="eyebrow">In review</div><div class="v ${inReview ? "warn" : ""}">${inReview}</div></div>
    <div class="stat"><div class="eyebrow">Dead letter</div><div class="v ${dl.length ? "warn" : ""}">${dl.length}</div></div>
    <div class="stat"><div class="eyebrow">Duplicates suppressed</div><div class="v">${dups}</div></div>
  </div>
  ${dlHtml}
  <div class="panel"><table><thead><tr><th>Arrived</th><th>Source file</th><th>Pages</th><th>Result</th><th>Routed to</th><th>Deciding rules</th><th></th></tr></thead><tbody>
  ${faxes.map((f) => {
    const d = docs().find((x) => x.fax_id === f.fax_id);
    const result = f.status === "dead_letter" ? `<span class="chip warn">Dead letter</span>` : d ? `<span class="chip solid">${LABELS.doc[d.doc_type]}</span>` : "";
    const where = d ? `<span class="chip ${d.needs_review ? "warn" : "ok"}">${q(d.queue)}</span>${d.priority === "high" ? '<span class="chip warn">High priority</span>' : ""}` : '<span class="dim">Held</span>';
    return `<tr class="${d ? "click" : ""}" ${d ? `data-doc="${d.doc_id}"` : ""}>
      <td class="mono dim">${time(f.received_at)}</td><td class="mono">${esc(f.fixture)}.pdf</td><td class="mono">${f.page_count ?? "-"}</td>
      <td>${result}</td><td>${where}</td><td>${d ? ruleChips(d.rule_hits, ["C-", "Q-"]) : ""}</td>
      <td class="right"><button class="btn ghost sm" data-resend="${f.fax_id}">Send again</button></td></tr>`;
  }).join("")}
  </tbody></table></div>
  <p class="note">"Send again" re-submits the identical fax to the pipeline. It is recognized, ignored, and logged as a suppressed duplicate.</p>`;
}

/* ---------------------------------------------------------------- review queue */
function viewReview() {
  const list = docs().filter((d) => d.needs_review);
  return `<div class="head"><div class="eyebrow">Human in the loop</div><h1>Review queue</h1>
  <p class="lede">Anything the checks cannot vouch for lands here with the reasons attached. A person reads the original fax, confirms the values, and the same rules run again. Nothing is drafted for sending until that happens.</p></div>
  ${list.length ? `<div class="panel"><table><thead><tr><th>Fax</th><th>Would route to</th><th>Why it is here</th><th></th></tr></thead><tbody>
  ${list.map((d) => `<tr class="click" data-doc="${d.doc_id}"><td class="mono">${esc(faxOf(d).fixture)}.pdf</td>
    <td>${d.tentative_queue ? `<span class="chip">${q(d.tentative_queue)}</span>` : '<span class="dim">undecided</span>'}</td>
    <td>${d.review_reasons.map((r) => `<span class="chip warn">${esc(r)}</span>`).join("")}</td>
    <td class="right"><button class="btn sm">Open</button></td></tr>`).join("")}
  </tbody></table></div>` : `<div class="panel"><div class="panel-b dim">Review queue is empty. Use "Reset demo" in the sidebar to start over.</div></div>`}`;
}

/* ---------------------------------------------------------------- denial board */
function card(d) {
  const f = d.extraction.fields;
  const v = (k) => f[k].value;
  const units = d.doc_type === "partial_denial" || d.doc_type === "full_denial" ? `${v("approved_units") ?? "?"} of ${v("requested_units") ?? "?"} units approved` : "";
  return `<button class="card" data-doc="${d.doc_id}"><div class="p">${esc(v("payer_name") || "Unknown payer")}</div>
    <div class="m">${esc(v("auth_number") || v("member_id") || "")}</div>
    <div class="units">${esc(LABELS.doc[d.doc_type])} · ${esc(units)}</div>
    <div style="margin-top:8px">${d.priority === "high" ? '<span class="chip warn">Appeal by ' + esc(v("appeal_deadline")) + "</span>" : v("appeal_deadline") ? '<span class="chip">Appeal by ' + esc(v("appeal_deadline")) + "</span>" : ""}${ruleChips(d.rule_hits, ["R-"])}</div></button>`;
}
function viewBoard() {
  const cols = BOARD.map((k) => {
    const list = docs().filter((d) => d.queue === k);
    return `<section class="col"><div class="col-h"><span class="t">${q(k)}</span><span class="mono dim">${list.length}</span></div>
      <div class="col-b">${list.map(card).join("") || '<div class="empty">Nothing here</div>'}</div></section>`;
  }).join("");
  const filed = docs().filter((d) => d.queue === "approved_filed");
  const npr = docs().filter((d) => d.queue === "not_payer_response");
  const mini = (list, empty) => list.map((d) => `<tr class="click" data-doc="${d.doc_id}"><td class="mono">${esc(faxOf(d).fixture)}.pdf</td><td>${esc(d.extraction.fields.payer_name.value || "")}</td><td>${ruleChips(d.rule_hits, ["C-", "Q-"])}</td></tr>`).join("") || `<tr><td class="dim">${empty}</td></tr>`;
  return `<div class="head"><div class="eyebrow">Denials by reason</div><h1>Denial board</h1>
  <p class="lede">Full and partial denials grouped by the reason the payer gave. The reason is matched by fixed keyword rules, not by the model, and every card shows the rule that put it here. Partial denials keep their approved units visible because those are still usable.</p></div>
  <div class="board">${cols}</div>
  <div class="rail" style="margin-top:24px">
    <div class="panel"><div class="panel-h"><h2>Approved, filed</h2><span class="mono dim">${filed.length}</span></div><table><tbody>${mini(filed, "None")}</tbody></table></div>
    <div class="panel"><div class="panel-h"><h2>Not a payer letter</h2><span class="mono dim">${npr.length}</span></div><table><tbody>${mini(npr, "None")}</tbody></table></div>
  </div>`;
}

/* ---------------------------------------------------------------- document view */
function fieldFlags(d, name, fv) {
  const spec = DEMO.field_specs[name];
  const flags = [];
  if (d.extraction.disagreements.includes(name)) flags.push("readers disagree");
  else if (fv.value !== null && fv.confidence < CONF_FLOOR) flags.push("low confidence");
  if (fv.value === null && spec.required_for.includes(d.doc_type)) flags.push("required, missing");
  return flags;
}
function viewDoc(id) {
  const d = S.documents[id];
  if (!d) return `<p class="dim">Document not found.</p>`;
  const fx = faxOf(d).fixture;
  const f = d.extraction.fields;
  const readers = DEMO.readers[fx];
  const rows = Object.keys(DEMO.field_specs).map((name) => {
    const fv = f[name];
    const flags = fieldFlags(d, name, fv);
    const dis = d.extraction.disagreements.includes(name);
    const shown = dis ? `<span class="dim">Haiku:</span> ${esc(readers[name].haiku ?? "none")}<br><span class="dim">Sonnet:</span> ${esc(readers[name].sonnet ?? "none")}` : fv.value === null ? '<span class="dim">not on page</span>' : esc(fv.value);
    return `<div class="kv ${flags.length ? "flag" : ""}"><div class="k">${esc(name)}</div>
      <div class="val">${shown}${flags.map((x) => ` <span class="chip warn">${x}</span>`).join("")}</div>
      <div class="confcell"><span class="conf">${fv.value === null ? "-" : fv.confidence.toFixed(2)}</span><div class="bar"><i class="${fv.confidence < CONF_FLOOR ? "low" : ""}" style="width:${Math.round(fv.confidence * 100)}%"></i></div></div></div>`;
  }).join("");
  const trail = d.rule_hits.map((h) => `<li><span class="id ${/^(G|V|R-NONE|R-MULTI|C-CONFLICT|Q-REVIEW)/.test(h.rule_id) ? "warn" : ""}">${esc(h.rule_id)}</span><span>${esc(h.detail)}</span></li>`).join("");
  const acts = Object.values(S.actions).filter((a) => a.doc_id === id);
  const events = Object.values(S.audit_log).filter((a) => a.doc_id === id || a.fax_id === d.fax_id).sort((a, b) => a.seq - b.seq);
  return `<a class="back" href="#/${d.needs_review ? "review" : "board"}">&larr; Back</a>
  <div class="head" style="margin-top:12px"><div class="eyebrow">Document</div><h1>${esc(pretty(fx))}</h1>
    <div><span class="chip solid">${LABELS.doc[d.doc_type]}</span><span class="chip ${d.needs_review ? "warn" : "ok"}">${q(d.queue)}</span>${d.priority === "high" ? '<span class="chip warn">High priority: appeal window 30 days or fewer</span>' : ""}${d.denial_reason ? `<span class="chip">reason: ${esc(d.denial_reason)}</span>` : ""}</div></div>
  <div class="docgrid">
    <div><div class="fax"><img src="data/img/${esc(fx)}.png" alt="Synthetic fax page for ${esc(pretty(fx))}"></div>
      <p class="note">Synthetic sample fax. <a href="data/pdf/${esc(fx)}.pdf" target="_blank" rel="noopener">Open the original PDF</a>.</p></div>
    <div>
      ${d.needs_review ? reviewBox(d, fx) : ""}
      ${acts.length ? actionPanel(acts) : ""}
      <div class="panel"><div class="panel-h"><div><div class="eyebrow">Extracted fields</div><h2>What was read, and how sure</h2></div></div>${rows}</div>
      <div class="panel"><div class="panel-h"><div><div class="eyebrow">Decision trail</div><h2>Rules that fired, in order</h2></div></div><ul class="trail">${trail}</ul></div>
      <div class="panel"><div class="panel-h"><div><div class="eyebrow">Audit</div><h2>Events for this document</h2></div></div>
        <table><tbody>${events.map((e) => `<tr><td class="mono dim">${time(e.ts)}</td><td class="mono">${esc(e.event)}</td><td class="dim">${esc(e.actor)}</td></tr>`).join("")}</tbody></table></div>
    </div></div>`;
}
function flaggedFields(d) {
  return Object.keys(DEMO.field_specs).filter((n) => fieldFlags(d, n, d.extraction.fields[n]).length && DEMO.truth[faxOf(d).fixture][n] != null);
}
function reviewBox(d, fx) {
  const names = flaggedFields(d);
  return `<div class="panel reviewbox"><div class="panel-h"><div><div class="eyebrow">Review</div><h2>Read the fax image, then confirm</h2></div></div><div class="panel-b">
    <div>${d.review_reasons.map((r) => `<span class="chip warn">${esc(r)}</span>`).join("")}</div>
    ${names.map((n) => `<label for="rv-${n}">${esc(n)}</label><input id="rv-${n}" data-field="${n}" autocomplete="off" spellcheck="false" placeholder="Type exactly what the page says">`).join("")}
    <p class="note">In this static demo, only the value printed on the page is accepted (a reviewer would not type a wrong one on purpose). The API accepts any value that passes the shared validation.</p>
    <div class="err" id="rv-err"></div>
    <div style="margin-top:12px"><button class="btn" id="rv-apply" data-review="${d.doc_id}">Confirm and re-run the rules</button></div></div></div>`;
}
function actionPanel(acts) {
  return `<div class="panel"><div class="panel-h"><div><div class="eyebrow">Proposed outbound action</div><h2>A person approves before anything is sent</h2></div></div><div class="panel-b">
  ${acts.map((a) => `<div class="draft">${esc(a.draft)}<div class="mono dim" style="margin-top:6px">${esc(a.kind)} · status: ${esc(a.status)}${a.approved_by ? " · by " + esc(a.approved_by) : ""}</div></div>
    <div style="display:flex;gap:8px;margin-bottom:8px">
    ${a.status === "pending_approval" ? `<button class="btn" data-act="approve" data-id="${a.action_id}">Approve</button><button class="btn ghost" data-act="reject" data-id="${a.action_id}">Reject</button>` : ""}
    ${a.status === "approved" ? `<button class="btn" data-act="dispatch" data-id="${a.action_id}">Send (simulated)</button>` : ""}
    </div>`).join("")}
  <p class="note">Sending is simulated: the demo has no fax or email sender. The pipeline refuses to dispatch anything that is not approved.</p></div></div>`;
}

/* ---------------------------------------------------------------- audit + rules */
function viewAudit() {
  const rows = Object.values(S.audit_log).sort((a, b) => b.seq - a.seq);
  return `<div class="head"><div class="eyebrow">Append-only</div><h1>Audit trail</h1>
  <p class="lede">What happened, when, who did it, and which rules fired. There are no patient names, member IDs, units, dates or reason text in this log by design: the writer only accepts a short list of keys and short machine tokens, and the table rejects edits and deletes.</p></div>
  <div class="panel"><table><thead><tr><th>#</th><th>Time</th><th>Event</th><th>Actor</th><th>Detail</th></tr></thead><tbody>
  ${rows.map((r) => `<tr><td class="mono dim">${r.seq}</td><td class="mono dim">${time(r.ts)}</td><td class="mono">${esc(r.event)}</td><td class="dim">${esc(r.actor)}</td>
  <td>${Object.entries(r.detail).map(([k, v]) => `<span class="chip" title="${esc(k)}">${esc(k)}: ${esc(Array.isArray(v) ? v.join(", ") : v)}</span>`).join("")}</td></tr>`).join("")}
  </tbody></table></div>`;
}
function viewRules() {
  return `<div class="head"><div class="eyebrow">Deterministic layer</div><h1>Rules</h1>
  <p class="lede">The model reads the page. These rules decide. Prefixes: V validation, C classification, R denial reason, G confidence gate, Q queue, P priority. Every routing decision on the other screens cites the IDs below.</p></div>
  <div class="panel"><table><thead><tr><th>Rule</th><th>What it does</th></tr></thead><tbody>
  ${Object.entries(DEMO.rules).map(([id, text]) => `<tr><td class="mono" style="color:var(--accent)">${esc(id)}</td><td>${esc(text)}</td></tr>`).join("")}
  </tbody></table></div>`;
}

/* ---------------------------------------------------------------- router + events */
function render() {
  const hash = location.hash.replace(/^#\/?/, "") || "incoming";
  const [route, arg] = hash.split("/");
  const views = { incoming: viewIncoming, review: viewReview, board: viewBoard, audit: viewAudit, rules: viewRules, doc: () => viewDoc(arg) };
  nav(route === "doc" ? (S.documents[arg]?.needs_review ? "review" : "board") : route);
  $("#view").innerHTML = (views[route] || viewIncoming)();
  window.scrollTo(0, 0);
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-doc],[data-resend],[data-retry],[data-review],[data-act]");
  if (!t) return;
  if (t.dataset.resend) {
    e.stopPropagation();
    applyPatch(DEMO.patches.duplicate[t.dataset.resend]);
    toast("Same fax again: recognized by its content hash, no second record, no model call. Logged as duplicate_suppressed.");
    return render();
  }
  if (t.dataset.retry) {
    applyPatch(DEMO.patches.retry[t.dataset.retry]);
    toast("Retried from the stored copy. It processed once and the dead letter is resolved.");
    return render();
  }
  if (t.dataset.review) {
    const id = t.dataset.review, rec = DEMO.patches.review[id], bad = [];
    document.querySelectorAll("[data-field]").forEach((i) => {
      const want = String(rec.corrections[i.dataset.field]).replace(/\s+/g, "").toUpperCase();
      if (i.value.replace(/\s+/g, "").toUpperCase() !== want) bad.push(i.dataset.field);
    });
    if (bad.length) { $("#rv-err").textContent = `Check the page again: ${bad.join(", ")} does not match what is printed.`; return; }
    applyPatch(rec.patch);
    const d = S.documents[id];
    toast(`Rules re-ran on the confirmed values: ${LABELS.doc[d.doc_type]}, routed to ${q(d.queue)}.`);
    return void (location.hash = `#/doc/${id}`, render());
  }
  if (t.dataset.act) {
    const p = DEMO.patches.action[t.dataset.id][t.dataset.act];
    applyPatch(p);
    toast({ approve: "Approved by a person. Nothing is sent until you press Send.", reject: "Rejected. It can no longer be sent.", dispatch: "Sent (simulated) and recorded in the audit trail." }[t.dataset.act]);
    return render();
  }
  if (t.dataset.doc) location.hash = `#/doc/${t.dataset.doc}`;
});

$("#reset").addEventListener("click", () => { S = clone(DEMO.base); toast("Demo reset."); render(); });
window.addEventListener("hashchange", render);

fetch("data/demo.json").then((r) => r.json()).then((j) => { DEMO = j; S = clone(j.base); render(); })
  .catch(() => { $("#view").innerHTML = '<p class="dim">Could not load data/demo.json. Serve this folder over HTTP (for example: python -m http.server).</p>'; });
