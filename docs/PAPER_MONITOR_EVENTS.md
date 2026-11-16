# PAPER monitor event journal (v52)

The optional `tools/paper_monitor_events.py` persists observations created by
[v51 PAPER_MONITOR.md](PAPER_MONITOR.md), **without** storing exchange credentials,
starting a service, contacting a provider or broker, sending notifications, or
declaring production readiness. The tool records state changes, not actionable
orders. It is intended for an explicit operator scheduler to invoke after a
completed local observation.

## One observation at a time

Use the compatible source checkout (this tool is not in the fixed v49 archive).
Prepare a separate private journal directory on a local POSIX filesystem and
a private, completed monitor JSON file. Do not use an account/capture/service
directory as the journal location.

```bash
umask 077
mkdir -m 700 /absolute/private/paper-monitor-history

python3 -I -S -B tools/paper_monitor.py \
  --service /absolute/private/btc-service \
  --capture /absolute/private/btc-capture \
  --account /absolute/private/btc-account \
  > /absolute/private/paper-observation-001.json

# A monitor exit of 0 or 1 yields a valid observation suitable for journaling.
# Exit 2/4 is NOT a validated observation; preserve its diagnostics separately.
python3 -I -S -B tools/paper_monitor_events.py record \
  --directory /absolute/private/paper-monitor-history \
  --input /absolute/private/paper-observation-001.json

python3 -I -S -B tools/paper_monitor_events.py check \
  --directory /absolute/private/paper-monitor-history
```

The input must be a complete owner-owned private (0600), regular, non-symlink
file of at most 64 KiB. The recorder accepts **only full three-source**
`paper_monitor` observations: service `running`, capture `current`, and
account `current` count as healthy only when each original exit code is 0,
the per-check boolean is consistent, and the aggregate status/exit code agrees.
An optional-service-only monitor run is intentionally **not** accepted into
this full-coverage history.

The journal keeps one private (0600) SQLite database and a persistent private
initialization lock file (`.paper-monitor-init.lock`) in an existing private
0700 directory. Competing process initializations are serialized, with a bounded
2-second lock-acquisition budget; the database event transaction remains SQLite
`BEGIN IMMEDIATE` with WAL and `synchronous=FULL`. Input JSON with duplicate
keys or a symlink at any input path component is refused. With a local WAL/FULL transaction, it stores each observation
count and last-observed timestamp atomically with a new state-transition event.
The signature includes all three check statuses/exit codes, so a change from
stale to timeout is a new event even if the aggregate stays degraded.
Identical repeated observations increment the observation counter but **do not
create another event**. Recovery to healthy creates a separate event.

Events are retained (maximum 10,000 distinct transitions). At capacity, a new
transition is refused, never pruned automatically. Saved-time regression is
refused without replacing the prior state. External backups should preserve
the entire quiescent journal including SQLite metadata. A successful journal
record is local evidence only; it does not confirm that a human received an
alert, recognized it, or approved any action.

| Exit | Meaning |
|---|---|
| 0 | The requested journal record/check was completed |
| 2 | Invalid/unavailable arguments, input, journal or refusal policy |
| 4 | Storage or result-delivery uncertainty |

The bounded JSON response reports `event_created`, `event_sequence`,
`observation_count` and the current status on record; `check` reports the
last observation timestamp and total transitions. All reports include
`live_enabled=false`, `orders_submitted=false`,
`notification_delivered=false`, `production_ready=false`.

## Limitations and remaining gates

The journal does not cryptographically authenticate the observations or protect
against an attacker with the same UID and filesystem write access. The observed
file is a point-in-time snapshot; the monitor and journal operations are not
one transaction. Any process failure between them can leave a missing
observation, and application-level acknowledgement semantics have not been
implemented. This is not a fully reliable, independently delivered alerting
system. It must not be used to trigger automatic trades or automatic service
restarts.

Actual host acceptance still requires prolonged provider and service soak,
real notification delivery and acknowledgement, deduplication under retries,
restore from external backup media, process manager testing and verified
broker-authoritative reconciliation. Keep `is_production_ready()` false.
