# PAPER operator alert queue (v53)

This is a **local-only acknowledgement queue** on top of the v52 SQLite
`paper-monitor-events.sqlite` journal. It does not send notifications,
create monitoring schedules, place orders, restart workers, or establish
production readiness. Explicit human acknowledgement does not assert that
a fault was repaired.

## Usage

The monitor journal must first be created with `paper_monitor_events.py record`
using a full three-source status snapshot. With the same local CPython and
a compatible private owner-controlled Linux directory:

```bash
# Copy complete, consistent monitor history into the queue (no network):
python3 -I -S -B tools/paper_alert_queue.py sync \
  --directory /absolute/private/paper-monitor-history

# List the first 100 unacknowledged events:
python3 -I -S -B tools/paper_alert_queue.py list \
  --directory /absolute/private/paper-monitor-history

# Explicit operator acknowledgement with an audit reason:
python3 -I -S -B tools/paper_alert_queue.py ack \
  --directory /absolute/private/paper-monitor-history \
  --sequence 1 --reason "operator checked logs"
```

`sync` checks the original monitoring history's count, contiguous sequence
and current head. It copies up to 10,000 recorded transitions into its own
`alert_queue` table under one `BEGIN IMMEDIATE` SQLite transaction. A
repeated sync is idempotent. It refuses a previously synced event whose
original status, signature or observation timestamp was modified.

`list` and `ack` refuse a queue that is not caught up with the current
monitor journal. `list` exposes at most 100 pending entries and a total count.
`ack` requires an exact sequence and a bounded human reason (8–200 characters);
the first acknowledgement is saved with UTC time. Repeat acknowledgement and
unknown sequences are refused. Both degraded states and recovery transitions
appear in the queue: an ACK refers to **review**, never automatic recovery.

Output is a single JSON object with a non-activation/production-ready false
boundary. Exit 0 means the requested local journal action succeeded; exit 2
means invalid/unavailable/refused inputs or records; exit 4 means a surprising
failure, storage or output uncertainty. Retain all evidence for nonzero exits.

## Important limits

- The queue lives **inside the same SQLite database** as monitoring events,
  not a broker or execution store. The directory and DB must be owner-owned
  mode 0700/0600; original database creation is never done by this tool.
- WAL/FULL transactional updates protect logical consistency among cooperating
  processes. They do not prove physical-disk durability after hardware failure,
  authenticate a user, provide cross-host replication or protect against a
  same-UID attacker with write access.
- The source event journal and queue are only consistent at the moment of
  a successful sync. The separately executed monitor snapshot, original
  recording and queue sync are not one atomic transaction.
- Acknowledgement is not external notification delivery, customer receipt,
  incident escalation or resolution. There is **no automatic retry**, cleanup,
  deletion, alert deduplication across hosts or time-bound escalation.
- Freeze writers and back up all SQLite WAL-related files together on the
  intended host; perform restoration drills before relying on it.
- New v53 tools are **not** in the fixed v49 minimal release archive.
  Use the reviewed source checkout and compatible environment.

Actual operations readiness still requires an approved notifier and confirmed
delivery/acknowledgement path, host soak and network/disk/clock faults, real
provider continuity, production manager deployment/rollback and
broker-authoritative reconciliation. Keep `is_production_ready()` false.
