# Project Judas

**Don't take the vendor's word for it.** Judas is a buyer's AI agent that makes competing software vendors *prove*
what their products can do. It talks to vendor agents, has them do real work in the real products, tests the result
against the buyer's own application, and then checks every result itself.

Built in one day for the Seldon **"Ship Happens"** hackathon (San Francisco, November 7, 2026).

---

## The demo: incident tracking, Airtable vs monday.com

> *"We operate a multi-service e-commerce application. Evaluate Airtable and monday.com for operational incident
> tracking. Demonstrate how each handles an actual application-generated incident, repeated events, and status
> synchronization."*

One click on **`/judas/live`** runs the whole evaluation (about 60–90 seconds):

| Stage | What happens | Who runs it |
|---|---|---|
| **1 · A2A** | Judas sends the request to a seller agent for each vendor over the **Agent2Agent protocol** | Judas → our seller adapters (labeled "not operated by" the vendor) |
| **2 · Official MCP** | Each adapter creates and updates real incidents through the vendor's **official MCP server** (`mcp.airtable.com`, `mcp.monday.com`) | The vendors' own servers and products |
| **3 · Crafting** | Our e-commerce app runs in a **Crafting sandbox**, raises a real incident, sends it 3 times, then resolves it | Crafting sandboxes (one per vendor) |
| **4 · Independent check** | Judas re-reads every record through each vendor's **separate public API** (Airtable REST, monday GraphQL) and compares every value | Judas |
| **Verdict** | Two separate results: **vendor capability** (stages 2+4) and **buyer-environment compatibility** (stages 3+4) | Fixed rules, no AI deciding |

**Latest result:** both vendors proved every tested requirement (7/7 independent checks each), and both passed
every buyer-environment test in Crafting (3/3 each, plus retry and no-duplicate checks). Judas also lists what's
still untested, and always shows a caveat: one run in free-plan test workspaces doesn't prove production
reliability.

### Real vs ours vs fictional
- **Real:**
  - the Airtable and monday.com products and their official MCP servers;
  - the vendor APIs used for read-back;
  - the Crafting sandboxes;
  - the A2A messages (official `a2a-sdk`) and MCP calls (official `mcp` SDK).
- **Ours:** the Judas buyer app, the two A2A seller adapters, and the e-commerce test app and runner.
- **Fictional:** the company and the incident data.

---

## Second scenario: Ramp vs Brex (evidence evaluator)

**`/judas`**: simulated seller agents answer 7 technical questions from Ramp's and Brex's **official** docs (fetched
through our own read-only MCP server), with:
- **Facet-based evidence:** a recommended pattern is never treated as a guarantee, and "not found" never means
  "unsupported".
- **Buyer priorities** (Must / Nice / Skip) that change the verdict.
- **Synthetic sync demos** over MCP, clearly labeled.
- **A Challenge button** that asks a vendor agent to search harder; only strict matches count.

---

## Quality: Seldon Wren

Seldon's Wren QA agent tested Judas in a real browser:

| Version | Result |
|---|---|
| **Final demo** (Airtable vs monday.com + Crafting) | ✅ **7/7**: buyer request, one-click run, stage chain, truthful labels, Crafting evidence, capability vs environment, verdict limits |
| Evaluator version (Ramp vs Brex) | ✅ 7/7 |
| First version (Ramp vs Brex documentation comparison) | ✅ 7/7 |

Details and all reports: [`qa/reports/`](qa/reports/README.md). Plus **140 automated tests** (`pytest`).

---

## Run it locally
```bash
./dev.sh                                    # starts all servers (web app on :8700)
open http://127.0.0.1:8700/judas/live       # Airtable vs monday.com
open http://127.0.0.1:8700/judas            # Ramp vs Brex
.venv/bin/python -m pytest -q               # tests
./dev.sh stop
```
- **Requirements:** Python 3.15 with a virtual environment in `.venv` (dependencies in `requirements.txt`).
- **The live scenario needs your own test workspaces:**
  - a monday.com account (set in a git-ignored `.env`);
  - an Airtable login (`.venv/bin/python scripts/airtable_login.py` opens the browser for OAuth);
  - a Crafting runner command (`CRAFTING_RUNNER` in `.env`, pointing at `crafting/judas_bridge.py`).
- **Without them,** the live page shows each stage as blocked or not run, honestly.

## Project layout
| Path | What it is |
|---|---|
| `judas/live.py`, `judas/live_flow.py` | Live evaluation: tests, evidence levels, verdicts, Crafting stage, independent read-back |
| `seller/live_server.py`, `seller/live_ops.py` | A2A seller adapters doing real work via the vendors' official MCP servers |
| `vendors_live/` | Vendor connections and the Airtable login (renewed automatically) |
| `crafting/` | Crafting integration runner and the e-commerce test app |
| `judas/compare.py`, `judas/evaluation.py`, `seller/vendors/` | Ramp/Brex evaluator |
| `docs_mcp/`, `sandbox_mcp/` | Our read-only MCP servers (official docs; synthetic demo data) |
| `web/static/` | The pages |
| `qa/` | Seldon Wren suites, reports, and live vendor evidence |
| `docs/` | [Demo script](docs/DEMO_SCRIPT.md) and [code audit of the first version](docs/JUDAS_AUDIT.md) |

## Rules Judas follows
- **Verification:** verify through a separate path; a seller's own report is never enough.
- **Labels:** our adapters are never presented as vendor-operated agents.
- **Two separate verdicts:** vendor capability and buyer-environment compatibility are judged apart.
- **Honest failure:** an adapter error or a skipped test is "not established" or "not run", never a vendor failure.
- **Data:** fictional only, in disposable test containers. No purchases, and no production data.
