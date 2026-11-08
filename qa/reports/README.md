# Wren QA evidence

Real Seldon Wren attempts only. Nothing here is simulated. Files are never overwritten; each attempt gets its own files.

| # | When (UTC) | Build | What was run | Outcome | Files |
|---|---|---|---|---|---|
| 1 | 2026-11-07 22:59 | `d0a4523` | one-case copy of `seldon.yml` (`open-judas`) | **Refused before start; no experiment created.** Auth OK, tunnel OK; API: *"You've used all your simulations. Buy more to keep running tests."* | `2026-11-07-smoke-attempt.md`, `.cli.log` |
| 2 | 2026-11-07 23:01 | `df0567d` (UI pass) | `qa/smoke.seldon.yml` (`open-judas`) | **Same refusal; no experiment created.** Tunnel reached the app (`tnl-da83db62717d.joinblok.co`) before the API refused. Balance right after: 0 granted / 0 used / 0 remaining tasks ($3 each), $0 credit. | `2026-11-07-smoke-attempt-2.cli.log`, `.balance.txt` |

| 3 | 2026-11-07 23:04 | `82d4630` | `qa/smoke.seldon.yml` (`open-judas`) | ✅ **1/1 passed.** Experiment `b0f403bd-6103-43d1-bf0a-d2c329fd283f`. The agent log showed the tunnel lands on the Discovery Lab page (`/`), so every case now clicks the Project Judas tab first (`9018fcd`). | `2026-11-07-smoke-attempt-3.*` (CLI log, report JSON/MD, agent log) |
| 4 | 2026-11-07 23:07 | `9018fcd` | full `seldon.yml` (7 cases) | ✅ **7/7 passed** (report `complete`, 0 failed, 0 blocked). Experiment `7b31724c-fa9a-4b5a-af37-92a73b517901`. The agent log shows Wren clicking through every flow and reading real content (the A2A reply tally, `docs.ramp.com` / `developer.brex.com` citations, the verdict panel, 9 timestamped history events). No bugs found. | `2026-11-07-full-run-1.*` (CLI log, report JSON/MD, agent log) |

| 5 | 2026-11-08 ~00:00 | `6687c55` (evaluator build) | `qa/evaluator.seldon.yml` (7 cases, incl. demo, priorities, challenge) | ✅ **7/7 passed** (report `complete`, 0 failed, 0 blocked). Experiment `112bd842-1248-4cc0-a78c-00723b934f73`. The agent log shows Wren choosing Must/Nice/Skip rows, running the Ramp demo (labeled synthetic), flipping to Real-time first ("favor Ramp", Q6 "decides the verdict"), and challenging Q4 ("searched 4 more places… still not found"). | `2026-11-07-evaluator-run-1.*` (CLI log, report JSON/MD, agent log) |

**Which report covers which build:** attempts 3–4 cover `judas-demo` (`5013eb9`, baseline `seldon.yml`); attempt 5
covers the evaluator build (`6687c55`, `qa/evaluator.seldon.yml`). Evaluator screenshots:
`https://app.seldon.com/experiments/112bd842-1248-4cc0-a78c-00723b934f73`.

**Screenshots and full evidence:** in the Seldon app at `https://app.seldon.com/experiments/7b31724c-fa9a-4b5a-af37-92a73b517901` (smoke: `…/b0f403bd-6103-43d1-bf0a-d2c329fd283f`).

**Earlier blocker (resolved 23:03):** the Seldon workspace behind this API key has no Wren tasks granted. Nothing was charged and nothing was purchased.

**To rerun:** `seldon run --file qa/smoke.seldon.yml --url http://127.0.0.1:8700/judas --auto-approve --wait`,
then the full `seldon.yml` (7 cases); save `seldon report --id <experiment-id> --json` and `--md` here as attempt 3+.
