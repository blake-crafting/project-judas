# Project Judas: Build Audit

**Auditor:** Claude (Fable 5.1), independent pass over the `feat/project-judas` branch
**Date:** 2026-11-07 · **Branch head at audit:** `47f3e3a` for §1–6; fix pass committed after §7 (see `git log`)
**Method:** every claim below was checked by running a command, reading the code path, or probing the live app.
Claims I could not check are listed as such in section 6. A second, independent reviewer (Opus) is running in parallel;
its findings are appended in section 7 when they arrive.

---

## 1. What was built vs. what was planned

| Plan task | Delivered | Evidence |
|---|---|---|
| 1 Brex OpenAPI parsing + Ramp response fields | ✅ | `seller/vendor_docs.py`, `tests/test_vendor_docs.py` (9 tests incl. 1 live fetch) |
| 2 Vendor-aware docs MCP + `DocsSession` | ✅ | 5 read-only tools incl. `get_schema`; `tests/test_docs_mcp_vendor.py` (7 tests against a real server process) |
| 3 Contracts + question set | ✅ | `judas/contracts.py`, `judas/questions.py`; amended finding/verdict vocabularies |
| 4 Vendor profiles + answer builder | ✅ | `seller/vendors/`; custom question via bounded keyword search |
| 5 Typed A2A dispatch, Brex seller :9104, `send_to_seller` | ✅ **M1** | `tests/test_judas_sellers_e2e.py`: both vendors answer 7 questions over real A2A, 3 subprocesses |
| 6 Deterministic comparison | ✅ | `judas/compare.py`; 9 tests incl. determinism |
| 7 Evaluation lifecycle + events | ✅ **M2** | concurrent contact, 20 s timeout, path-safe ids, archive-not-delete |
| 8 Judas web API | ✅ | 12 tests: validation, 404s, traversal, isolation, reset |
| 9 Judas page | ✅ **M3** | browser click-through (desktop + 375 px), seller-down path, custom question |
| 10 Seldon Wren QA | ⏸ **blocked** | `seldon.yml` (7 cases) passes `seldon validate --local`; live runs need `seldon login` |
| 11 Crafting hosting (optional) | not started | by design; only if organizers require a deployed URL |

**Test suite at audit:** `.venv/bin/python -m pytest -q` → **89 passed** at the time of §1–6; **98 passed** after the fix pass in §7. Four tests are marked
`live` and hit docs.ramp.com / developer.brex.com; the rest run offline against seeded fixtures.

**All four points from the external (ChatGPT) review are implemented:** early Wren smoke test (ready, blocked on login);
custom buyer question; stricter evidence semantics; proportional Wren plan.

---

## 2. Findings from this audit

### 2.1 Important (fixed in `47f3e3a`): the buyer trusted whatever a seller said

**What I found.** `judas/contracts.validate()` correctly rejects a `documented` answer that carries no `real_mcp_response`
evidence, and the *seller-side* builder never produces one. But the *buyer* never called `validate()` on responses it received,
and `compare()` counted findings at face value. Probe: a forged Brex response claiming `documented` for all 7 questions with
empty evidence produced coverage **7/7** and verdict **`docs_favor:brex`**. The spec's core promise ("a seller cannot mark
itself as passing") was enforced on the wrong side of the trust boundary.

**Fix (TDD, red → green).**
- `judas/evaluation.contact()` now validates every received response. An invalid one is stored as `None`, recorded as
  `exchange_failed` with `InvalidVendorResponse: …`, and shown as "unreachable/unusable" to the buyer.
- `judas/compare()` coerces any unsubstantiated `documented` claim to `docs_unavailable` with the summary prefix
  *"Unsubstantiated claim (no documentation evidence)"* (defense in depth).
- Tests: `test_buyer_rejects_unsubstantiated_vendor_response`, `test_unsubstantiated_documented_claim_is_never_counted`.

**Ruling worth knowing.** A vendor whose claims are *all* unsubstantiated yields `insufficient_evidence`, not a win for the
other vendor. Judas refuses to compare rather than crown someone by default. That is the honest outcome; it also means a
demo with one broken seller shows "insufficient evidence", which is correct.

### 2.2 Same-evaluation concurrency (upgraded and fixed after the second review)

My probe (two overlapping `Evaluate both` on one id) showed only duplicated history entries, so I graded it cosmetic. The
second reviewer showed worse cases: two parallel *Contact* calls lost one vendor's answer, and an *Evaluate* during a
*Contact* saved `comparison: null`. Fixed in the §7 pass: a per-evaluation lock around load → save, an atomic file write,
and a single in-flight guard in the UI so a double-click can't start two runs.

### 2.3 Process slips during the build (already corrected, recorded for honesty)

- Twice a commit landed while the test suite was red (once a shell pipe hid the exit code; once the commit ran in parallel
  with the tests). Both were fixed in the next commit; the ledger records them. The discipline since: run, read, then commit.
- One regression test (null-safe UI rendering) was written immediately *after* its fix rather than before; the ledger says so.

---

## 3. Honesty and provenance checks (the product's core promise)

| Rule | Where enforced | Verified how |
|---|---|---|
| `documented` requires real MCP evidence | seller builder (`seller/vendors/common.py`), buyer receive path (`evaluation.contact`), comparison (`compare._substantiated`) | forged-response probe before/after fix; tests |
| Not found ≠ unsupported | exact copy: *"Not found in the reviewed X documentation. This does not mean the capability is unsupported."* | `test_missing_doc_is_not_found_not_unsupported`; seen in browser |
| Documented ≠ tested | every comparison cell carries `tested: false` and a "not tested" badge | `test_cells_are_untested…`; seen in browser |
| Official source vs our wrapper | evidence shows `source` (docs.ramp.com / developer.brex.com URL) and separately `via: hackathon-docs MCP (our wrapper, not the vendor's)` | tests; seen in browser |
| Verdict describes docs coverage only | caveat copy is exact and always rendered; verdict vocabulary is `docs_favor:<v>` / `no_clear_difference_in_docs` / `insufficient_evidence` | `test_q3_money_note_clarification_and_caveat` |
| Docs down → honest label | all findings `docs_unavailable`, limitation `Live vendor docs unavailable (…)` | `test_docs_unavailable_gives_docs_unavailable_not_crash` |
| Sellers labeled simulated | card descriptions start *"Hackathon-owned adapter, NOT operated by X."*; limitation copy exact; persona starts `(fictional)` | `test_brex_card_is_labeled_not_operated_by_brex`, `test_limitation_copy_and_pitch_label` |

**One accuracy bug was caught by the browser check, not by tests:** Ramp's "no duplicates" answer matched the word
"idempotency" on an unrelated accounting-sync endpoint, which made Ramp look like it documented de-duplication for
transactions. Searches are now scoped to the transaction endpoints (`Lookup.within`). The comparison flipped from
"Ramp 7/7" to the current, correct "Ramp 5/7" with Q5 *not found in reviewed docs*. This is exactly the kind of false
positive the product must not produce; it argues for keeping a live-docs browser check in the demo rehearsal.

---

## 4. Security and safety checks

| Check | Result |
|---|---|
| Outbound fetches | only the four allowlisted vendor doc URLs (`vendor_docs.VENDORS`); `_download()` raises `PermissionError` otherwise. Other URLs in code are labels/comments, not fetched. |
| A2A targets | registry allowlist on both the registry `base_url` and the Agent Card's advertised endpoints (`buyer/client._check_allowed`); tested |
| Evaluation ids | `^eval-\d{8}-[0-9a-f]{6}$` enforced in `load()`; `../../etc/passwd` and URL-encoded traversal → 404; tested |
| Input bounds | request ≤ 2000 chars, custom question ≤ 300; empty → 400; probed live |
| Rendering | `judas.js` has zero `innerHTML` / `insertAdjacentHTML` / `outerHTML`; all nodes via `textContent`; `<b>` and `<script>` render literally (probed in browser); static test guards it |
| Secrets | none in the tree (scan for `seldon_…`, `sk-…`); `.env`, `data/`, `.superpowers/` git-ignored; Seldon key never handled by Claude |
| Real accounts | no Ramp/Brex production API calls anywhere; claude.ai Brex connector unused |
| Write actions | sellers are read-only docs Q&A; MCP tools all `read_only_hint=True` and the client refuses tools that are not |

---

## 5. Live-app probes performed

- Full flow with live vendor docs: both sellers answer; 7-row comparison; verdict `docs_favor:brex`
  (reason: must-have Q5 found only in Brex's reviewed docs); 1 clarification (Ramp Q3 amount conversion); 9 events.
- Custom question `Do you de-duplicate repeated transaction imports? <b>bold?</b>`: sent to both, answered via keyword
  search, flagged *needs clarification*, HTML rendered literally, no stray `null` text.
- Brex seller stopped: Brex column `docs unavailable`, state "unreachable" with a `./dev.sh` hint, verdict
  `insufficient_evidence`, response in ~1 s (no hang).
- 375 px viewport: no horizontal overflow.
- `./dev.sh` / `./dev.sh stop`: all five ports up / all five freed.

---

## 6. Not verified (and why)

- **Seldon Wren live run.** Blocked: the `seldon` CLI is not authenticated on this machine (`seldon login` is yours to run).
  `seldon.yml` is validated for syntax only. Plan: one smoke case → core workflows → full 7-case suite; keep every run id/report.
- **Organizer rule.** Whether a tunneled local build counts as a "deployed build" for the final Wren run is unconfirmed;
  Crafting hosting is the fallback.
- **Live-docs tests on a fresh cache.** The 4 `live` tests passed today; they depend on docs.ramp.com and developer.brex.com
  being reachable and unchanged. The Brex `_spec/` URLs are public but not advertised.
- **Load beyond demo scale.** No stress testing; the app is a single-process demo with JSON persistence.

---

## 7. Independent reviewer findings (Opus) and what was done

The second reviewer read the full branch diff, ran the suite on a clean checkout (83 passed, 4 live deselected) and
reproduced its findings with scripts. Its verdict: **"Ready to merge: with fixes."** Re-graded by effect and acted on in one
TDD fix pass (every fix had a failing test first; suite now **98 passed**).

| # | Reviewer finding | Grade (reviewer → mine) | Outcome |
|---|---|---|---|
| 1 | A vendor whose docs were *unavailable* was described as if its docs lacked the feature ("Found in Ramp's reviewed docs only"); a partial outage could even produce `docs_favor:ramp` for docs never read | Critical → Critical | **Fixed.** No one-sided note when either cell is `docs_unavailable`; a must-have gap on either side → `insufficient_evidence` |
| 2 | Buyer trusts seller claims (my §2.1, same finding) + two gaps: `validate()` crashes on non-dict items (→ 500), and any `https://` string a seller sends is shown as an "official source" | Important → Important | **Fixed.** Validation tolerates garbage; evidence counts only if it cites the vendor's own allowlisted doc URL |
| 3 | Same-id race loses data (parallel Contact Ramp/Brex → one write lost; Evaluate during Contact → `comparison: null`); UI never disables the other buttons; double-click orphans an evaluation | Important → Important (I had under-graded this as cosmetic in §2.2) | **Fixed.** Per-evaluation lock, atomic save, single UI in-flight guard |
| 4 | Custom question marked `documented` from one generic keyword ("repeated" matched an unrelated Ramp endpoint) | Important → Important | **Fixed.** Domain stopwords; all meaningful keywords must co-occur in one operation |
| 5 | Stale cache + slow network: every MCP call retried a 15 s download → seller exceeds the 20 s buyer timeout and shows "unreachable" despite a usable cache | Important → Important (live-demo risk) | **Fixed.** 10-minute backoff after a failed refresh; 5 s timeout when a cache exists |
| 6 | Wren target is `127.0.0.1:8700` | Unverified | **Stands.** The Seldon CLI tunnels localhost targets; confirmed by the smoke run after login |
| 7 | `ID_RE` accepts a trailing newline (still 404s) | Minor | Deferred |
| 8 | A seller `judas.error/v1` reply was treated as success | Minor | Closed by the §2.1 fix (receive-side validation) |
| 9 | Brex merged scopes produced a broken "url1 + url2" citation link | Minor → Important (a broken citation in a citations product) | **Fixed.** Primary spec cited; all contributing specs listed |
| 10 | Ramp Q4/Q6 lookups are permissive (no changed-since filter / event-type check) | Minor | Deferred; the answer text shows the actual fields |
| 11 | `LIVE_DOCS=off` only affects Ramp | Minor | Deferred; `EVIDENCE_SOURCE=off` is the working kill switch |
| 12 | Non-string `source` would crash rendering | Minor | Closed by #2 (sources must be strings in the allowlist) |

Reviewer's "declined to judge" list (verdict rule design, the Q5 search scope, array-shaped OpenAPI responses, re-parsing
cost, no cancellation after buyer timeout) was reviewed; none changed a decision.

**Note on my own audit:** the reviewer caught a Critical issue (#1) that my pass missed, and showed that an issue I had
graded as cosmetic (#3) actually lost data. Two independent reads were worth it.

---

## 8. Recommendation

The branch is **ready to merge for the demo** once Wren has run at least the smoke case. One Critical and five Important
findings across the two reviews are fixed with tests (suite 98/98); the four deferred items are minor. Remaining human steps: `seldon login`, then say "run Wren"; optionally decide on
the per-evaluation lock and on Crafting hosting.
