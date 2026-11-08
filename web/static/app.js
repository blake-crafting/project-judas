// Agent Discovery Lab UI. Seller responses are untrusted: everything is
// rendered with textContent (via el()), never innerHTML.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { sessionId: null, requirement: null, results: [], selected: null, seenEvents: new Set() };

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

async function api(path, body) {
  const res = await fetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : {});
  let data;
  try { data = await res.json(); } catch { data = { error: `Server returned ${res.status}` }; }
  if (!res.ok) { const e = new Error(data.error || `HTTP ${res.status}`); e.hint = data.hint; throw e; }
  return data;
}

function showError(id, err) {
  const box = $(id);
  box.replaceChildren(el("span", { text: err.message }), err.hint ? el("small", { text: err.hint }) : null);
  box.hidden = false;
}
const clearError = (id) => { $(id).hidden = true; };

function busy(btn, on, label) {
  btn.disabled = on;
  btn.classList.toggle("busy", on);
  if (label) btn.textContent = label;
}

// ---------- signal chain ----------
function chain({ buyer, a2a, seller, mcp, docs, sellerName, skipMcp }) {
  const set = (sel, on) => document.querySelector(sel).classList.toggle("lit", !!on);
  set('[data-node="buyer"]', buyer); set('[data-wire="a2a"]', a2a); set('[data-node="seller"]', seller);
  set('[data-wire="mcp"]', mcp); set('[data-node="docs"]', docs);
  for (const s of ['[data-wire="mcp"]', '[data-node="docs"]'])
    document.querySelector(s).classList.toggle("skipped", !!skipMcp);
  if (sellerName !== undefined) $("chain-seller").textContent = sellerName || "not chosen";
}

// ---------- scenarios ----------
async function loadScenarios() {
  try {
    const { scenarios } = await api("/api/scenarios");
    $("scenarios").replaceChildren(...scenarios.map((s) => el("button", {
      type: "button", text: s.label, "data-question": s.question,
      onclick: () => { $("question").value = s.question; $("requirements").value = s.requirements.join(", "); $("question").focus(); },
    })));
  } catch (e) { showError("err-discover", e); }
}

// ---------- 1. discover ----------
async function discover() {
  clearError("err-discover"); clearError("err-ask");
  const btn = $("btn-discover");
  busy(btn, true, "Searching registry");
  try {
    const data = await api("/api/discover", {
      session_id: state.sessionId,
      question: $("question").value,
      requirements: $("requirements").value.split(",").map((s) => s.trim()).filter(Boolean),
      consent_to_share: $("consent").checked,
    });
    state.sessionId = data.session_id; state.requirement = data.requirement; state.results = data.results;
    state.selected = data.results.find((r) => r.base_url)?.id ?? null;
    renderAgents();
    chain({ buyer: true, sellerName: null, skipMcp: false });
    await refreshTrace();
  } catch (e) { showError("err-discover", e); }
  finally { busy(btn, false, "Discover agents"); }
}

function renderAgents() {
  const list = $("agents");
  $("registry-note").textContent = `${state.results.length} of 3 registry agents matched. Curated local list, not an internet crawl.`;
  if (!state.results.length) {
    list.replaceChildren(el("li", { class: "empty", text: "No registry agent matched. Try a scenario, or mention expenses, vendors, bills or payroll." }));
    $("btn-ask").disabled = true; return;
  }
  list.replaceChildren(...state.results.map((a) => {
    const id = `agent-${a.id}`;
    return el("li", { class: "agent" + (a.id === state.selected ? " selected" : ""), onclick: () => select(a.id) },
      el("input", { type: "radio", name: "agent", id, checked: a.id === state.selected, "aria-label": a.name }),
      el("div", {},
        el("span", { class: "score", text: `match ${a.score}` }),
        el("label", { for: id, class: "name", style: "all:unset;font-weight:600;cursor:pointer", text: a.name }),
        el("div", { class: "op", text: a.operator }),
        el("div", { class: "badges" }, ...(a.badges || []).map((b) => el("span", { class: `badge ${b}`, text: b }))),
        el("div", { class: "matched", text: `matched: ${a.matched.join(" · ")}` })));
  }));
  $("btn-ask").disabled = !state.selected;
}

function select(id) {
  state.selected = id; renderAgents();
  const a = state.results.find((r) => r.id === id);
  chain({ buyer: true, sellerName: a?.name.replace(/\s*\(fictional\)/, "") });
}

// ---------- 2. connect & ask ----------
async function ask() {
  clearError("err-ask");
  if (!state.selected || !state.requirement) return;
  const btn = $("btn-ask");
  const agent = state.results.find((r) => r.id === state.selected);
  busy(btn, true, "Contacting seller");
  chain({ buyer: true, a2a: true, sellerName: agent.name.replace(/\s*\(fictional\)/, "") });
  try {
    const data = await api("/api/ask", { session_id: state.sessionId, agent_id: state.selected, requirement: state.requirement });
    const r = data.response;
    const usedMcp = (r.tool_calls || []).length > 0;
    chain({ buyer: true, a2a: true, seller: true, mcp: usedMcp, docs: usedMcp, skipMcp: !usedMcp });
    renderAnswer(data.agent, r, data.trace);
    renderTrace(data.events);
    $("trace-box").open = true;
  } catch (e) {
    chain({ buyer: true });
    showError("err-ask", e);
    await refreshTrace();
  } finally { busy(btn, false, "Connect & ask"); }
}

function renderAnswer(agent, r, trace) {
  const got = trace.find((s) => s.step === "seller_answer_received") || {};
  const nodes = [
    el("div", { class: "answer-head" },
      el("span", { class: "who", text: agent.name }),
      el("span", { class: "status", text: r.status.replace("_", " ") }),
      el("span", { class: `mode ${r.evidence_mode}`, text: r.evidence_mode.replaceAll("_", " ") }),
      el("span", { class: "meta", text: `A2A task ${got.state || "?"} · ${got.ms ?? "?"} ms · ${r.request_id}` })),
  ];
  if (r.evidence_mode === "local_fixture") nodes.push(el("div", { class: "banner", text: "Live vendor docs unavailable. Showing unverified local fixture evidence." }));
  if (r.evidence_mode === "demo_profile") nodes.push(el("div", { class: "banner demo", text: "Fictional vendor. Every claim below comes from an invented demo profile." }));

  nodes.push(el("div", { class: "claims" }, ...r.claims.map((c, i) => {
    const prov = [`source: ${c.source_type || "none"}`, `verified: ${c.verified}`, `via MCP tool: ${c.observed_via_tool}`];
    if (c.via) prov.push(`path: ${c.via}`);
    if (c.retrieved_at) prov.push(`retrieved ${c.retrieved_at}`);
    return el("article", { class: `claim ${c.finding}`, style: `animation-delay:${i * 60}ms` },
      el("div", { class: "claim-top" }, el("span", { class: `f ${c.finding}`, text: c.finding }), el("span", { class: "req", text: c.requirement })),
      el("p", { class: "detail", text: c.detail }),
      (c.evidence || []).length ? el("ul", { class: "ops" }, ...c.evidence.map((e) => el("li", {},
        el("span", { text: e.title + " " }), el("code", { text: e.path }),
        el("span", { class: "fields" }, "required fields: ",
          e.required_request_fields.length ? el("b", { text: e.required_request_fields.join(", ") }) : "none")))) : null,
      el("div", { class: "prov" }, prov.join(" · "),
        c.source_url && /^https:\/\//.test(c.source_url) ? [" · ", el("a", { href: c.source_url, target: "_blank", rel: "noopener noreferrer", text: c.source_url })] : null));
  })));

  if (r.unanswered?.length) nodes.push(el("h3", { class: "sub", text: "Unanswered (gaps the vendor can see)" }),
    el("div", { class: "unanswered" }, ...r.unanswered.map((u) => el("span", { text: u }))));
  if (r.tool_calls?.length) nodes.push(el("h3", { class: "sub", text: "MCP tool calls made by the seller" }),
    el("pre", { class: "toolcalls" }, ...r.tool_calls.map((t) => [
      el("span", { class: "t", text: t.tool }), `(${JSON.stringify(t.args)}) → ${t.match_count ?? "ok"} · ${t.ms} ms\n`])));
  nodes.push(el("h3", { class: "sub", text: "Limitations" }), el("ul", { class: "limits" }, ...r.limitations.map((l) => el("li", { text: l }))));
  $("answer").replaceChildren(...nodes);
}

// ---------- trace (event log for this session) ----------
function renderTrace(events) {
  $("trace-count").textContent = events.length;
  $("trace").replaceChildren(...events.slice().reverse().map((e) => {
    const fresh = !state.seenEvents.has(e.event_id);
    state.seenEvents.add(e.event_id);
    return el("li", { class: fresh ? "fresh" : "" },
      el("span", { class: "ev", text: e.event_type }),
      el("span", { class: "ts", text: e.timestamp.replace("T", " ").replace("+00:00", "Z") }),
      e.agent_id ? el("span", { class: "ag", text: `@${e.agent_id}` }) : null,
      el("pre", { text: JSON.stringify(e.payload, null, 1) }));
  }));
}

async function refreshTrace() {
  if (!state.sessionId) return;
  try { renderTrace((await api(`/api/events?session_id=${encodeURIComponent(state.sessionId)}`)).events); } catch { /* non-fatal */ }
}

// ---------- event log tab ----------
async function loadLog() {
  const body = $("log-body");
  try {
    const { events } = await api("/api/events");
    body.replaceChildren(...(events.length ? events.slice().reverse().map((e) => el("tr", {},
      el("td", { text: e.timestamp.slice(11, 23) }), el("td", { text: e.session_id }),
      el("td", { text: e.event_type }), el("td", { text: e.agent_id || "" }),
      el("td", { class: "payload", text: JSON.stringify(e.payload).slice(0, 400) }))) :
      [el("tr", {}, el("td", { colspan: 5, text: "No events yet." }))]));
  } catch (e) { body.replaceChildren(el("tr", {}, el("td", { colspan: 5, text: e.message }))); }
}

function switchTab(tab) {
  for (const b of document.querySelectorAll(".tabs [role=tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  $("view-buyer").hidden = tab !== "buyer";
  $("view-log").hidden = tab !== "log";
  if (tab === "log") loadLog();
}

// ---------- wire up ----------
$("btn-discover").addEventListener("click", discover);
$("btn-ask").addEventListener("click", ask);
$("btn-refresh-log").addEventListener("click", loadLog);
$("btn-reset").addEventListener("click", async () => {
  if (!confirm("Delete all demo events? This only clears this app's local event log.")) return;
  await api("/api/reset", {}); state.seenEvents.clear(); loadLog(); $("trace").replaceChildren(); $("trace-count").textContent = "0";
});
for (const b of document.querySelectorAll(".tabs [role=tab]")) b.addEventListener("click", () => switchTab(b.dataset.tab));
loadScenarios();
