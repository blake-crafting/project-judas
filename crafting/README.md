# Crafting stage: buyer-application integration tests

Judas decides *what* to test and compares vendors. Crafting runs the test inside an isolated copy of
the buyer's application. The vendor's own API performs the vendor-side work.

## What runs where

| Piece | Where | Owner |
|---|---|---|
| `judas_bridge.py`, `runner.py`, `creds.py` | Judas host (the machine running Judas) | hackathon team |
| Template `judas-incident-eval`, org `demo` | Crafting | hackathon team (new; nothing shared modified) |
| Sandboxes `judas-airtable`, `judas-monday` (private) | Crafting | hackathon team |
| `service/northstar.py`: checkout :8081 -> incident-sync :8080 -> Postgres `db` | inside each sandbox | buyer code (fictional Northstar Commerce) |
| `service/vendors.py`: Airtable REST / monday GraphQL connector | inside each sandbox | buyer code, calling the vendor's real API |
| Records | Airtable table `JUDAS_CRAFTING_incidents`; monday group `JUDAS Crafting incidents` | disposable `JUDAS_TEST_` containers |

No public endpoint. No shared cluster or Kubernetes interception. The Judas host drives the sandbox with
the official `cs` CLI (`cs exec`).

## Run it

From the repository root (credentials are read from the git-ignored `.env` and `data/oauth/`):

```bash
.venv/bin/python -m crafting.runner --vendor both
```

Judas calls the bridge once per vendor. The request goes in on stdin, and one result JSON comes out on stdout:

```bash
echo '{"evaluation_id":"eval-1","tests":["incident_ingest","repeated_events","status_sync"]}' | .venv/bin/python crafting/judas_bridge.py --vendor monday
```

One-time vendor setup (safe to rerun): `.venv/bin/python -m crafting.setup_vendors`.

## The ten acceptance steps (equal inputs for both vendors)

| # | Test | Level | Who is responsible |
|---|---|---|---|
| 1 | generate_order_failure | 3 app | buyer |
| 2 | create_vendor_task | 1 vendor op | vendor |
| 3 | readback_created_task | 2 persistence | vendor |
| 4 | update_internal_status | 3 app | buyer |
| 5 | propagate_status_to_vendor | 1 vendor op | vendor |
| 6 | readback_updated_status | 2 persistence | vendor |
| 7 | duplicate_event_no_duplicate_task (3 deliveries) | 3 app | buyer |
| 8 | recover_from_transient_network_failure (injected) | 3 app | buyer |
| 9 | recover_from_lost_response_without_duplicate (injected) | 3 app | buyer + vendor lookup-by-field |
| 10 | final_state_consistent | 2 persistence | both |

After the sandbox finishes, the runner re-reads every record **from the Judas host**, independently of
the sandbox code.

The failures in steps 8 and 9 are *injected* in the buyer's connector layer. They are controlled
simulations of a network fault, not real vendor outages. `blocked` always means a prerequisite failed
(permission, plan limit, missing credential, busy sandbox, or an earlier step). It is never evidence that a
vendor lacks a capability.

## Evidence contract

`schema.py` defines `judas.crafting_evaluation/v1` (full file in `data/crafting/runs/`). The bridge emits
the lighter `judas.crafting_test_result/v1` for the Judas UI. Each `resources[]` entry has a `role` and an
`expected_status`. Only `status_sync_incident` should read "Resolved".

## Credentials

Credentials stay on the Judas host. Airtable gets a short-lived access token per run, placed in the
sandbox's `/tmp/judas-creds`, owner-only, and removed after the run. The Airtable login refreshes only on
the host. monday uses the Crafting personal secret `judas-monday-token`, which is mounted only in private
sandboxes. Nothing is printed or written into evidence.

## Concurrency

One run per sandbox at a time (file lock in `data/crafting/locks`). Airtable and monday can run in parallel.
