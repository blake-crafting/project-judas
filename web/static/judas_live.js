// Project Judas, live real-vendor scenario. Vendor data is untrusted: every node is built with textContent.
"use strict";

const $ = (id) => document.getElementById(id);
const LEVEL = { verified: "verified", executed: "executed, not verified", tool_available: "tool available",
  blocked: "blocked", failed: "failed", unknown: "not established", none: "none", answered: "answered",
  not_run: "not run", pass: "pass", fail: "fail", error: "error", skipped: "skipped" };
const COMPAT = { both_compatible: "Both passed every buyer-environment test", not_run: "Not run",
  compat_incomplete: "Incomplete" };
const VERDICT = { both_proven: "Both vendors proved every tested requirement",
  insufficient_evidence: "Insufficient live evidence", additional_testing_required: "Additional testing required" };
let meta = null;

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
function fill(node, ...kids) { node.replaceChildren(...kids.flat().filter((k) => k != null)); }
const badge = (lvl) => el("span", { class: `lvl lvl-${lvl}`, text: LEVEL[lvl] || lvl });
const link = (href, text) => el("a", { href: /^https:\/\//.test(href || "") ? href : null, target: "_blank", rel: "noopener noreferrer", text });

async function api(path, body) {
  const res = await fetch(path, body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) } : {});
  const data = await res.json().catch(() => ({ error: `Server returned ${res.status}` }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function vendorCard(v, ev) {
  const res = ev && ev.results[v.id];
  const err = ev && ev.errors[v.id];
  const rb = ev && ev.readback[v.id];
  const na = ev && ev.native_agent[v.id];
  const host = new URL(v.mcp).host;
  const head = el("header", {}, el("b", { text: v.name }),
    el("span", { class: "vstate", text: v.configured ? "connected to our test workspace" : "not connected" }));
  const iface = el("ul", { class: "iface" },
    el("li", {}, el("b", { text: "Seller agent: " }), `our A2A adapter (not operated by ${v.name})`),
    el("li", {}, el("b", { text: "Vendor interface: " }), `${v.name}'s official MCP server (${host})`),
    el("li", {}, el("b", { text: "Integration test: " }), "our e-commerce app in a Crafting sandbox"));
  if (!ev) return el("article", { class: "vendor" }, head, iface);
  const flow = el("ol", { class: "exchange live-flow" });
  const hop = (route, what, cls) => flow.append(el("li", { class: cls || null }, el("span", { class: "route", text: route }),
    el("span", { class: "what", text: what })));
  hop(`Stage 1 · Judas → ${v.name} adapter`, "A2A: demonstrate incident tracking in our workspace");
  if (res) {
    hop(`Stage 2 · Adapter → ${host}`, `${res.steps.length} official MCP calls: ${res.steps.map((s) => s.tool).join(", ")}`, "mcp");
    hop(`Stage 2 · ${v.name} → Judas`, `created ${Object.keys(res.created.item_ids || {}).length} incidents · ids ${Object.values(res.created.item_ids || {}).join(", ")}`);
  } else {
    hop(`Stage 2 · Adapter → ${host}`, `did not complete: ${err || "no reply"}`, "bad");
  }
  if (rb && rb.checks && rb.checks.length) {
    const ok = rb.checks.filter((c) => c.pass).length;
    hop(`Stage 4 · Judas → ${rb.interface}`, `independent check of stage 2: ${ok}/${rb.checks.length} values match`, ok === rb.checks.length ? "good" : "bad");
  }
  const cr = (ev.crafting || {})[v.id];
  if (cr && cr.status === "ran") {
    const r = cr.result || {}; const tests = r.tests || []; const pass = tests.filter((t) => t.status === "pass").length;
    hop(`Stage 3 · Crafting sandbox ${(r.sandbox || {}).name || ""}`, `our app raised real incidents: ${pass}/${tests.length} tests passed`, pass === tests.length ? "good" : "bad");
    const jr = cr.judas_readback || {};
    hop(`Stage 4 · Judas → ${jr.interface || "vendor API"}`, `independent check of stage 3: ${jr.read || 0}/${jr.reported || 0} records found${jr.status_ok ? ", status Resolved" : ""}`,
      jr.found && jr.status_ok ? "good" : "bad");
  } else if (cr) {
    hop("Stage 3 · Crafting sandbox", cr.status === "not_run" ? `not run: ${cr.reason}` : `error: ${cr.error}`, "bad");
  }
  const parts = [head, iface, flow];
  if (res && res.created.container_url) parts.push(el("p", { class: "open" }, link(res.created.container_url, `Open this run in ${v.name}`),
    el("small", { text: ` · ${res.created.group_name || res.created.table_name || ""}` })));
  if (cr && cr.status === "ran") {
    const r = cr.result || {}; const sb = r.sandbox || {};
    parts.push(el("div", { class: "crafting" },
      el("b", { text: "Crafting integration test" }), " ",
      sb.url ? link(sb.url, `sandbox ${sb.name || sb.id}`) : el("span", { text: `sandbox ${sb.name || sb.id || "?"}` }),
      el("small", { text: ` · ${sb.org || ""} ${sb.template ? "· " + sb.template : ""}` }),
      el("ul", { class: "facets tight" }, ...[...(r.tests || []), ...(r.extra_tests || [])].map((t) => el("li", { class: t.status === "pass" ? "yes" : "no" },
        el("span", { class: "mark", text: t.status === "pass" ? "✓" : "✗" }), el("span", { text: `${t.id}: ${t.detail || t.status}` })))),
      el("p", { class: "obs", text: `Resources: ${(r.resources || []).map((x) => x.id).join(", ") || "none reported"}` }),
      (r.errors || []).length ? el("p", { class: "error", text: r.errors.join(" · ") }) : null,
      r.log_excerpt ? el("details", { class: "ev-details" }, el("summary", { text: "Sandbox log excerpt" }), el("pre", { class: "logx", text: r.log_excerpt })) : null));
  }
  if (rb && rb.checks && rb.checks.length) parts.push(el("details", { class: "ev-details" }, el("summary", { text: "Readback checks" }),
    el("ul", { class: "facets tight" }, ...rb.checks.map((c) => el("li", { class: c.pass ? "yes" : "no" },
      el("span", { class: "mark", text: c.pass ? "✓" : "✗" }),
      el("span", { text: `${c.item} · ${c.field}: expected ${JSON.stringify(c.expected)}, got ${JSON.stringify(c.actual)}` }))))));
  return el("article", { class: "vendor" }, ...parts);
}

function renderVendors(ev) { fill($("vendors"), ...meta.vendors.map((v) => vendorCard(v, ev))); }

function renderVerdict(ev) {
  const vd = ev.verdict;
  const ids = meta.vendors.map((v) => v.id);
  const nameOf = (code) => meta.vendors.find((v) => v.id === code.split(":")[1]).name;
  const head = vd.code.startsWith("live_favor:") ? `Live tests favor ${nameOf(vd.code)}` : (VERDICT[vd.code] || vd.code);
  const chead = (vd.compat_code || "not_run").startsWith("compat_favor:") ? `${nameOf(vd.compat_code)} fits our environment better`
    : (COMPAT[vd.compat_code] || vd.compat_code || "Not run");
  const rows = meta.tests.map((t) => el("tr", {}, el("th", { scope: "row" }, el("span", { text: `${t.id} · ${t.requirement}` }),
    el("small", { class: "test-desc", text: `${t.test}. Pass if: ${t.success}.` })),
    ...ids.map((v) => { const e = ev.evidence[v][t.id]; return el("td", {}, badge(e.level), el("p", { class: "obs", text: e.detail })); })));
  const section = (title, note) => el("tr", { class: "section-row" }, el("th", { colspan: String(ids.length + 1) },
    el("span", { text: title }), el("small", { class: "test-desc", text: note })));
  const crows = (meta.crafting_tests || []).map((t) => el("tr", {}, el("th", { scope: "row" },
    el("span", { text: `${t.id} · ${t.requirement}` }), el("small", { class: "test-desc", text: t.test })),
    ...ids.map((v) => { const e = (ev.crafting_evidence || {})[v]?.[t.id] || { level: "not_run", detail: "" };
      return el("td", {}, badge(e.level), el("p", { class: "obs", text: e.detail })); })));
  fill($("verdict"), verdictSummary(ev),
    el("div", { class: "scoreboard" }, el("div", { class: "sb-verdict" },
      el("p", { class: "sb-kicker", text: "Vendor capability (official MCP, independently verified)" }),
      el("p", { class: "sb-headline", text: head }), el("p", { class: "sb-reason", text: vd.reason }),
      el("p", { class: "sb-kicker sb-second", text: "Buyer-environment compatibility (our app in a Crafting sandbox)" }),
      el("p", { class: "sb-headline small", text: chead }), el("p", { class: "sb-reason", text: vd.compat_reason || "" })),
      el("p", { class: "sb-caveat", text: vd.caveat })),
    el("table", { class: "ctable" }, el("thead", {}, el("tr", {}, el("th", { text: "Requirement and test" }),
      ...meta.vendors.map((v) => el("th", { text: v.name })))), el("tbody", {},
      section("Vendor capability", "Stage 2 operations through the official MCP server, checked by Judas in stage 4."), ...rows,
      section("Buyer-environment compatibility", "Stage 3: our e-commerce app in a Crafting sandbox, re-checked by Judas in stage 4."), ...crows)),
    el("div", { class: "clar" }, el("h3", { text: "Still untested" }), el("ul", {}, ...vd.untested.map((u) => el("li", { text: u })))));
}

function renderHistory(evs) {
  $("history-count").textContent = evs.length;
  fill($("timeline"), ...evs.slice().reverse().map((e) => el("li", {},
    el("span", { class: "ev", text: e.event_type }), el("span", { class: "ts", text: e.timestamp.replace("T", " ").replace("+00:00", "Z") }),
    e.agent_id ? el("span", { class: "ag", text: `@${e.agent_id}` }) : null, el("pre", { text: JSON.stringify(e.payload, null, 1) }))));
}

// ---------------------------------------------------------------------------------------------------------
// Command-center presentation. Every stage, theme and check below is derived from REAL run data: the events
// the backend records while it works (polled from /api/events) or the saved evaluation. No timers decide
// which system is executing; a check is only shown as passed after the backend reports it.
// ---------------------------------------------------------------------------------------------------------
const STEPS = ["buyer", "a2a", "mcp", "verify", "crafting", "verdict"];
const STAGE_COPY = {
  idle: ["JUDAS", "Ready", "One click runs both vendors live: A2A → official MCP → Crafting sandboxes → independent verification."],
  buyer: ["JUDAS", "Buyer agent · finding seller adapters", "Judas loads the buyer's request and finds the two seller adapters in its registry."],
  vendors: ["JUDAS", "A2A → official MCP · vendors doing the work", "Our seller adapters received A2A requests and are executing through each vendor's official MCP server."],
  verify: ["JUDAS", "Judas · re-reading the vendors' work", "Judas re-reads what each vendor created through a separate public API (Airtable REST, monday.com GraphQL)."],
  crafting: ["CRAFTING", "Crafting — testing in the buyer's environment", "Two isolated application environments are testing real integrations against identical buyer requirements."],
  verdict: ["JUDAS", "Judas — independent verification and verdict", "Judas re-read every vendor record itself and compared the persisted state with the buyer's requirements."],
  blocked: ["JUDAS", "Run did not complete", "The run stopped before a verdict. Nothing below is shown as passed unless the backend reported it."],
};
const CHECKS = [
  ["created", "Incident created"], ["persisted", "Vendor record persisted"], ["synced", "Status synchronized"],
  ["dupes", "Duplicate events handled"], ["recovery", "Injected-failure recovery"], ["readback", "Judas independent readback"],
];
const vendorIds = () => (meta ? meta.vendors.map((v) => v.id) : ["airtable", "monday"]);
const vname = (id) => (meta && meta.vendors.find((v) => v.id === id) || {}).name || id;
const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Pure: real events of ONE evaluation -> presentation state. Mirrors judas/live_flow.run():
 *  discovery -> A2A/MCP (vendors in parallel) -> Judas readback -> Crafting (sandboxes in parallel, each
 *  followed by Judas's readback of its records) -> comparison. */
function deriveState(evts) {
  const of = (t, v) => evts.filter((e) => e.event_type === t && (!v || (e.payload || {}).vendor === v));
  const ids = vendorIds();
  const vend = {};
  for (const v of ids) {
    const res = of("live_operation_result", v)[0], fail = of("live_operation_failed", v)[0];
    const ver = of("live_verification", v)[0], cs = of("crafting_test_started", v)[0], cr = of("crafting_test_result", v)[0];
    vend[v] = { requested: of("live_operation_requested", v).length > 0, result: res && res.payload, failed: fail && fail.payload,
      verified: ver && ver.payload, craftStarted: !!cs, craft: cr && cr.payload };
  }
  const all = (k) => ids.every((v) => k(vend[v]));
  const any = (k) => ids.some((v) => k(vend[v]));
  const steps = Object.fromEntries(STEPS.map((s) => [s, "todo"]));
  const started = evts.some((e) => e.event_type === "evaluation_started");
  const done = evts.some((e) => e.event_type === "comparison_completed");
  if (started) steps.buyer = "active";
  if (evts.some((e) => e.event_type === "registry_searched")) steps.buyer = "done";
  const opsDone = all((x) => x.result || x.failed);
  if (any((x) => x.requested)) { steps.a2a = opsDone ? "done" : "active"; steps.mcp = opsDone ? (any((x) => x.failed) ? "blocked" : "done") : "active"; }
  if (opsDone) steps.verify = all((x) => x.verified || x.failed) ? "done" : "active";
  const craftDone = all((x) => x.craft);
  if (any((x) => x.craftStarted)) steps.crafting = craftDone ? (any((x) => x.craft && x.craft.status === "error") ? "blocked" : "done") : "active";
  if (done) steps.verdict = "done";
  let stage = "idle";
  if (started) stage = "buyer";
  if (steps.a2a === "active") stage = "vendors";
  if (steps.verify === "active" || (steps.verify === "done" && steps.crafting === "todo" && !done)) stage = "verify";
  if (steps.crafting === "active") stage = "crafting";                    // Crafting theme ONLY while sandboxes run
  if (steps.crafting === "done" || steps.crafting === "blocked") stage = "verify";
  if (done) stage = "verdict";
  return { stage, steps, vend, done };
}

function applyStage(st, prov) {
  const body = document.body;
  body.dataset.stage = st.stage;
  body.dataset.theme = st.stage === "crafting" ? "crafting" : "judas";
  const [sys, title, desc] = STAGE_COPY[st.stage] || STAGE_COPY.idle;
  $("cc-system").textContent = sys; $("cc-title").textContent = title; $("cc-desc").textContent = desc;
  for (const li of document.querySelectorAll("#cc-timeline li")) {
    const s = st.steps[li.dataset.step] || "todo";
    li.dataset.state = s;
    li.setAttribute("aria-current", s === "active" ? "step" : "false");
  }
  // existing signal chain, lit only for stages that really happened
  const lit = { buyer: st.steps.buyer !== "todo", a2a: st.steps.a2a !== "todo", seller: st.steps.a2a !== "todo",
    mcp: st.steps.mcp !== "todo", docs: st.steps.mcp === "done" };
  for (const n of document.querySelectorAll("#chain li")) n.classList.toggle("lit", !!lit[n.dataset.node || n.dataset.wire]);
  if (prov != null) $("cc-prov").textContent = prov;
}

/** Per-vendor check states from the full saved Crafting result (preferred) or the live event (partial). */
function craftChecks(v, ev, live) {
  const out = Object.fromEntries(CHECKS.map(([k]) => [k, { state: "todo", note: "" }]));
  const cr = ev && (ev.crafting || {})[v];
  const lv = live && live.vend[v];
  const running = lv && lv.craftStarted && !lv.craft;
  if (running) for (const k of Object.keys(out)) out[k] = { state: "running", note: "awaiting the sandbox's report" };
  const st = (s) => (s === "pass" || s === "passed" ? "pass" : s === "skipped" || s === "blocked" ? "blocked" : s ? "fail" : "todo");
  if (cr && cr.status === "ran") {
    const r = cr.result || {}; const t = Object.fromEntries((r.tests || []).map((x) => [x.id, x]));
    const step = (id, name) => ((t[id] || {}).steps || []).find((s) => s.name === name);
    const ingest = t.incident_ingest || {};
    const c1 = step("incident_ingest", "generate_order_failure"), c2 = step("incident_ingest", "create_vendor_task"),
      c3 = step("incident_ingest", "readback_created_task");
    out.created = { state: st(c1 ? c1.status : ingest.status), note: "checkout failure → incident in Postgres" };
    out.persisted = { state: st(c2 && c3 ? (c2.status === "passed" && c3.status === "passed" ? "pass" : "fail") : ingest.status), note: "vendor record read back by id" };
    out.synced = { state: st((t.status_sync || {}).status), note: "resolved internally → vendor reads Resolved" };
    out.dupes = { state: st((t.repeated_events || {}).status), note: "same event ×3 → one vendor record" };
    const ex = r.extra_tests || [];
    out.recovery = { state: ex.length ? st(ex.every((x) => x.status === "pass") ? "pass" : ex.some((x) => x.status === "fail") ? "fail" : "skipped") : "todo",
      note: "INJECTED by our harness: dropped connection + lost response" };
    const jr = cr.judas_readback || {};
    out.readback = { state: jr.found && jr.status_ok ? "pass" : jr.reported ? "fail" : "todo", note: `${jr.read || 0}/${jr.reported || 0} records via ${jr.interface || "vendor API"}` };
  } else if (cr) {
    for (const k of Object.keys(out)) out[k] = { state: "blocked", note: cr.status === "not_run" ? `not run: ${cr.reason}` : `error: ${cr.error || ""}` };
  } else if (lv && lv.craft) {                       // live event only: three grouped tests + readback summary
    const t = lv.craft.tests || {};
    if (lv.craft.status === "error") for (const k of Object.keys(out)) out[k] = { state: "blocked", note: lv.craft.error || "runner error" };
    else {
      out.created = out.persisted = { state: st(t.incident_ingest), note: "" };
      out.synced = { state: st(t.status_sync), note: "" }; out.dupes = { state: st(t.repeated_events), note: "" };
      out.recovery = { state: "todo", note: "details arrive with the final result" };
      out.readback = { state: /^(\d+)\/\1 found$/.test(lv.craft.judas_readback || "") ? "pass" : "fail", note: lv.craft.judas_readback || "" };
    }
  }
  return out;
}

function sandboxPanel(v, ev, live) {
  const cr = ev && (ev.crafting || {})[v];
  const r = (cr && cr.result) || {}; const sb = r.sandbox || {};
  const lv = live && live.vend[v];
  const liveErr = lv && lv.craft && lv.craft.status === "error";
  const state = lv && lv.craftStarted && !lv.craft ? "running" : cr && cr.status === "ran" ? "done" : cr || liveErr ? "blocked"
    : lv && lv.craft ? "done" : "idle";
  if (!sb.name && lv && lv.craft && lv.craft.sandbox) sb.name = lv.craft.sandbox;   // name reported in the live event
  const api = v === "airtable" ? "Airtable REST API" : "monday.com GraphQL API";
  const checks = craftChecks(v, ev, live);
  const sum = r.summary && r.summary.total ? `${r.summary.passed}/${r.summary.total} integration checks` : null;
  const extra = [sb.id && `id ${sb.id}`, sb.org && `${sb.org} org`, sb.template && `template ${sb.template}`].filter(Boolean).join(" · ");
  const ident = sb.name ? [sb.url ? link(sb.url, sb.name) : el("span", { text: sb.name }), extra ? el("small", { text: ` · ${extra}` }) : null]
    : [el("span", { class: "muted-i", text: state === "running" ? "sandbox running…" : "no sandbox result yet" })];
  return el("article", { class: "cc-sandbox", "data-state": state, "data-vendor": v },
    el("header", {}, el("span", { class: "cc-tag", text: "Crafting isolated sandbox" }), el("b", { text: vname(v) }),
      sum ? el("span", { class: "cc-sum", text: sum }) : null),
    el("p", { class: "cc-ident" }, ...ident),
    el("div", { class: "cc-diagram", "aria-label": `Northstar test app connected to ${api}` },
      el("span", { class: "svc" }, el("b", { text: "Checkout" }), el("small", { text: ":8081" })), el("i", { text: "→" }),
      el("span", { class: "svc" }, el("b", { text: "Incident" }), el("small", { text: ":8080" })), el("i", { text: "→" }),
      el("span", { class: "svc db" }, el("b", { text: "Postgres" }), el("small", { text: "internal state" })), el("i", { class: "out", text: "⇢" }),
      el("span", { class: "svc ext" }, el("b", { text: vname(v) }), el("small", { text: api }))),
    el("ul", { class: "cc-checks" }, ...CHECKS.map(([k, label]) => el("li", { "data-state": checks[k].state },
      el("span", { class: "cc-mark", "aria-hidden": "true" }), el("span", { class: "cc-lbl", text: label }),
      el("small", { text: checks[k].note }), el("span", { class: "sr", text: ` (${checks[k].state})` })))));
}

function renderSandboxes(ev, live) { fill($("sandboxes"), ...vendorIds().map((v) => sandboxPanel(v, ev, live))); }

function renderHighlights(ev, live) {
  const cards = [
    ["dupes", "Duplicate events", "The same incident event is delivered three times.", "Exactly one vendor record."],
    ["synced", "Status synchronization", "The buyer's app resolves the incident.", "The vendor record reads Resolved when re-read independently."],
    ["recovery", "Failure recovery (injected)", "Our test harness drops a connection and loses a response on purpose. Not an Airtable or monday.com outage.",
      "The integration recovers with no duplicate record."],
  ];
  const per = Object.fromEntries(vendorIds().map((v) => [v, craftChecks(v, ev, live)]));
  fill($("highlights"), ...cards.map(([k, title, what, expect]) => el("article", { class: "cc-hl" },
    el("b", { text: title }), el("p", { text: what }), el("p", { class: "cc-expect" }, el("span", { text: "Pass if: " }), expect),
    el("ul", {}, ...vendorIds().map((v) => el("li", { "data-state": per[v][k].state },
      el("span", { class: "cc-mark", "aria-hidden": "true" }), el("span", { text: vname(v) }),
      el("small", { text: per[v][k].state === "todo" ? "not reported" : per[v][k].state })))))));
}

function verdictSummary(ev) {
  const cell = (v) => {
    const cr = (ev.crafting || {})[v] || {}; const r = cr.result || {}; const jr = cr.judas_readback || {};
    const caps = Object.values((ev.evidence || {})[v] || {});
    return el("div", { class: "cc-vs" }, el("b", { text: vname(v) }),
      el("span", {}, el("em", { text: r.summary ? `${r.summary.passed}/${r.summary.total}` : "—" }), " Crafting integration checks"),
      el("span", {}, el("em", { text: jr.reported ? `${jr.read}/${jr.reported}` : "—" }), " records re-read by Judas"),
      el("span", {}, el("em", { text: `${caps.filter((c) => c.level === "verified").length}/${caps.length}` }), " capability tests verified via MCP"));
  };
  return el("div", { class: "cc-vsum" }, ...vendorIds().map(cell));
}

async function load() {
  meta = await api("/api/live/scenario");
  $("request").textContent = meta.scenario.request;
  fill($("tests"), ...meta.tests.map((t) => el("li", {}, el("b", { text: `${t.requirement}` }), el("span", { text: ` · ${t.test}` }))));
  fill($("untested"), ...meta.untested.map((u) => el("li", { text: u })));
  renderVendors(null); renderSandboxes(null, null); renderHighlights(null, null);
}

/** While a run is in progress: poll the real event log and render the stage the backend has reached. */
function trackRun(clickedAt) {
  let eid = null, last = "";
  const tick = async () => {
    try {
      const all = (await api(eid ? `/api/events?session_id=${encodeURIComponent(eid)}` : "/api/events")).events || [];
      if (!eid) {
        const starts = all.filter((e) => e.event_type === "evaluation_started" && (e.payload || {}).kind === "live");
        const cand = starts[starts.length - 1];
        if (!cand) return;
        const finished = all.some((e) => e.session_id === cand.session_id && e.event_type === "comparison_completed");
        if (finished && Date.parse(cand.timestamp) < clickedAt - 2000) return;   // an older, finished run
        eid = cand.session_id;
      }
      const evts = all.filter((e) => e.session_id === eid);
      const sig = evts.map((e) => e.event_id).join();
      if (sig === last) return;
      last = sig;
      const st = deriveState(evts);
      applyStage(st, `LIVE run ${eid} · started ${evts[0].timestamp.slice(11, 19)} UTC · ${evts.length} real events so far`);
      renderSandboxes(null, st); renderHighlights(null, st); renderHistory(evts);
    } catch { /* keep the last real state; the final response is authoritative */ }
  };
  tick();
  const h = setInterval(tick, 1000);
  return () => clearInterval(h);
}

async function runLive() {
  const btn = $("btn-run");
  $("err").hidden = true;
  btn.disabled = true; btn.textContent = "Running: vendors, then Crafting sandbox (up to ~2 min)…";
  const clickedAt = Date.now();
  applyStage({ stage: "buyer", steps: { ...Object.fromEntries(STEPS.map((s) => [s, "todo"])), buyer: "active" } },
    "LIVE run requested · waiting for the backend's first event…");
  renderVendors(null); renderSandboxes(null, null); renderHighlights(null, null);
  $("verdict").replaceChildren(el("p", { class: "empty", text: "A new live run is in progress. The verdict appears when the backend reports it." }));
  const stop = trackRun(clickedAt);
  try {
    const ev = await api("/api/live/run", {});
    stop();
    showRun(ev, `${ev.joined ? "Joined the run already in progress" : "New live run"}: ${ev.evaluation_id}, ${ev.created_at.slice(11, 16)} UTC.`, true);
    const target = $("verdict-panel");
    target.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
    setTimeout(() => { if (Math.abs(target.getBoundingClientRect().top) > 40) target.scrollIntoView({ block: "start" }); }, 1000);
  } catch (e) {
    stop();
    applyStage({ ...deriveState([]), stage: "blocked" }, `Run did not complete: ${e.message}`);
    $("err").textContent = e.message; $("err").hidden = false;
  } finally { btn.disabled = false; btn.textContent = "Run live evaluation"; }
}

function showRun(ev, note, isLive) {
  renderVendors(ev); renderVerdict(ev); renderHistory(ev.events || []);
  renderSandboxes(ev, null); renderHighlights(ev, null);
  const st = deriveState(ev.events || []);
  if (!st.done && ev.verdict) { st.stage = "verdict"; st.steps.verdict = "done"; }
  const when = `${ev.created_at.slice(0, 10)} ${ev.created_at.slice(11, 19)} UTC`;
  applyStage(st, isLive ? `Completed live run ${ev.evaluation_id} · ${when} · ${(ev.events || []).length} real events`
    : `SAVED RUN ${ev.evaluation_id} · ${when} · evidence from a completed run, not a new execution`);
  $("run-note").textContent = note;
}

$("btn-run").addEventListener("click", runLive);
load().then(async () => {
  try {
    const ev = await api("/api/live/latest");
    showRun(ev, `Showing the latest completed run (${ev.evaluation_id}, ${ev.created_at.slice(11, 16)} UTC). Click Run live evaluation to run it again.`);
  } catch { /* no run yet */ }
}).catch((e) => { $("err").textContent = e.message; $("err").hidden = false; });
