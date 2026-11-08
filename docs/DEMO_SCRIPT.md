# Project Judas: 3–4 minute demo script (evaluator build, tag `judas-evaluator`)

**Setup (before you go on):**
1. Run `./dev.sh` (6 servers, including the demo sandbox on :9105).
2. Open `http://127.0.0.1:8700/judas` and click **Reset demo data**.
3. Full screen, 1440×900 or larger.

**Fallback:** the earlier, simpler build is tagged `judas-demo` (`git checkout judas-demo && ./dev.sh`). Its script and
its own Wren report (7/7, experiment `7b31724c…`) are in that tag.

---

### 1. The problem (0:00 – 0:25)
> "Today, enterprise buyers depend on vendors to explain what their software can do. We think buyers' AI agents will
> talk directly to vendor agents: interrogate them, ask for demonstrations, weigh the evidence, and say what still
> needs testing. That's Project Judas."

### 2. The buyer decides what matters (0:25 – 0:55)
Click **Expense sync: Ramp vs Brex**.
> "A 300-person finance team wants to sync card transactions from Ramp or Brex. Here's what matters to them: each
> requirement is a must-have, a nice-to-have, or skipped. Judas only asks about what's kept, and the verdict follows
> the must-haves."

Point at the profile chips (Balanced / Data integrity first / Real-time first). Leave **Balanced** selected.

### 3. Discover and ask, agent to agent (0:55 – 1:35)
Click **Discover agents**, then **Contact Ramp**.
> "Judas found both seller agents and sent its questions over **A2A**, the open agent-to-agent protocol. The Ramp
> agent answered from Ramp's **official** docs, which it looks up through **MCP**, a standard way for agents to use
> tools. This time that includes the guides, not just the API list: money units, scopes, pagination, webhooks."

Point at one answer's checklist:
> "Each requirement is broken into checks. For duplicates: is there a transaction ID? ✓. Is it documented as stable
> from pending to cleared? ✗, not found. A vendor's recommended pattern is labeled a pattern, never a guarantee."

### 4. "Show me": a demonstration (1:35 – 2:15)
Click **Request demo** on Ramp.
> "Now Judas asks the agent to **do** something, not just describe it. The Ramp agent runs a real sync routine over
> MCP: it pages two overlapping time windows, removes the duplicates by ID, and converts amounts to cents, including
> yen, which has no decimals."

Point at the dashed panel:
> "Notice the label: **synthetic data, our sandbox, not Ramp's servers.** Ramp's own demo server needs an account, so
> we don't pretend. This proves the integration logic works on Ramp-shaped data, not that Ramp's API behaves this way."

### 5. A buyer-specific verdict (2:15 – 3:10)
Click **Evaluate both**. The page jumps to the verdict.
> "For this buyer: **Additional testing required.** Two must-haves (stable IDs and no duplicates) aren't fully
> established by either vendor's docs. Judas says what's still open and what evidence would change its mind. An AI
> buyer that can say 'not enough evidence' is more credible than one forced to pick a winner."

Scroll to the **Q4 · incremental sync** row and click **Challenge Ramp**.
> "Judas can push back. It tells the Ramp agent exactly what's missing (a filter for changed records) and asks
> again over A2A. The agent searches more of Ramp's official docs. Still not found, and Judas says so. It even
> surfaces a lead: a field described as 'used by the `updated_after` filter', which isn't on this endpoint. Leads are
> shown, never counted. The original answer is kept in the record."

Click **Real-time first**, then **Evaluate both** again.
> "A different buyer cares about real-time updates. Same evidence, different priorities: **reviewed docs favor
> Ramp**. Ramp documents a `transactions.cleared` webhook; in Brex's reviewed docs, the only card-spend event covers
> pending and declined, not settled. The deciding row is highlighted."

Point at a cell:
> "Every cell shows what was found, how strong the evidence is (official docs, or synthetic demo) and the source.
> The caveat is always on screen."

### 6. Interaction history and QA (3:10 – 3:40)
Open **Interaction history**:
> "Every step is recorded: discovery, each A2A message, each demo request and result, each challenge, the comparison."

> "And we didn't just test it ourselves. Seldon's **Wren** QA agent tested this exact build in a real browser:
> **7 of 7 passed**, including the demo request, the buyer-priority flip and the challenge."

*Open `https://app.seldon.com/experiments/112bd842-1248-4cc0-a78c-00723b934f73` (backup: `qa/reports/2026-11-07-evaluator-run-1.report.md`).
Real run: build `6687c55`, suite `qa/evaluator.seldon.yml`.*

### Close (3:40)
> "Buyer agents that interrogate vendor agents, ask for demonstrations, and are honest about what's proven. That's
> where software buying is going."

---

**Real vs simulated (if asked):**
- **Real:** A2A messages, MCP tool calls, Ramp's and Brex's official docs and guides, the comparison logic, and the
  code that runs each demo.
- **Simulated:** the buyer company, both seller personas, and the sandbox's transaction data (shaped like each
  vendor's documented format).
- **Not included:** vendor-hosted demos (Ramp's needs an account token; no public Brex demo), real accounts,
  production APIs, and purchases.

---

## Real vendors (live): Airtable vs monday.com (about 1½ min, tab "Real vendors (live)")
*Branch `feature/real-vendor-evaluation`. Needs `./dev.sh`, the monday line in `.env`, and the one-time Airtable
login (`.venv/bin/python scripts/airtable_login.py`).*

**1. Set it up (0:00 – 0:20)**
> "So far the vendor agents were ours, answering from docs. Now the real test: a buyer asks two actual SaaS vendors
> to *prove* they can handle its project tracking, inside the real products."

Point at the request and the 4 tests (project, 3 tasks with status and priority, a status change, agent access).

**2. Run it (0:20 – 0:50)**
Click **Run live evaluation** (about 7–15 seconds).
> "Judas sends the same request over A2A to an adapter for each vendor. Those adapters are ours, and the page says
> so. But they act through each vendor's **official MCP server**, run by Airtable and by monday.com, inside our test
> workspaces."

Point at each vendor card's chain: A2A → `mcp.airtable.com` / `mcp.monday.com` → the real object IDs.

**3. Don't trust, verify (0:50 – 1:10)**
> "Here's the point: Judas doesn't take the adapter's word for it. It re-reads every task through each vendor's
> **separate public API**, Airtable's REST API and monday's GraphQL API, and checks every value. 7 of 7, on both."

Click **Open this run in Airtable** and **Open this run in monday.com**: the tasks are really there.

**4. The verdict, and agent maturity (1:10 – 1:30)**
> "Both proved every tested requirement, so on what we tested there's no difference, and Judas lists what's still
> untested. The last row is about the vendors' own agents. monday.com runs a real A2A agent; Judas reached it, and
> it replied 'AI credits limit reached' on our free plan. Airtable doesn't run one at all. That's agent-interface
> maturity, kept separate from the product verdict."

**What's real here:** the Airtable and monday.com products, their official MCP servers, monday's own A2A agent,
the A2A messages, and the independent read-back. **Ours:** the buyer and the two adapters. **Fictional:** the task
data.
