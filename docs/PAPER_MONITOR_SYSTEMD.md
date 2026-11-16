# Inert PAPER monitor systemd timer review (v56)

`tools/paper_monitor_systemd.py` is a **pure renderer** of an example
systemd **user** service/timer pair for the existing v54
`paper_monitor_tick.py`. It outputs the two unit contents in JSON.
It creates no file, installs nothing, enables no timer, launches no
worker, submits no order and delivers no external alert.

The v55 gate reports stale snapshots and unacknowledged previous faults, but
it must be scheduled **separately** if an operator wants regular checks.
This v56 renderer focuses only on periodically collecting and journaling
three-source status; it does not install or schedule the v55 gate, nor
does it implement notification delivery or escalation.

## Render an inactive unit pair

Review the exact runtime and the paths for the operator's host:

```bash
python3 -I -S -B tools/paper_monitor_systemd.py \
  --python /usr/bin/python3 \
  --tool /absolute/reviewed/trading/tools/paper_monitor_tick.py \
  --service /absolute/private/btc-service \
  --capture /absolute/private/btc-capture \
  --account /absolute/private/btc-account \
  --directory /absolute/private/paper-monitor-history \
  --unit-name paper-monitor \
  --interval-seconds 60 \
  --timeout-seconds 10
```

It returns one JSON object containing the strings
`paper-monitor.service` and `paper-monitor.timer`, and explicit
`installed=false`, `enabled=false`, `activated=false`,
`external_notification_delivered=false`, `broker_orders=false`,
`production_ready=false` boundaries.

Only absolute paths composed of a restricted ASCII character set
(`A-Z a-z 0-9 _ . / -`) are accepted. No spaces, percent substitutions,
quotes, shell metacharacters, escaped commands, symlink canonicalization,
relative paths, or `..` components are allowed. This is a template,
**not** a path/host capability check. The reviewed tool path must end
in `/tools/paper_monitor_tick.py`. A real operator must verify all the
given paths and execute the host preflight before using the units.

The rendered oneshot unit uses Python `-I -S -B`, no shell,
`NoNewPrivileges=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`,
`ProtectSystem=strict`, journal-only `ReadWritePaths`,
`RestrictAddressFamilies=AF_UNIX`, memory/task/time limits and
`UMask=0077`. The timer uses `OnBootSec` and `OnUnitActiveSec`
with a configurable interval (30–3600 seconds) and
`Persistent=false`, so missed periods are not backfilled.
The per-probe timeout is 1–30 seconds and the timer interval must exceed
the worst-case three probes plus margin.

## Host verification and operator acceptance

These renderings are **not activated**, not service-manager tested on the
actual operator host, and not proof that a specific Linux distribution
supports all security directives in a user manager. Review the two
generated strings before writing separate unit files. Validate with
`systemd-analyze verify`, check private directory permissions and
service access, and test every ordered stop/fault scenario on the
intended host.

The sample oneshot exits nonzero when the native observation is degraded
(exit 1) or the journal cannot be verified (2/4). The timer itself does
not deliver alerts or escalate errors. The operator must separately
configure, verify and monitor notification delivery and acknowledgements.
Failure to record a tick can leave the last saved observation stale; use
the v55 gate to detect this independently.

**Do not simply enable unattended operation from successful source CI.**
The project has no certified live broker execution, real-provider soak,
actual-host rollout/rollback, external backup restore or accepted alert
delivery. This renderer is intentionally excluded from the fixed v49
source runtime archive and cannot make `production_ready` true.
