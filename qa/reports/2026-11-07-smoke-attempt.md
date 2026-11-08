# Wren smoke attempt — 2026-11-07 22:59 UTC (no test executed)

Command (from the repo root):
`seldon run --file <copy of seldon.yml with only open-judas> --url http://127.0.0.1:8700/judas --auto-approve --wait --label smoke --name project-judas --json`

Result:
- Authentication: OK (`seldon login` done; `seldon runs` works).
- Tunnel: OK — the CLI registered Cloudflare tunnel connections to the local app (see `2026-11-07-smoke-attempt.cli.log`).
- Run: **refused by Seldon before starting**: "You've used all your simulations. Buy more to keep running tests."
- No experiment was created; nothing was charged.

Account balance at the time (read-only API calls):
- `seldon api get_task_balance` → granted 0, used 0, remaining 0, unit_price_usd 3
- `seldon api get_credit_balance` → balance_usd 0, granted_usd 0
- `seldon api get_experiments` → "Your plan does not include usability."

Next step: the hackathon Wren allowance must be granted to this account (or simulations purchased by the account owner).
Then re-run: smoke (1 case) → core workflows → full 7 cases.
