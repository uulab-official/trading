# PAPER one-shot monitoring (v51)

`tools/paper_monitor.py` is a **read-only, one-shot, offline orchestration**
of the existing native probes. It is not a resident daemon, a scheduler, an
external alert delivery integration, a service restart mechanism, a full economic
audit, or evidence of production readiness.

The supported service monitor calls `paper_service.py check`. Optional
separate inputs call `market_capture.py check` for saved capture freshness and
`paper_stream.py check` for the continuing simulated account head. These are
three distinct claims: running workers, current saved candles, and valid saved
account status. All three require their own positive result before the combined
status is healthy.

## One-shot use

Use compatible Linux/Python source checkout, with the exact original service,
capture and account paths. No shell expansion or dynamic command argument is
passed to the children. The monitor does not activate any service.

```bash
python3 -I -S -B tools/paper_monitor.py \
  --service /absolute/private/btc-service \
  --capture /absolute/private/btc-capture \
  --account /absolute/private/btc-account \
  --timeout-seconds 10
```

A minimal `--service`-only check is permitted but makes **no** assertion about
the capture or account. For ongoing supervision, supply all three. The monitor
spawns each trusted local check once, sequentially, and waits up to the
per-check timeout (1–60 seconds). The process stdout/stderr are bounded to
64 KiB for result acceptance; the underlying native probes perform their own
read-only input checks.

| Exit | Meaning |
|---|---|
| `0` | All specifically requested checks returned exit 0 with exact expected status |
| `1` | One or more requested checks not healthy, timed out or returned an invalid result |
| `2` | CLI arguments unavailable or invalid |
| `4` | Monitor JSON output delivery failed |

The JSON result has `status=healthy|attention_required|unavailable`, per-probe
`healthy`, `status` and `exit_code`, and boundaries recording
`orders_submitted=false`, `notification_delivered=false`,
`production_ready=false`.

The monitor does not store or deliver alerts. A separate, explicitly approved
operator scheduler can invoke it and use its exit status to deliver alerts
through a vetted notification system. This project does not install a timer,
configure credentials, call a webhook, or restart services by itself.
Repeated invocations are independent observations with no deduplication,
escalation policy or acknowledgement tracking.

## Operational boundaries

- No broker requests, trades, state repair, account mutation, or automatic retries.
- A healthy service check is not proof of real exchange data freshness; include the
  capture probe separately.
- Saved account status is not equivalent to completed full replay/audit; perform
  `paper_stream.py audit` after stopping its writer when necessary.
- A local runner/CI success does not prove the operator's host or a future instant.
- This v51 tool is not part of the fixed v49 55-file source-release profile.
  Run it from the reviewed v51 checkout, not a supposed modified v49 archive.

Next steps still require an operator-host timed soak, tested alert delivery,
manager deployment/rollback, backup-media disaster drills, and broker-authoritative
reconciliation before any live readiness claim.
