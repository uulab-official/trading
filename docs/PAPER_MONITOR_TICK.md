# Atomic one-shot PAPER monitoring (v54)

The operator-invoked `paper_monitor_tick.py` connects the existing v51
three-source status checks with the v52 observation history and v53 local ACK
queue. **The saved monitor observation and alert queue synchronization now
commit in one SQLite transaction.** A failed queue consistency check rolls
back the corresponding new observation rather than leaving an orphan event.

This is an explicit one-shot command. It does not create a systemd timer, send
an external alert, restart a worker, call an exchange, place an order or set
production readiness.

## Run one full observation

Operate from the reviewed source checkout with the same compatible Linux
CPython environment as the PAPER runtime. The journal must be in an existing
owner-controlled, private 0700 directory; supply the actual absolute paths for
the already configured supervisor, its capture and its account.

```bash
python3 -I -S -B tools/paper_monitor_tick.py \
  --service /absolute/private/btc-service \
  --capture /absolute/private/btc-capture \
  --account /absolute/private/btc-account \
  --directory /absolute/private/paper-monitor-history \
  --timeout-seconds 10
```

The tool invokes the existing *read-only* check commands once each. It
validates all three results using the native monitor observation contract,
then opens/initializes the private v52 SQLite journal and performs
`record_in_transaction` followed by v53 `_sync` before a single commit.

On success, JSON contains the status of each probe, whether an event was
created, the event sequence, cumulative observation count, and number
of pending operator acknowledgements. Repeated identical observations only
increment the count. A changed fault or recovery creates a new event. An
inconsistent or missing source history blocks the whole transaction and
preserves the existing journal/queue state.

| Exit | Meaning |
|---|---|
| `0` | The three requested native probes reported their exact healthy states and the single journal/queue transaction completed |
| `1` | At least one probe was degraded or timed out; the complete observation was nevertheless recorded and queued |
| `2` | Invalid inputs, incompatible observation or explicit consistency/policy refusal |
| `4` | Storage or output uncertainty; inspect the journal before any further decision |

The `--timeout-seconds` limit (1–60) is applied per check. Failure of the
service progress probe does not imply stale market data, and a current
captured-price observation does not imply service liveness. The separate
account head is not an economic full-history audit.

Use `paper_alert_queue.py list` to view pending local events and
`paper_alert_queue.py ack --sequence N --reason ...` for deliberate review.
ACK means **reviewed**, not fixed or delivered. There is no external
notification delivery or automatic escalation.

## Operational constraints

- The source status checking itself precedes the journal transaction and
  therefore the three observations are not a single simultaneous physical
  snapshot; only the resulting journal and queue commit is atomic.
- Run the tool in a trusted operator context on a local POSIX filesystem.
  An attacker with the same UID can alter monitoring or database files.
  The paths must correspond to the same configured service/capture/account
  pair; the tool does not prove that binding independently.
- Timeouts are not permission to automatically restart workers. Capture
  errors and native refusals remain authoritative.
- A failed JSON stdout delivery cannot undo a transaction that already
  committed. Inspect the journal and queue before retrying.
- Use a new/private journal, backup its WAL database coherently when stopped,
  and observe the 10,000 distinct-transition limit. Avoid an unattended
  scheduler until actual host soak/alert delivery acceptance.
- This v54 utility and refactoring are **not** included in the fixed v49
  55-file source release. The underlying frozen PAPER strategy/economics,
  provider, broker, order and readiness behavior has not changed.

See [PAPER_MONITOR.md](PAPER_MONITOR.md),
[PAPER_MONITOR_EVENTS.md](PAPER_MONITOR_EVENTS.md),
[PAPER_ALERT_QUEUE.md](PAPER_ALERT_QUEUE.md), and
[PRODUCTION_CHECKLIST.md](../PRODUCTION_CHECKLIST.md).
