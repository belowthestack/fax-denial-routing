/* Insurance faxes board. It decides nothing and words nothing.
   Every card, sentence and drafted next step comes from data/demo.json, which scripts/export_demo.py
   produced by playing the Python code over the fixtures. A button here only applies the rows
   that the Python code recorded for that action ("patches") and redraws. */
"use strict";

const COLS = [
  { id: "new", title: "New", help: "Just arrived. Being read." },
  { id: "needs_you", title: "Needs you", help: "The system wasn't sure about something." },
  { id: "ready", title: "Ready to send", help: "Sorted. Next step is drafted." },
  { id: "done", title: "Done", help: "Filed, sent or ignored." },
];
const COL_TITLE = Object.fromEntries(COLS.map((c) => [c.id, c.title]));
const REASON_CLASS = { "Missing paperwork": "doc", "Medical necessity": "nec", "Coverage problem": "elig", "Approval expired": "exp", "Billing code": "code" };
const PK = { faxes: "fax_id", documents: "doc_id", actions: "action_id", dead_letter: "dl_id", audit_log: "seq", outbox: "outbox_id", cards: "card_id", summary: "id" };
const READING_MS = 1400;

let DEMO, S;
let poolIdx = 0, lastFax = null, busy = false, openId = null, showDetails = false, editing = null, seqN = 0, orderN = 0;
let temps = [], fresh = new Set(), edits = {};

const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));
const time = (iso) => (iso || "").slice(11, 19);

function reset() {
  S = clone(DEMO.base);
  poolIdx = 0; busy = false; temps = []; fresh = new Set(); edits = {}; editing = null; showDetails = false;
  seqN = Math.max(0, ...Object.keys(S.audit_log).map(Number));
  orderN = Math.max(0, ...Object.values(S.cards).map((c) => c.order));
  const withDoc = Object.values(S.cards).filter((c) => c.doc_id).sort((a, b) => b.order - a.order);
  lastFax = withDoc.length ? withDoc[0].fax_id : null;
  closePanel();
}

/* Apply the rows the Python code recorded. Audit and outbox ids are renumbered so patches recorded
   from different starting points still stack in the order the viewer triggers them. */
function applyPatch(patch) {
  for (const [table, rows] of Object.entries(patch || {})) {
    for (const row of rows) {
      const r = clone(row);
      if (table === "audit_log") { r.seq = ++seqN; S.audit_log[r.seq] = r; continue; }
      if (table === "outbox") { r.outbox_id = ++seqN; S.outbox[r.outbox_id] = r; continue; }
      if (table === "cards") {
        const old = S.cards[r.card_id];
        if (!old) r.order = ++orderN; else r.order = old.order;
        if (!old || old.column !== r.column) fresh.add(r.card_id);
      }
      S[table][String(r[PK[table]])] = r;
    }
  }
  setTimeout(() => { fresh.clear(); renderBoard(); }, 3200);
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toast.h);
  toast.h = setTimeout(() => t.classList.remove("on"), 5200);
}

/* ---------------------------------------------------------------- board */
const cards = () => Object.values(S.cards);
function sortCol(id, list) {
  if (id === "needs_you" || id === "ready") {
    return list.sort((a, b) => (a.due_soon === b.due_soon ? (a.deadline || "9999").localeCompare(b.deadline || "9999") || a.order - b.order : a.due_soon ? -1 : 1));
  }
  return list.sort((a, b) => b.order - a.order);
}

function cardHtml(c) {
  if (c.temp) {
    return `<article class="card temp" aria-label="A new fax is being read"><div class="who"><span class="patient">New fax</span><span class="payer">Just arrived</span></div>
      <p class="says"><span class="pulse"></span>Reading this fax</p></article>`;
  }
  const tags = [];
  if (c.deadline_text) tags.push(`<span class="chip ${c.due_soon && c.column !== "done" ? "warn" : ""}">${esc(c.deadline_text)}</span>`);
  if (c.reason_label) tags.push(`<span class="chip reason-${REASON_CLASS[c.reason_label] || "doc"}">${esc(c.reason_label)}</span>`);
  const flag = c.column === "needs_you" ? `<p class="flag">${esc(c.why)}</p>` : c.column === "new" ? `<p class="flag fail">${esc(c.why)}</p>` : "";
  const sent = c.action_status === "approved" || c.action_status === "dispatched";
  const stepText = edits[c.action_id] || c.step;
  const step = stepText && c.column !== "new" ? `<p class="step"><span class="lab">${c.column === "needs_you" ? "Next" : sent ? "What was sent" : "Next step"}</span>${esc(stepText)}</p>` : "";
  const state = c.column === "done" ? `<p class="state ${sent ? "sent" : ""}">${esc(c.state_text)}</p>` : "";
  let actions = "";
  if (c.column === "ready" && c.action_id) actions = `<div class="card-actions"><button class="btn sm" data-approve="${c.action_id}">Approve</button><button class="btn ghost sm" data-open="${c.card_id}">Open</button></div>`;
  else if (c.column === "needs_you") actions = `<div class="card-actions"><button class="btn sm" data-open="${c.card_id}">Check it</button></div>`;
  else if (c.column === "new") actions = `<div class="card-actions"><button class="btn sm" data-retry="${c.fax_id}">Retry</button></div>`;
  return `<article class="card ${fresh.has(c.card_id) ? "fresh" : ""}" tabindex="0" data-open="${c.card_id}">
    <div class="who"><span class="patient">${esc(c.patient || (c.column === "new" ? "Unread fax" : "No patient"))}</span><span class="payer">${esc(c.sender)}</span></div>
    <p class="says">${esc(c.headline)}</p>${flag}<div class="tags">${tags.join("")}</div>${step}${state}${actions}</article>`;
}

function renderBoard() {
  if (!$("#board-root")) return;
  const all = cards();
  const cols = COLS.map((col) => {
    let list = sortCol(col.id, all.filter((c) => c.column === col.id));
    const items = (col.id === "new" ? temps.map((t) => ({ temp: true, card_id: t })) : []).concat(list);
    const count = list.length + (col.id === "new" ? temps.length : 0);
    const cls = col.id === "needs_you" ? "needs" : col.id === "ready" ? "ready" : "";
    return `<section class="col ${cls}" aria-label="${col.title}"><div class="col-h"><div class="row"><span class="t">${col.title}</span><span class="n">${count}</span></div><div class="help">${col.help}</div></div>
      <div class="col-b">${items.map(cardHtml).join("") || `<div class="empty">${col.id === "new" ? "Nothing new. Try Simulate a new fax." : "Nothing here."}</div>`}</div></section>`;
  }).join("");
  $("#summary").textContent = S.summary.s.text;
  $("#board").innerHTML = cols;
  const left = DEMO.pool.length - poolIdx;
  $("[data-demo=new]").disabled = busy || left === 0;
  $("[data-demo=outage]").disabled = busy || left === 0;
  $("[data-demo=again]").disabled = busy || !lastFax;
  $("#pool-hint").textContent = left ? `${left} more sample fax${left === 1 ? "" : "es"} to bring in.` : "All sample faxes are on the board. Reset to start over.";
}

function viewBoard() {
  return `<div id="board-root">
  <div class="headline"><div><div class="eyebrow">Today, ${esc(prettyDay(DEMO.meta.demo_today))}</div><h1 id="summary"></h1>
    <p class="sub">Insurance faxes for a therapy clinic. Every patient and insurance company here is made up.</p></div></div>
  <div class="demo" role="group" aria-label="Demo controls"><span class="eyebrow">Demo controls</span>
    <button class="btn" data-demo="new">Simulate a new fax</button>
    <button class="btn ghost" data-demo="again">Send the same fax again</button>
    <button class="btn ghost" data-demo="outage">Simulate an outage</button>
    <button class="btn ghost" data-demo="reset">Reset demo</button>
    <span class="hint" id="pool-hint"></span></div>
  <div class="board" id="board"></div></div>`;
}
function prettyDay(iso) {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

/* ---------------------------------------------------------------- demo controls */
function arrive(mode) {
  if (busy) return;
  const fixture = DEMO.pool[poolIdx];
  if (!fixture) return toast("All sample faxes are on the board. Use Reset demo to start over.");
  poolIdx++; busy = true;
  const tmp = "tmp-" + poolIdx;
  temps.push(tmp);
  renderBoard();
  setTimeout(() => {
    temps = temps.filter((t) => t !== tmp);
    const patch = mode === "outage" ? DEMO.patches.outage[fixture] : DEMO.patches.arrive[fixture];
    applyPatch(patch);
    const card = patch.cards[0];
    busy = false;
    if (mode === "outage") toast("The system couldn't read this fax. It is kept safely and we will try again. Press Retry when it is back.");
    else { lastFax = card.fax_id; toast(`New fax read and sorted. It went to ${COL_TITLE[S.cards[card.card_id].column]}.`); }
    renderBoard();
  }, READING_MS);
}

function retry(faxId) {
  if (busy) return;
  const patch = DEMO.patches.retry[faxId];
  if (!patch) return;
  busy = true;
  const card = S.cards[faxId];
  card.headline = "Reading this fax"; card.why = "Trying again."; renderBoard();
  setTimeout(() => {
    applyPatch(patch);
    busy = false; lastFax = faxId;
    toast(`Read and sorted. It went to ${COL_TITLE[S.cards[faxId].column]}.`);
    renderBoard(); if (openId === faxId) renderPanel();
  }, READING_MS);
}

function sendAgain() {
  const target = (openId && S.cards[openId]?.doc_id && openId) || lastFax;
  const patch = target && DEMO.patches.duplicate[target];
  if (!patch) return toast("Bring in a fax first, then send it again.");
  applyPatch(patch);
  const c = S.cards[target];
  toast(`Already have this fax (${c.patient || c.sender}). Skipped. We spot a repeat before reading it, so nothing is counted twice.`);
  renderBoard(); if (openId) renderPanel();
}

/* ---------------------------------------------------------------- side panel */
function openPanel(id) {
  if (!S.cards[id] || S.cards[id].temp) return;
  openId = id; editing = null; showDetails = false;
  if (S.cards[id].doc_id) lastFax = S.cards[id].fax_id;
  renderPanel();
  $("#scrim").hidden = false; $("#panel").hidden = false; document.body.classList.add("locked");
  $("#panel").scrollTop = 0; $("#panel [data-close]")?.focus();
}
function closePanel() {
  openId = null; editing = null;
  $("#scrim").hidden = true; $("#panel").hidden = true; document.body.classList.remove("locked");
}

function fieldsHtml(c) {
  if (!c.fields.length) return `<p class="dim">No insurance details found on this page.</p>`;
  return `<ul class="fields">${c.fields.map((f) => `<li class="${f.status === "check" ? "check" : ""}"><span class="k">${esc(f.label)}</span>
    <span class="v">${esc(f.value)}${f.note ? `<div class="note">${esc(f.note)}</div>` : ""}</span>
    <span class="chip ${f.status === "check" ? "warn" : "ok"}">${f.status === "check" ? "Check this" : "Sure"}</span></li>`).join("")}</ul>`;
}

function detailsHtml(c) {
  const doc = c.doc_id && S.documents[c.doc_id];
  const hits = doc ? doc.rule_hits : [];
  const events = Object.values(S.audit_log).filter((a) => a.fax_id === c.fax_id || (c.doc_id && a.doc_id === c.doc_id)).sort((a, b) => a.seq - b.seq);
  return `<div class="details"><h3>Rules that fired, in order</h3>
    ${hits.length ? `<ul class="trail">${hits.map((h) => `<li><span class="id">${esc(h.rule_id)}</span><span>${esc(h.detail)}</span></li>`).join("")}</ul>` : `<p class="dim">No rules fired yet because the fax could not be read.</p>`}
    <h3>History for this fax</h3>
    <table class="log"><tbody>${events.map((e) => `<tr><td class="mono dim">${time(e.ts)}</td><td class="mono">${esc(e.event)}</td><td class="dim">${esc(e.actor)}</td>
      <td>${Object.entries(e.detail).map(([k, v]) => `<span class="chip">${esc(k)}: ${esc(Array.isArray(v) ? v.join(", ") : v)}</span> `).join("")}</td></tr>`).join("")}</tbody></table>
    <p class="small-note">The history holds codes and event names only, never a value read from a letter.</p></div>`;
}

function nextSection(c) {
  if (c.column === "needs_you") {
    const rv = DEMO.patches.review[c.doc_id];
    const rows = c.fields.filter((f) => rv && rv.fields.includes(f.name));
    return `<div class="sec confirm"><div class="eyebrow">Check these</div><p>Look at the fax on the left and type what it says.</p>
      ${rows.map((f) => `<label for="in-${f.name}">${esc(f.label)}</label><input id="in-${f.name}" data-field="${f.name}" autocomplete="off" spellcheck="false" placeholder="Type it exactly as printed">`).join("")}
      <div class="err" id="err" role="alert"></div>
      <div class="row-actions"><button class="btn" data-confirm="${c.card_id}">Confirm</button></div>
      <p class="small-note">In this demo only the value printed on the sample fax is accepted.</p></div>`;
  }
  if (c.column === "ready") {
    const text = edits[c.action_id] ?? c.step;
    const body = editing === c.action_id ? `<textarea class="draft" id="edit-box" aria-label="Edit the next step">${esc(text)}</textarea>` : `<div class="draft">${esc(text)}</div>`;
    const btns = editing === c.action_id
      ? `<button class="btn" data-save-edit="${c.action_id}">Save edit</button>`
      : `<button class="btn" data-approve="${c.action_id}">Approve</button><button class="btn ghost" data-edit="${c.action_id}">Edit</button>`;
    return `<div class="sec"><div class="eyebrow">Next step</div>${body}<div class="row-actions">${btns}</div>
      <p class="small-note">Nothing goes out until you approve it. In this demo nothing is really sent, and edits stay in your browser.</p></div>`;
  }
  if (c.column === "new") {
    return `<div class="sec warn"><div class="eyebrow">What happens next</div><p>The fax is kept safely. Nothing was guessed. Press Retry once the reading service is back.</p>
      <div class="row-actions"><button class="btn" data-retry="${c.fax_id}">Retry</button></div></div>`;
  }
  const sent = c.action_status === "approved" || c.action_status === "dispatched";
  return `<div class="sec"><div class="eyebrow">Result</div><p class="big">${esc(c.state_text)}</p>${sent ? `<p class="small-note">What was sent: ${esc(edits[c.action_id] || c.step)}</p>` : ""}</div>`;
}

function renderPanel() {
  const c = S.cards[openId];
  if (!c) return closePanel();
  const keep = $("#panel").scrollTop;
  const unreadable = c.column === "new";
  $("#panel").innerHTML = `<div class="ph"><div><h2 id="panel-title">${esc(c.patient || (c.column === "new" ? "Unread fax" : "No patient on this fax"))}</h2>
      <div class="sub">${esc(c.sender)}</div>
      ${c.patient ? `<div class="demo-name">Patient name added for this demo. The sample fax does not print one.</div>` : ""}</div>
      <button class="btn ghost sm" data-close>Close</button></div>
    <div class="pbody"><div><div class="fax"><img src="data/img/${esc(c.fixture)}.png" alt="Faxed letter from ${esc(c.sender)}"></div>
      <p class="fax-note">Synthetic sample fax. <a href="data/pdf/${esc(c.fixture)}.pdf" target="_blank" rel="noopener">Open the original</a>.</p></div>
    <div>
      <div class="sec"><div class="eyebrow">What the letter says</div><p class="big">${esc(c.headline)}</p>
        ${c.deadline_text || c.reason_label ? `<div class="tags" style="margin-top:8px">${c.deadline_text ? `<span class="chip ${c.due_soon && c.column !== "done" ? "warn" : ""}">${esc(c.deadline_text)}</span>` : ""}${c.reason_label ? `<span class="chip reason-${REASON_CLASS[c.reason_label] || "doc"}">${esc(c.reason_label)}</span>` : ""}</div>` : ""}</div>
      <div class="sec ${c.column === "needs_you" || unreadable ? "warn" : ""}"><div class="eyebrow">Why it is here</div><p>${esc(c.why)}</p></div>
      ${nextSection(c)}
      ${unreadable ? "" : `<div class="sec"><div class="eyebrow">What the system read</div>
        <p class="small-note" style="margin:0 0 8px">Two readers look at every fax. If they don't agree, it says Check this.</p>${fieldsHtml(c)}</div>`}
      <div class="sec"><button class="link" data-details>${showDetails ? "Hide details" : "Details for technical reviewers"}</button>${showDetails ? detailsHtml(c) : ""}</div>
    </div></div>`;
  $("#panel").scrollTop = keep;
}

function confirmReview(cardId) {
  const c = S.cards[cardId], rv = DEMO.patches.review[c.doc_id];
  const norm = (v) => String(v).replace(/\s+/g, "").toUpperCase();
  const bad = [];
  document.querySelectorAll("[data-field]").forEach((i) => { if (norm(i.value) !== norm(rv.answers[i.dataset.field])) bad.push(i.dataset.field); });
  if (bad.length) { $("#err").textContent = "That doesn't match the fax. Look at the page again and retype it."; return; }
  applyPatch(rv.patch);
  toast(`Thanks. It moved to ${COL_TITLE[S.cards[cardId].column]}.`);
  renderBoard(); renderPanel();
}

function approve(actionId) {
  const patch = DEMO.patches.action[actionId];
  if (!patch) return;
  applyPatch(patch);
  const c = Object.values(S.cards).find((x) => x.action_id === actionId);
  if (c) fresh.add(c.card_id);
  toast(`Approved. Sent (demo only). ${c && c.patient ? c.patient + "'s card" : "The card"} moved to Done.`);
  editing = null; renderBoard(); if (openId) renderPanel();
}

/* ---------------------------------------------------------------- how it works */
function viewHow() {
  const m = DEMO.meta, L = DEMO.labels, h = m.reader_stats.haiku, s = m.reader_stats.sonnet;
  const rows = DEMO.recorded.map((r) => {
    const decided = r.needs_review ? `Held for a person. ${esc(r.why_review)}` : esc(L.types[r.type]);
    const went = r.needs_review ? `${esc(L.queues.human_review)}, then ${esc(L.queues[r.after_review])} once confirmed` : esc(L.queues[r.queue]);
    return `<tr><td data-label="Letter">${r.n}. ${esc(r.letter)}</td><td data-label="What the system decided">${decided}</td><td data-label="Where it went">${went}</td>
      <td data-label="Rules"><div class="chips">${r.rules.map((id) => `<span class="chip rule" title="${esc(DEMO.rules[id] || "")}">${esc(id)}</span>`).join("")}</div></td></tr>`;
  }).join("");
  const rules = Object.entries(DEMO.rules).map(([id, t]) => `<tr><td data-label="Rule"><span class="chip rule">${esc(id)}</span></td><td data-label="What it does">${esc(t)}</td></tr>`).join("");
  return `<div class="how"><div class="eyebrow">For the curious and the technical</div><h1>How it works</h1>
  <p class="lede">A billing coordinator gets insurance faxes all day. Each one has to be opened, understood, and turned into a next step. This reads each fax, sorts it, drafts the next step, and asks a person before anything goes out.</p>
  <div class="steps">
    <div class="stepc"><h3>A fax arrives</h3><p>From a folder, an upload, or a fax service.</p></div>
    <div class="stepc"><h3>Two readers look</h3><p>Two AI models read the page separately. If they disagree, a person checks.</p></div>
    <div class="stepc"><h3>Fixed rules decide</h3><p>Plain rules, not the AI, say what kind of letter it is and where it goes.</p></div>
    <div class="stepc"><h3>It lands on the board</h3><p>With the next step already drafted in plain words.</p></div>
    <div class="stepc"><h3>A person approves</h3><p>Nothing goes out until someone says yes.</p></div>
  </div>
  <div class="block"><h2>The flow, in technical terms</h2>
    <div class="flow">
      <div class="fnode">Fax arrives<small>folder, upload or fax service poll</small></div><span class="arrow">&rarr;</span>
      <div class="fnode">Repeat check<small>content hash claimed in the database, before any model call</small></div><span class="arrow">&rarr;</span>
      <div class="fnode">Two model readers<small>Claude Haiku 4.5 and Claude Sonnet 4.6 on AWS Bedrock</small></div><span class="arrow">&rarr;</span>
      <div class="fnode">Shared schema<small>one definition of fields and checks</small></div><span class="arrow">&rarr;</span>
      <div class="fnode">Rules layer<small>classify, reason, confidence gate, route</small></div><span class="arrow">&rarr;</span>
      <div class="fnode">Board<small>draft next step, person approves</small></div></div>
    <div class="flow"><div class="fnode keep">Reading fails<small>fax kept, alert sent, retry later (the dead letter queue)</small></div>
      <div class="fnode side">Any check fails<small>goes to Needs you with the reason</small></div>
      <div class="fnode">Everything writes to an append-only audit log<small>codes and event names, no medical values</small></div></div></div>
  <div class="block"><h2>Two readers have to agree</h2>
    <p class="plain">One AI reading one fax can be confidently wrong, so two separate AI models read it. If they differ on an ID, a number or a date, the fax goes to Needs you.</p>
    <div class="tech"><div class="eyebrow">In technical terms</div>
      <p>The self-reported confidence of a single model did not catch its own mistakes. Across ${h.total} identifier, number and date fields on the 9 insurance letters, Claude Haiku 4.5 read ${h.correct} correctly and Claude Sonnet 4.6 read ${s.correct} correctly. Haiku misread two member IDs while reporting 0.99 and 0.95 confidence. Sonnet misread none; its misses were fields on a blurred scan that it reported as unreadable. The two readers' disagreement caught both Haiku errors. This is a small sample: one try per model, recorded on ${esc(m.captured_at)}.</p>
      <div class="stat"><div><b>${h.correct}/${h.total}</b><span>Haiku 4.5 correct</span></div><div><b>${s.correct}/${s.total}</b><span>Sonnet 4.6 correct</span></div></div></div></div>
  <div class="block"><h2>Rules decide, not the AI</h2>
    <p class="plain">The AI only reads the page. A fixed list of rules decides what kind of letter it is and which pile it goes in. Every decision lists the rule behind it, so anyone can see why.</p>
    <div class="tech"><div class="eyebrow">In technical terms</div><p>A deterministic rules layer applies after extraction: validation, classification from the numbers (not the model's label), denial reason by keyword match, a confidence gate, then queue assignment. The same extraction always gives the same decision. Open Details on any card to see the codes that fired. The full list is at the bottom of this page.</p></div></div>
  <div class="block"><h2>The same fax is never counted twice</h2>
    <p class="plain">Fax machines and fax services sometimes send the same page again. The system recognizes a repeat before it spends anything reading it, and skips it. Try Send the same fax again on the board.</p>
    <div class="tech"><div class="eyebrow">In technical terms</div><p>This is idempotent intake. The fax bytes are hashed and inserted under a UNIQUE constraint before any model call, so a repeat returns the existing record at no cost, and two copies arriving at the same instant still make one record.</p></div></div>
  <div class="block"><h2>Failures are kept, not lost</h2>
    <p class="plain">If the reading service is down, the fax is kept safely, someone is alerted, and the fax is read again later. Nothing is guessed and nothing disappears. Try Simulate an outage on the board.</p>
    <div class="tech"><div class="eyebrow">In technical terms</div><p>A fax that fails extraction (or a bug after it) is written to a dead letter queue with the stage and error class, never its content. An alert fires, and a retry reprocesses the stored copy exactly once. If the alert itself fails, the dead letter row is already saved.</p></div></div>
  <div class="block"><h2>A history nobody can edit</h2>
    <p class="plain">Every step is written to a log that can only be added to. It records what happened and which rule applied, and never the patient's details.</p>
    <div class="tech"><div class="eyebrow">In technical terms</div><p>The audit table rejects updates and deletes with database triggers. The writer only accepts a short list of keys and short machine tokens, so a member ID or a reason sentence is refused. A test scans the log for every value ever read from a letter.</p></div></div>
  <div class="block"><h2>Recorded results on the 10 sample letters</h2>
    <p>These are the real outputs from the recorded pass. ${m.tests_collected ? `The repo has ${m.tests_collected} automated tests that replay them, and they are checked on every change.` : ""} Letters 02, 05 and 07 are held for a person: 02 has units too blurred to read, and on 05 and 07 the two readers disagreed on the member ID.</p>
    <table class="tbl"><thead><tr><th>Letter</th><th>What the system decided</th><th>Where it went</th><th>Rules</th></tr></thead><tbody>${rows}</tbody></table></div>
  <div class="block"><h2>The rules</h2>
    <table class="tbl"><thead><tr><th>Rule</th><th>What it does</th></tr></thead><tbody>${rules}</tbody></table></div>
  <div class="block"><h2>Honest limits</h2>
    <p>This is a pilot on made-up data, not a certified or HIPAA compliant product. It has never touched a real patient. The board here is a recording: its buttons replay what the Python code produced and cannot take an upload. Sending is simulated. A real deployment needs a fax service and an AI provider covered by signed business associate agreements, real sign-in, and testing on far more letters.</p>
    <p style="margin-top:12px"><a href="${esc(m.repo_url)}" target="_blank" rel="noopener">See the code, the tests and the design notes on GitHub</a></p></div></div>`;
}

/* ---------------------------------------------------------------- shell */
function render() {
  const route = location.hash.replace(/^#\/?/, "") || "board";
  $("#tabs").innerHTML = [["board", "Board"], ["how", "How it works"]].map(([id, t]) => `<a href="#/${id}" class="${route === id ? "on" : ""}">${t}</a>`).join("");
  $("#view").innerHTML = route === "how" ? viewHow() : viewBoard();
  if (route !== "how") renderBoard();
  closePanel();
  window.scrollTo(0, 0);
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-demo],[data-approve],[data-open],[data-retry],[data-confirm],[data-edit],[data-save-edit],[data-details],[data-close]");
  if (e.target.id === "scrim") return closePanel();
  if (!t) return;
  const d = t.dataset;
  if (d.demo) {
    if (d.demo === "new") return arrive("new");
    if (d.demo === "outage") return arrive("outage");
    if (d.demo === "again") return sendAgain();
    if (d.demo === "reset") { reset(); renderBoard(); return toast("Demo reset."); }
  }
  if (d.approve) { e.stopPropagation(); return approve(d.approve); }
  if (d.retry) { e.stopPropagation(); return retry(d.retry); }
  if (d.confirm) return confirmReview(d.confirm);
  if (d.edit) { editing = d.edit; renderPanel(); return void $("#edit-box")?.focus(); }
  if (d.saveEdit) { edits[d.saveEdit] = $("#edit-box").value.trim() || edits[d.saveEdit]; editing = null; renderPanel(); renderBoard(); return toast("Edit saved in your browser. Approve when you are ready."); }
  if (d.details !== undefined) { showDetails = !showDetails; return renderPanel(); }
  if (d.close !== undefined) return closePanel();
  if (d.open) openPanel(d.open);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && openId) closePanel();
  if (e.key === "Enter" && e.target.matches?.("article.card[data-open]")) openPanel(e.target.dataset.open);
});
window.addEventListener("hashchange", render);

fetch("data/demo.json").then((r) => r.json()).then((j) => { DEMO = j; reset(); render(); })
  .catch(() => { $("#view").innerHTML = '<p class="dim">Could not load data/demo.json. Serve this folder over HTTP, for example: python -m http.server.</p>'; });
