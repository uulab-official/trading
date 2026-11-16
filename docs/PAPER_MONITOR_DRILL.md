# Offline PAPER monitoring incident/recovery drill (v57)

`tools/paper_monitor_drill.py` is a **synthetic, standalone operator
acceptance drill** that exercises the actual v52 observation recorder, v53
operator ACK queue and v55 freshness/unresolved-fault gate. It uses a
private throwaway directory and private generated JSON snapshots.

The drill never touches a real PAPER account, service or exchange capture,
starts a worker, connects to an external provider, places orders, or sends
a real notification. It does not install systemd timer units or create
credentials. It is specifically not a production-readiness certificate.

## Run

From the reviewed trading source checkout on Linux CPython 3.10+:

```bash
python3 -I -S -B tools/paper_monitor_drill.py
```

No account directory, provider market, credentials, webhook or execution
arguments are accepted. The tool uses the three adjacent reviewed Python
tools in the same `tools/` directory and does not import them from a
search path or install packages.

One JSON object is returned. Exit 0 indicates the synthetic exercise
passed; exit 2 means an unavailable input, invalid result or refused
contract; exit 4 means an unexpected failure/output uncertainty. Failures
do not claim that source stores were modified. The throwaway test history
is deleted after execution, so store external CI logs separately as evidence.

## Expected scenario

1. Record a synthetic **healthy** three-source observation. Synchronize
   the local alert queue and require the freshness gate to pass. Record
   the **same healthy observation again**, without adding a transition,
   and require the gate to remain healthy. This protects against confusing
   the most recent observation time with the last state-change time.
2. Record a synthetic capture **degraded** observation. Before syncing
   the queue, require the gate to refuse an incomplete history. After
   sync, require a degraded gate result containing both current fault and
   unacknowledged-fault reasons.
3. Record synthetic **recovery** and synchronize the queue. Require that
   the gate still fails because the previous fault remains unacknowledged,
   even though the latest source state is healthy.
4. Explicitly ACK exactly the synthetic fault sequence with a bounded
   reason. The gate must now pass; duplicate ACK must be refused.
5. Read the original journal and verify exactly **three distinct events**
   and **four observations** (including the unchanged healthy repeat).
   Return a structured list of five verified stages.

The drill validates the **interaction** between existing persisted-state
tools, rather than only testing a mock implementation. It does not exercise
the actual exchange's network continuity, the systemd runner, external
notification delivery or real host clock/disk faults.

## CI and operator acceptance

The dedicated
[`.github/workflows/paper-monitor-drill.yml`](../.github/workflows/paper-monitor-drill.yml)
runs the full drill on Linux CPython 3.10, 3.12 and 3.13 without installing
third-party packages. The test must run against actual adjacent tools and
cannot be replaced by a simulated provider or out-of-tree state.

A green run provides **synthetic contract verification** only. Before
unattended PAPER operation the operator must still demonstrate actual
systemd activation, failure handling, live data freshness, network/disk/
clock soak, real delivered and acknowledged external alarms, external
backup restoration and verified rollback. The live trading path separately
requires broker-authoritative order/fill/position reconciliation and
risk controls. Until independently demonstrated, keep
`production_ready=false` and do not activate live orders.

## v55 historical timestamp regression correction

The saved `state.observed_at` advances on every observation, even when its
status does not change. The latest `events.observed_at` advances only when
the status/signature changes. They must **not** be required to match.
The fixed v55 gate requires the latest observation to be at or after the
latest transition and checks its own age against the wall clock. A saved
observation earlier than the last transition is refused. The v57 synthetic
drill exercises a duplicate healthy observation to prevent a regression.
