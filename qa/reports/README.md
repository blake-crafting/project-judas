# Seldon Wren QA evidence

Seldon's Wren QA agent tests the Judas pages in a real browser, from plain-English test cases. Everything here
comes from real Wren runs; nothing is simulated. Each attempt keeps its own files.

## Summary

| Version tested | Suite | Result | Experiment |
|---|---|---|---|
| **Final demo** (Airtable vs monday.com + Crafting) | `qa/final.seldon.yml` (7 cases) | ✅ **7/7 passed** | `b80fb5c3-dea2-42dc-a574-47df733381ab` |
| Evaluator version (Ramp vs Brex, with priorities, demos, challenges) | `qa/evaluator.seldon.yml` (7 cases) | ✅ 7/7 passed | `112bd842-1248-4cc0-a78c-00723b934f73` |
| First version (Ramp vs Brex documentation comparison) | `seldon.yml` (7 cases) | ✅ 7/7 passed | `7b31724c-fa9a-4b5a-af37-92a73b517901` |

## All attempts

| # | When (UTC) | What ran | Outcome | Files |
|---|---|---|---|---|
| 1 | Nov 7, 22:59 | Smoke test (1 case) | Refused before starting: the Seldon workspace had no Wren credits yet. No experiment created | `2026-11-07-smoke-attempt.md`, `.cli.log` |
| 2 | Nov 7, 23:01 | Smoke test (1 case) | Same refusal (0 credits). The tunnel did reach the app | `2026-11-07-smoke-attempt-2.cli.log`, `.balance.txt` |
| 3 | Nov 7, 23:04 | Smoke test (1 case), after credits were granted | ✅ 1/1 passed (`b0f403bd-6103-43d1-bf0a-d2c329fd283f`). The log showed the tunnel opens the app's home page, so every case now clicks into the right tab first | `2026-11-07-smoke-attempt-3.*` |
| 4 | Nov 7, 23:07 | First version, full suite | ✅ 7/7 passed (`7b31724c…`) | `2026-11-07-full-run-1.*` |
| 5 | Nov 8, ~00:20 | Evaluator version, full suite | ✅ 7/7 passed (`112bd842…`). Wren set priorities, ran the synthetic demo, flipped the verdict with "Real-time first", and challenged a requirement | `2026-11-07-evaluator-run-1.*` |
| 6 | Nov 8, ~02:06 | **Final demo**, full suite | ✅ **7/7 passed** (`b80fb5c3…`). Wren clicked **Run live evaluation**, saw a new live run complete, and checked the stage chain, adapter labels, Crafting evidence, the two separate verdicts and the stated limits | `2026-11-08-final-run-1.*` |

Each attempt's files are the CLI output (`.cli.log`), the report (`.report.md` / `.report.json`), and Wren's
step-by-step reasoning (`.agent-log.txt`). Screenshots are in the Seldon app under each experiment.

## Rerunning
Start the app (`./dev.sh`), then:
```bash
seldon run --file qa/final.seldon.yml --url http://127.0.0.1:8700/judas/live --auto-approve --wait
```
A local URL is reached through a temporary tunnel that the Seldon CLI opens for the run.
