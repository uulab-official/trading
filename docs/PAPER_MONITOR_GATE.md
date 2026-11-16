# Read-only PAPER monitoring freshness gate (v55)

`tools/paper_monitor_gate.py` is a local, operator-invoked **read-only**
acceptance check for the existing v52 monitor history and v53 ACK queue.
It does not connect to a provider or broker, schedule jobs, send notifications,
start services, restart processes or place orders. It cannot establish overall
production readiness.

## Run

Use a reviewed checkout of the PAPER tools on the actual Linux operator host
and an owner-private (0700) monitoring history directory.

```bash
python3 -I -S -B tools/paper_monitor_gate.py \
  --directory /absolute/private/paper-monitor-history \
  --max-age-seconds 120
```

The threshold must be between 30 and 86,400 seconds. The operator should
choose a threshold based on the **actual** scheduled monitoring period, host
load and upstream observation latency. It is not a freshness guarantee about
exchange prices: the original three native checks provide their own meanings.

A healthy gate requires ALL of the following:

- A readable, owner-private existing SQLite journal with the original
  v52 history fully synchronized into the v53 queue, including agreement
  between the state timestamp and the final retained event timestamp.
- A current `healthy` three-source observation, with a timestamp no more
  than the configured age threshold in the past and not in the future.
- **Zero unacknowledged degraded events**, including faults followed by a
  healthy recovery observation. A recovery event never silently ACKs the
  original failure.

A healthy baseline/recovery event can itself remain pending operator review.
The result reports the total pending count separately from unresolved faults.
A human ACK records review only, not proof of a resolved incident.

The output is a bounded JSON observation with `reasons`, snapshot timestamp,
age, event and observation counts, pending acknowledgements and unacknowledged
fault count. It never changes the monitor history or queue.

| Exit code | Meaning |
|---|---|
| `0` | The saved snapshot is within the age window, healthy and has no unacknowledged faults |
| `1` | A valid observation was stale, degraded or had a pending fault |
| `2` | Invalid/missing/inconsistent private journal, unsupported timestamps or arguments |
| `4` | Unexpected failure or failure to confirm JSON output delivery |

A missing database is an error; the gate does **not** create one. A monitor
recording that stops without a new event will age into `stale_observation`,
even if its last successful status was healthy. A timestamp in the future is
refused, not interpreted as a fresh healthy sample. The gate uses UTC-aware
saved time but is still dependent on the accuracy of the host wall clock.

## Operator acceptance

Before using this from a monitoring scheduler, test the following on the
real host: a missed monitoring tick, sustained service degradation,
recovered-but-unacknowledged fault, explicit ACK, time rollback, lost journal,
journal tampering, failed SQLite reads, and external backup restoration.
Run both native status checks and the gate with the intended permissions and
operating filesystem.

A successful gate does **not** assert that any external notification was
delivered or read, that the capture truly tracks the real-time market,
that a position is financially safe, or that the system may execute live
orders. The source monitor tick, recording and human acknowledgement remain
independent operations. The gate is separate from the frozen v49 minimal
source release; execute it from this reviewed checkout.

See [PAPER_MONITOR_TICK.md](PAPER_MONITOR_TICK.md),
[PAPER_MONITOR_EVENTS.md](PAPER_MONITOR_EVENTS.md),
[PAPER_ALERT_QUEUE.md](PAPER_ALERT_QUEUE.md), and
[PRODUCTION_CHECKLIST.md](../PRODUCTION_CHECKLIST.md).
