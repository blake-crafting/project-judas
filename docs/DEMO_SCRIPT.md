# Project Judas: demo script

Two scenarios. **Part 1 is the main demo** (about 2½ minutes). Part 2 is an optional second scenario.

**Setup:** run `./dev.sh`, then open `http://127.0.0.1:8700/judas/live`. The live run needs the monday.com, Airtable
and Crafting setup described in the README. The page also shows the latest completed run as soon as it opens.

---

## Part 1: Incident tracking, Airtable vs monday.com (`/judas/live`)

### 1. The problem (0:00 – 0:20)
> "Today, software buyers rely on vendors to tell them what their product can do. Judas flips that: a buyer's AI
> agent makes competing vendors *prove* it in their real products, tests it against the buyer's own application,
> and checks every result itself."

### 2. The request (0:20 – 0:35)
Point at the buyer request and the tests.
> "Our fictional company runs a multi-service e-commerce app. It wants incident tracking, and it needs to know how
> each tool handles a real application incident, repeated events, and status changes."

### 3. Run it (0:35 – 1:45)
Click **Run live evaluation** (about 60–90 seconds). The status bar lights up stage by stage. Walk through one
vendor card:

1. **Stage 1 · A2A:**
   > "Judas sends the request over the Agent2Agent protocol to a seller agent for each vendor. These adapters are
   > ours, and the page says so."
2. **Stage 2 · Official MCP:**
   > "The adapter does real work through the vendor's own MCP server, `mcp.airtable.com` and `mcp.monday.com`:
   > three incidents and a status change, with real record IDs."
3. **Stage 3 · Crafting:**
   > "Then our own e-commerce app runs in a Crafting sandbox. It raises a real incident, sends it three times, then
   > resolves it. It also survives a dropped network call and a lost response without creating a duplicate."
4. **Stage 4 · Independent check:**
   > "Finally, Judas doesn't take anyone's word for it. It re-reads every record through each vendor's separate API
   > and compares every value."

### 4. The verdict (1:45 – 2:15)
Point at the dark verdict panel.
> "Two separate answers. **Vendor capability:** both proved every tested requirement. **Buyer-environment
> compatibility:** both passed every test in our Crafting sandbox. And Judas lists what's still untested, with a
> caveat that one run doesn't prove production reliability."

### 5. Independent QA (2:15 – 2:30)
> "Seldon's Wren QA agent tested this exact flow in a real browser: 7 of 7 passed."

Show the report summary in `qa/reports/2026-11-08-final-run-1.report.md`.

**What's real:**
- the Airtable and monday.com products and their official MCP servers;
- the vendor APIs used for the checks;
- the Crafting sandboxes;
- the A2A messages.

**Ours:** the buyer app, the two seller adapters and the test app. **Fictional:** the company and the incident
data.

---

## Part 2 (optional): Ramp vs Brex evidence evaluator (`/judas`)

About 2 minutes. Simulated seller agents answer from Ramp's and Brex's **official** docs.

1. **Set priorities:** click **Expense sync: Ramp vs Brex**. Each requirement is Must / Nice / Skip, or pick a
   profile. **Suggest from my request** sets priorities from the buyer's own words.
2. **Ask:** click **Discover agents**, then **Contact Ramp**. Each answer is a ✓/✗ checklist cited to the official
   docs. A recommended pattern is never treated as a guarantee, and "not found" never means "unsupported".
3. **Demonstrate:** click **Request demo**. The agent runs a sync routine on synthetic data, clearly labeled as
   synthetic.
4. **Verdict:** click **Evaluate both**.
   - **Balanced** buyer: *"Additional testing required"*.
   - Switch to **Real-time first** and evaluate again: *"Reviewed docs favor Ramp"* (decided by webhooks).
5. **Push back:** **Challenge Ramp** on an open requirement. The agent searches more widely; only strict matches
   count.
