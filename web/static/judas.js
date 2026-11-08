// Project Judas page. Seller and doc content is untrusted: every dynamic node is built
// with textContent via el(); this file never assigns HTML strings.
"use strict";

const $ = (id) => document.getElementById(id);
const VENDORS = { ramp: "Ramp", brex: "Brex" };
const STATUS_LABEL = {
  documented: "documented",
  documented_needs_clarification: "needs clarification",
  partially_documented: "partially documented",
  not_found_in_reviewed_docs: "not found in reviewed docs",
  docs_unavailable: "docs unavailable",
};
const LEVEL_LABEL = {
  vendor_claim: "vendor claim", official_docs: "official docs", synthetic_demo: "synthetic demo",
  vendor_demo: "vendor demo", verified_integration: "verified integration",
};
const PRIO_LABEL = { must: "Must", nice: "Nice", skip: "Skip" };
const SHORT_LABEL = {
  documented: "documented",
  partially_documented: "partial",
  documented_needs_clarification: "need clarification",
  not_found_in_reviewed_docs: "not found",
  docs_unavailable: "docs unavailable",
};
const state = { evaluationId: null, seen: new Set(), inflight: false, questions: {}, answered: new Set(),
  qlist: [], priorities: {}, profiles: {}, profile: "default" };
const qLabel = (id) => (id === "QX" ? "Your question" : `${id} · ${state.questions[id] || ""}`.replace(/ · $/, ""));
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const ACTION_BUTTONS = ["btn-discover", "btn-evaluate", "btn-contact-ramp", "btn-contact-brex", "btn-demo-ramp", "btn-demo-brex"];

// One request at a time per page: all action buttons are disabled while any of them runs,
// so a double-click can never start two evaluations or race writes to the same one.
function setInflight(on) {
  state.inflight = on;
  for (const id of ACTION_BUTTONS) {
    const b = $(id);
    if (on) { b.dataset.wasDisabled = b.disabled ? "1" : ""; b.disabled = true; }
    else if (b.dataset.wasDisabled !== "1") b.disabled = false;
  }
}

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}

// replaceChildren() turns null into the text "null"; always go through fill().
function fill(node, ...kids) { node.replaceChildren(...kids.flat().filter((k) => k != null)); }

async function api(path, body) {
  const res = await fetch(path, body !== undefined ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : {});
  let data;
  try { data = await res.json(); } catch { data = { error: `Server returned ${res.status}` }; }
  if (!res.ok) { const e = new Error(data.error || `HTTP ${res.status}`); e.hint = data.hint; throw e; }
  return data;
}

function showError(err) {
  const box = $("err-buyer");
  fill(box, el("span", { text: err.message }), err.hint ? el("small", { text: err.hint }) : null);
  box.hidden = false;
}
const clearError = () => { $("err-buyer").hidden = true; };

function busy(btn, on, label) {
  btn.disabled = on; btn.classList.toggle("busy", on);
  if (label) btn.textContent = label;
}

function chain({ a2a, seller, mcp, docs }) {
  const set = (sel, on) => document.querySelector(sel).classList.toggle("lit", !!on);
  set('[data-node="buyer"]', true); set('[data-wire="a2a"]', a2a); set('[data-node="seller"]', seller);
  set('[data-wire="mcp"]', mcp); set('[data-node="docs"]', docs);
}

// ---------- scenario ----------
async function loadScenario() {
  clearError();
  try {
    const s = await api("/api/judas/scenario");
    $("request").value = s.request;
    state.qlist = s.questions;
    state.questions = Object.fromEntries(s.questions.map((q) => [q.id, q.requirement]));
    state.profiles = s.profiles;
    applyProfile("default");
  } catch (e) { showError(e); }
}

// The buyer decides what matters: each requirement is a must-have, a nice-to-have, or skipped.
function applyProfile(key) {
  state.profile = key;
  state.priorities = { ...state.profiles[key].priorities };
  $("suggest-note").hidden = true;
  renderPriorities();
}

// Rule-based: every suggestion cites the buyer's own words, and the buyer can change any of them.
async function suggestFromRequest() {
  clearError();
  try {
    const r = await api("/api/judas/suggest-priorities", { request: $("request").value });
    state.priorities = r.priorities;
    state.profile = "suggested";
    renderPriorities();
    const note = $("suggest-note");
    fill(note, r.reasons.length
      ? [el("b", { text: "Suggested from your words: " }),
        r.reasons.map((x) => `“${x.matched}” → ${x.requirement}: ${PRIO_LABEL[x.priority]}`).join(" · ")]
      : [el("span", { text: "No priority words found in the request; defaults kept." })]);
    note.hidden = false;
  } catch (e) { showError(e); }
}

function renderPriorities() {
  fill($("profiles"), ...Object.entries(state.profiles).map(([k, p]) => el("button", {
    type: "button", class: `chip prof${k === state.profile ? " on" : ""}`, "aria-pressed": k === state.profile ? "true" : "false",
    text: p.label, onclick: () => applyProfile(k) })),
    el("button", { type: "button", class: `chip prof suggest${state.profile === "suggested" ? " on" : ""}`,
      text: "Suggest from my request", onclick: suggestFromRequest }));
  fill($("requirements"), ...state.qlist.map((q) => el("li", { class: `rq p-${state.priorities[q.id]}` },
    el("span", { class: "rq-name", text: q.requirement }),
    el("span", { class: "seg", role: "group", "aria-label": `Priority for ${q.requirement}` },
      ...Object.keys(PRIO_LABEL).map((k) => el("button", {
        type: "button", class: state.priorities[q.id] === k ? "on" : null, "aria-pressed": state.priorities[q.id] === k ? "true" : "false",
        text: PRIO_LABEL[k], onclick: () => { state.priorities[q.id] = k; state.profile = "custom"; renderPriorities(); } }))))));
}

function payload() {
  return { request: $("request").value, custom_question: $("custom-question").value.trim() || null,
    priorities: Object.keys(state.priorities).length ? state.priorities : null };
}

// ---------- discover ----------
async function discover() {
  clearError();
  if (state.inflight) return false;
  setInflight(true);
  const btn = $("btn-discover");
  busy(btn, true, "Discovering");
  try {
    const d = await api("/api/judas/discover", payload());
    state.evaluationId = d.evaluation_id;
    state.questions = Object.fromEntries(d.requirements.questions.map((q) => [q.id, q.requirement]));
    state.answered = new Set();
    for (const v of Object.keys(VENDORS)) { fill($(`thread-${v}`), ); }
    fill($("comparison-body"), el("p", { class: "empty", text: "Click Evaluate both to ask both agents and compare their documentation." }));
    for (const a of d.agents) {
      $(`state-${a.vendor}`).textContent = `discovered · ${(a.badges || []).join(" · ")}`;
      $(`btn-contact-${a.vendor}`).disabled = false;
      $(`btn-demo-${a.vendor}`).disabled = false;
      fill($(`demo-${a.vendor}`), );
    }
    $("agents-note").textContent = `${d.agents.length} vendor agents found in the curated registry · evaluation ${d.evaluation_id}`;
    if (!state.qlist.length) {          // Discover without loading the scenario: show what is being asked
      fill($("requirements"), ...d.requirements.questions.map((q) => el("li", { text: q.requirement })));
    }
    chain({});
    await refreshHistory();
    return true;
  } catch (e) { showError(e); return false; }
  finally { setInflight(false); busy(btn, false, "Discover agents"); }
}

// ---------- contact ----------
async function contact(vendor) {
  clearError();
  if (state.inflight) return;
  if (!state.evaluationId && !(await discover())) return;
  setInflight(true);
  const btn = $(`btn-contact-${vendor}`);
  busy(btn, true, `Contacting ${VENDORS[vendor]}`);
  chain({ a2a: true });
  try {
    const r = await api(`/api/judas/evaluations/${state.evaluationId}/contact`, { vendor });
    renderThread(vendor, r);
    const calls = (r.response && r.response.tool_calls) || [];
    chain({ a2a: true, seller: !r.error, mcp: calls.length > 0, docs: calls.length > 0 });
    $(`state-${vendor}`).textContent = r.error ? "unreachable" : "answered over A2A";
    if (!r.error) state.answered.add(vendor);
    if (state.answered.size === Object.keys(VENDORS).length && !$("comparison-body").querySelector("table")) {
      fill($("comparison-body"), el("p", { class: "empty", text: "Both agents have answered. Click Evaluate both to build the comparison." }));
    }
    renderHistory(r.events || []);
  } catch (e) { showError(e); }
  finally { setInflight(false); busy(btn, false, `Contact ${VENDORS[vendor]}`); }
}

// Per answer: the official source and the operation it came from. The retrieval time and the
// "via our MCP wrapper" note are the same for every answer, so they are stated once per vendor.
function evidenceLine(ev) {
  const parts = [];
  if (ev.source) parts.push(el("a", { href: /^https:\/\//.test(ev.source) ? ev.source : null, target: "_blank",
    rel: "noopener noreferrer", title: ev.retrieved_at ? `retrieved ${ev.retrieved_at}` : null,
    text: ev.source.replace(/^https:\/\//, "") }));
  if (ev.operation || ev.schema_ref) parts.push(el("code", { text: ev.operation || ev.schema_ref }));
  return el("div", { class: "evidence" }, ...parts.flatMap((p, i) => (i ? [" · ", p] : [p])));
}

// One line per separately checked part of the requirement; core parts decide the finding.
function facetList(facets) {
  return el("ul", { class: "facets" }, ...facets.map((f) => el("li", { class: `${f.found ? "yes" : "no"}${f.core ? "" : " extra"}`,
    title: f.summary || "" },
    el("span", { class: "mark", text: f.found ? "✓" : "✗" }), el("span", { text: f.name }),
    el("small", { text: ` · ${KIND[f.kind] || f.kind}${f.core ? "" : ", supporting"}` }))));
}
const KIND = { native_capability: "capability", api_primitive: "API building block", guarantee: "guarantee",
  integration_pattern: "recommended pattern", delivery_semantics: "delivery behavior", scope: "scope",
  deprecation: "deprecation", limitation: "limitation" };

function tally(answers) {
  const n = {};
  for (const a of answers) n[a.finding] = (n[a.finding] || 0) + 1;
  return Object.keys(SHORT_LABEL).filter((k) => n[k]).map((k) => `${n[k]} ${SHORT_LABEL[k]}`).join(", ") || "no answers";
}

function utcTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.toISOString().slice(11, 16)} UTC`;
}

function renderThread(vendor, r) {
  const box = $(`thread-${vendor}`);
  const name = VENDORS[vendor];
  if (r.error) {
    fill(box, el("p", { class: "error", text: `Could not reach ${name}: ${r.error}` }),
      r.hint ? el("p", { class: "muted", text: r.hint }) : null);
    return;
  }
  const resp = r.response || {};
  const answers = resp.answers || [];
  const lookups = (resp.tool_calls || []).length;
  const firstEv = answers.flatMap((a) => a.evidence || [])[0];
  const hop = (route, what) => el("li", {}, el("span", { class: "route", text: route }), el("span", { class: "what", text: what }));
  fill(box,
    el("ol", { class: "exchange", "aria-label": `Exchange with the ${name} agent` },
      hop(`Judas → ${name} agent`, `A2A request: ${answers.length} questions`),
      hop(`${name} agent → ${name}'s docs`, `${lookups} MCP lookup${lookups === 1 ? "" : "s"}`),
      hop(`${name} agent → Judas`, `A2A reply: ${tally(answers)}`)),
    resp.pitch ? el("p", { class: "pitch", text: resp.pitch.text }) : null,
    el("ol", { class: "answers" }, ...answers.map((a) => el("li", { class: `ans ${a.finding}` },
      el("div", { class: "ans-top" }, el("span", { class: `st s-${a.finding}`, text: STATUS_LABEL[a.finding] || a.finding }),
        el("b", { text: qLabel(a.question_id) })),
      (a.facets && a.facets.length) ? facetList(a.facets) : el("p", { text: a.answer }),
      ...(a.evidence || []).slice(0, 2).map(evidenceLine)))),
    firstEv ? el("p", { class: "lookup-note", text: `Every lookup went through ${firstEv.via || "our docs MCP server"}`
      + (firstEv.retrieved_at ? `. Docs retrieved ${utcTime(firstEv.retrieved_at)}.` : ".") }) : null,
    el("ul", { class: "limits" }, ...(resp.limitations || []).map((l) => el("li", { text: l }))),
  );
}

// ---------- demonstration ----------
// The buyer asks the seller over A2A to DO something, not just describe it. The seller runs real
// code over MCP against our synthetic sandbox; the panel says so in plain words.
function renderDemo(vendor, d, err) {
  const box = $(`demo-${vendor}`);
  const name = VENDORS[vendor];
  if (err) { fill(box, el("p", { class: "error", text: `Demo did not run: ${err}` })); return; }
  if (!d) { fill(box, ); return; }
  const lists = d.tool_calls.filter((c) => c.tool === "list_card_transactions").length;
  fill(box,
    el("div", { class: "demo-head" },
      el("b", { text: "Demonstration" }),
      el("span", { class: "lv lv-synthetic_demo", text: "synthetic data" }),
      el("span", { class: "demo-where", text: d.endpoint.vendor_hosted ? `${name}-hosted` : `our sandbox MCP, not ${name}'s servers` })),
    el("ol", { class: "exchange" },
      el("li", {}, el("span", { class: "route", text: `Judas → ${name} agent` }), el("span", { class: "what", text: "A2A: demonstrate card-transaction sync" })),
      el("li", {}, el("span", { class: "route", text: `${name} agent → sandbox` }), el("span", { class: "what", text: `${d.tool_calls.length} MCP calls (list_card_transactions × ${lists})` })),
      el("li", {}, el("span", { class: "route", text: `${name} agent → Judas` }), el("span", { class: "what", text: `${d.unique_records} unique records, ${d.duplicates_removed} duplicates removed` }))),
    el("ul", { class: "observed" }, ...d.observations.map((o) => el("li", {}, el("b", { text: o.requirements.join(", ") }), " ", o.text))),
    el("details", { class: "raw" }, el("summary", { text: `Sample record in ${name}'s documented shape` }),
      el("pre", { text: JSON.stringify(d.sample_record, null, 1) }),
      el("p", { class: "muted", text: d.disclaimer || "" })));
}

async function requestDemo(vendor) {
  clearError();
  if (state.inflight) return;
  if (!state.evaluationId && !(await discover())) return;
  setInflight(true);
  const btn = $(`btn-demo-${vendor}`);
  busy(btn, true, "Running demo");
  try {
    const r = await api(`/api/judas/evaluations/${state.evaluationId}/demo`, { vendor });
    renderDemo(vendor, r.demo, r.error ? `${r.error}${r.hint ? " " + r.hint : ""}` : null);
    renderHistory(r.events || []);
  } catch (e) { showError(e); }
  finally { setInflight(false); busy(btn, false, "Request demo"); }
}

// ---------- evaluate ----------
async function evaluateBoth() {
  clearError();
  if (state.inflight) return;
  const btn = $("btn-evaluate");
  busy(btn, true, "Evaluating");                      // disabled before anything is awaited
  if (!state.evaluationId && !(await discover())) { busy(btn, false, "Evaluate both"); return; }
  setInflight(true);
  chain({ a2a: true });
  try {
    const ev = await api(`/api/judas/evaluations/${state.evaluationId}/evaluate`,
      { priorities: Object.keys(state.priorities).length ? state.priorities : undefined });
    for (const v of Object.keys(VENDORS)) {
      renderDemo(v, (ev.demos || {})[v], (ev.demos || {})[v] === null ? "the sandbox did not answer (./dev.sh starts it)" : null);
      renderThread(v, { response: ev.responses[v], error: ev.errors[v],
        hint: ev.errors[v] ? "The seller agents may not be running. Start them with ./dev.sh and try again." : null });
      $(`state-${v}`).textContent = ev.errors[v] ? "unreachable" : "answered over A2A";
    }
    renderComparison(ev.comparison);
    const ok = Object.keys(VENDORS).some((v) => ev.responses[v]);
    chain({ a2a: true, seller: ok, mcp: ok, docs: ok });
    // The verdict is the result of this click: bring it into view instead of leaving it below the threads.
    // Background tabs skip smooth scrolling, so jump if the smooth scroll has not arrived after a second.
    const target = $("comparison");
    target.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
    setTimeout(() => { if (Math.abs(target.getBoundingClientRect().top) > 40) target.scrollIntoView({ block: "start" }); }, 1000);
    await refreshHistory();
  } catch (e) { showError(e); }
  finally { setInflight(false); busy(btn, false, "Evaluate both"); }
}

// A cell says three separate things: what was found, how strong the evidence is, and what was observed.
function cell(c, vendor, qid) {
  const core = (c.facets || []).filter((f) => f.core);
  const open = core.filter((f) => !f.found);
  const fu = c.follow_up;
  return el("td", { class: `cell ${c.status}` },
    el("div", { class: "cell-top" },
      el("span", { class: `st s-${c.status}`, text: STATUS_LABEL[c.status] || c.status }),
      ...(c.evidence_levels || []).map((l) => el("span", { class: `lv lv-${l}`, text: LEVEL_LABEL[l] || l }))),
    core.length ? el("ul", { class: "facets tight" }, ...core.map((f) => el("li", { class: f.found ? "yes" : "no" },
      el("span", { class: "mark", text: f.found ? "✓" : "✗" }), el("span", { text: f.name })))) :
      el("p", { text: c.summary || "" }),
    ...(c.observed || []).map((o) => el("p", { class: "obs", text: `Observed in synthetic demo: ${o}` })),
    fu ? followUpNote(fu) : null,
    open.length && qid !== "QX" && c.finding !== "docs_unavailable" ? el("button", {
      type: "button", class: "challenge", text: fu ? `Challenge ${VENDORS[vendor]} again` : `Challenge ${VENDORS[vendor]}`,
      title: `Ask the ${VENDORS[vendor]} agent for evidence of: ${open.map((f) => f.name).join("; ")}`,
      onclick: () => challenge(vendor, qid) }) : null,
    (c.facets || []).some((f) => f.found) ? el("details", { class: "ev-details" }, el("summary", { text: "Evidence" }),
      ...(c.facets || []).filter((f) => f.found).map((f) => el("div", { class: "evidence" },
        el("b", { text: f.name }), " · ", f.source ? el("a", { href: /^https:\/\//.test(f.source) ? f.source : null,
          target: "_blank", rel: "noopener noreferrer", text: f.source.replace(/^https:\/\//, "") }) : null,
        f.ref ? [" · ", el("code", { text: f.ref })] : null, el("div", { class: "q", text: f.summary || "" })))) : null);
}

// ---------- challenge: one focused follow-up ----------
function followUpNote(fu) {
  return el("div", { class: "followup" },
    el("p", {}, el("b", { text: "Follow-up: " }),
      `searched ${fu.searched} more place${fu.searched === 1 ? "" : "s"} in the official docs. `,
      fu.resolved.length ? `Resolved: ${fu.resolved.join("; ")}. ` : "",
      fu.still_missing.length ? `Still not found: ${fu.still_missing.join("; ")}.` : ""),
    fu.candidates.length ? el("details", { class: "ev-details" },
      el("summary", { text: `${fu.candidates.length} possible lead${fu.candidates.length === 1 ? "" : "s"} (not counted)` }),
      ...fu.candidates.map((k) => el("div", { class: "evidence" }, el("code", { text: k.where || "" }), " · ",
        el("span", { text: (k.source || "").replace(/^https:\/\//, "") }), el("div", { class: "q", text: k.snippet || "" })))) : null);
}

async function challenge(vendor, qid) {
  clearError();
  if (state.inflight || !state.evaluationId) return;
  setInflight(true);
  try {
    const r = await api(`/api/judas/evaluations/${state.evaluationId}/challenge`, { vendor, question_id: qid });
    if (r.error) showError({ message: r.error });
    renderComparison(r.evaluation.comparison);
    const row = [...document.querySelectorAll("#comparison-body tbody tr")].find((tr) => tr.dataset.q === qid);
    if (row) row.scrollIntoView({ block: "center" });
    await refreshHistory();
  } catch (e) { showError(e); }
  finally { setInflight(false); }
}

// Why: which requirements mattered to this buyer, what is still open, and what evidence would change it.
function reasoning(c) {
  const x = c.explanation;
  if (!x) return null;
  return el("div", { class: "sb-why" },
    el("p", {}, el("b", { text: "Must-haves for this buyer: " }), x.mattered.map(qLabel).join(", ") || "none"),
    x.unresolved.length ? el("p", {}, el("b", { text: "Still open: " }),
      x.unresolved.map((u) => `${u.question_id} (${(u.missing || []).join("; ") || "evidence missing"})`).join(" · ")) : null,
    x.could_change.length ? el("p", {}, el("b", { text: "What would change this: " }), x.could_change[0]) : null);
}

// The scoreboard: verdict, one square per requirement for each vendor, and the caveat right beside it.
function scoreboard(c, std, deciding) {
  const verdictText = {
    no_clear_difference_in_docs: "No clear difference in the reviewed documentation",
    insufficient_evidence: "Insufficient evidence to compare",
    additional_testing_required: "Additional testing required",
  }[c.verdict] || (c.verdict.startsWith("docs_favor:") ? `Reviewed docs favor ${VENDORS[c.verdict.split(":")[1]]}` : c.verdict);
  const cols = `repeat(${std.length}, var(--sq))`;
  const sq = (r, v) => el("li", {
    class: `sq q-${r[v].status}${r.must_have ? " must" : ""}${deciding.has(r.question_id) ? " deciding" : ""}`,
    title: `${qLabel(r.question_id)}: ${STATUS_LABEL[r[v].status] || r[v].status}`,
    "aria-label": `${r.question_id} ${STATUS_LABEL[r[v].status] || r[v].status}`,
  });
  return el("div", { class: "scoreboard", role: "group", "aria-label": "Verdict" },
    el("div", { class: "sb-verdict" },
      el("p", { class: "sb-kicker", text: "Verdict" }),
      el("p", { class: "sb-headline", text: verdictText }),
      el("p", { class: "sb-reason", text: c.verdict_reason || "" }),
      reasoning(c)),
    el("div", { class: "sb-coverage" },
      el("div", { class: "sb-row sb-head" }, el("span", {}),
        el("ol", { class: "strip", style: `grid-template-columns:${cols}` },
          ...std.map((r) => el("li", { class: r.must_have ? "must" : "", text: r.question_id }))), el("span", {})),
      ...Object.keys(VENDORS).map((v) => el("div", { class: "sb-row" },
        el("span", { class: "sb-name", text: VENDORS[v] }),
        el("ol", { class: "strip", style: `grid-template-columns:${cols}` }, ...std.map((r) => sq(r, v))),
        el("span", { class: "sb-count", text: c.coverage_detail
          ? `${c.coverage_detail[v].documented} documented · ${c.coverage_detail[v].partial} partial`
          : `${c.coverage[v]} documented` }))),
      el("p", { class: "sb-key" },
        el("span", { class: "k q-documented" }), "documented ",
        el("span", { class: "k q-partially_documented" }), "partial ",
        el("span", { class: "k q-not_found_in_reviewed_docs" }), "not found in reviewed docs ",
        el("span", { class: "k must-dot" }), "must-have")),
    el("p", { class: "sb-caveat", text: c.caveat }));
}

function renderComparison(c) {
  if (!c) return;
  const std = c.rows.filter((r) => r.question_id !== "QX");
  const deciding = new Set(c.deciding || []);
  const head = (v) => el("th", {}, VENDORS[v], el("span", { class: "cov", text: ` ${c.coverage[v]}` }));
  fill($("comparison-body"),
    scoreboard(c, std, deciding),
    el("table", { class: "ctable" },
      el("thead", {}, el("tr", {}, el("th", { text: "Requirement" }), head("ramp"), head("brex"),
        el("th", { text: "Buyer note" }))),
      el("tbody", {}, ...c.rows.map((r) => el("tr", { class: deciding.has(r.question_id) ? "deciding" : null, "data-q": r.question_id },
        el("th", { scope: "row" }, el("span", { text: r.question_id === "QX" ? "Your question" : `${r.question_id} · ${r.requirement}` }),
          r.must_have ? el("span", { class: "tag must", text: "must-have" }) : el("span", { class: "tag nice", text: "nice-to-have" }),
          deciding.has(r.question_id) ? el("span", { class: "tag decides", text: "decides the verdict" }) : null),
        cell(r.ramp, "ramp", r.question_id), cell(r.brex, "brex", r.question_id),
        el("td", { class: "note", text: r.buyer_note || "" }))))),
    c.clarifications && c.clarifications.length ? el("div", { class: "clar" },
      el("h3", { text: "Questions to clarify with the vendors" }),
      el("ul", {}, ...c.clarifications.map((x) => el("li", { text: x.text })))) : null,
  );
}

// ---------- history ----------
function renderHistory(events) {
  $("history-count").textContent = events.length;
  fill($("timeline"), ...events.slice().reverse().map((e) => {
    const fresh = !state.seen.has(e.event_id); state.seen.add(e.event_id);
    return el("li", { class: fresh ? "fresh" : "" },
      el("span", { class: "ev", text: e.event_type }),
      el("span", { class: "ts", text: e.timestamp.replace("T", " ").replace("+00:00", "Z") }),
      e.agent_id ? el("span", { class: "ag", text: `@${e.agent_id}` }) : null,
      el("pre", { text: JSON.stringify(e.payload, null, 1) }));
  }));
}

async function refreshHistory() {
  if (!state.evaluationId) return;
  try { renderHistory((await api(`/api/judas/evaluations/${state.evaluationId}/events`)).events); } catch { /* non-fatal */ }
}

// ---------- wire up ----------
$("btn-scenario").addEventListener("click", loadScenario);
$("btn-discover").addEventListener("click", discover);
$("btn-evaluate").addEventListener("click", evaluateBoth);
$("btn-contact-ramp").addEventListener("click", () => contact("ramp"));
$("btn-contact-brex").addEventListener("click", () => contact("brex"));
$("btn-demo-ramp").addEventListener("click", () => requestDemo("ramp"));
$("btn-demo-brex").addEventListener("click", () => requestDemo("brex"));
$("btn-reset").addEventListener("click", async () => {
  if (!confirm("Archive all Judas evaluations? They are moved to data/archive, not deleted.")) return;
  try { await api("/api/judas/reset", {}); location.reload(); } catch (e) { showError(e); }
});
