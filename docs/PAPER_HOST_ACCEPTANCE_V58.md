# v58 — PAPER operator-host acceptance and evidence protocol

> Status: **not accepted**. This protocol is a checklist, not proof of a successful host installation, unattended operation, live brokerage, profitability, or data authenticity.

## Scope and hard boundaries

- Repo: `uulab-official/trading`, review branch `feat/smc-market-structure`. Work only from a pinned reviewed commit and record the SHA and Python version in the evidence.
- Keep `is_production_ready() = False`, PAPER-only execution, no broker credentials, no real orders, no public dashboard, and no automatic worker restart.
- The default evidence path must be *outside* native account/capture/service and monitor journal directories. Use a dedicated owned `0700` parent; never copy private account/monitor data into public CI artifacts.
- A green synthetic drill or GitHub workflow does not certify the intended host. All host acceptance steps below are initially **unverified**.

## Evidence manifest (filled by the operator, not generated as successful by this document)

Record: test identifier; UTC start/end; reviewed source SHA; Python implementation/minor; Linux/kernel/architecture; filesystem and mount options; service/capture/account/journal **identifiers** (avoid raw sensitive paths in shared logs); command + exact exit code; sanitized stderr/stdout hash; storage/clock/network fault injected; independently observed outcome; reviewer; decision PASS/FAIL/NOT_RUN and corrective-action link.

A PASS must include an observed outcome **on the intended host**. Do not mark a skipped, mocked, timed-out, unavailable, or unconfirmed outcome as PASS. Retain original failed attempts.

## A. Host capability and isolated PAPER source release

1. Provision a new, empty owner-only `0700` probe directory, not inside native stores. Run `python3 -I -S -B tools/paper_host_preflight.py --directory /absolute/private/probe`. Record all seven capability results, tool exit and runtime fingerprint.
2. Independently review `docs/PAPER_RELEASE.md` and the source archive's externally pinned SHA-256. Build/check/extract/verify into new private outputs on the target host. Confirm no unexpected source file and exact runtime compatibility. The release does not migrate an account or activate a service.
3. **Fail** if Unix sockets, descriptor access, flock, SQLite WAL, exact permissions, hash verification or pinned-code compatibility are unavailable.

## B. Existing continuous-paper pair and supervisor

1. With the operator present, verify the capture, stream account and service refer to the **same** immutable source binding. Record the read-only output from `market_capture.py check`, `paper_stream.py check` and `paper_service.py check`; these are different readiness signals.
2. Only after manual review, run a supervised PAPER pair on an existing compatible account in the foreground according to `docs/PAPER_SERVICE.md`. No real orders. Record both direct-worker progress, process ownership, freshness and account audit separately.
3. Exercise graceful stop, unexpected worker failure, parent termination and a genuine long-held exclusive lease. Confirm no child is orphaned and no automatic respawn/second writer occurs. Preserve the incomplete-attempt refusal.
4. Inject stale capture, history gap, clock shift, denied disk write and interrupted persistence in a disposable PAPER setup. Require fail-closed behavior and immutable evidence. Never edit account control metadata to clear a refusal.

## C. Monitor, incident review and staleness

1. Run `python3 -I -S -B tools/paper_monitor_drill.py`. Require `verified_synthetic`, exactly three transitions and five stages. This validates only the synthetic contract.
2. On the host, record a fresh `paper_monitor_tick.py` observation against the reviewed service/capture/account/journal pair. Run `paper_monitor_gate.py --directory /absolute/private/journal --max-age-seconds 120` using the operator's validated age budget.
3. Simulate: healthy repeated tick without extra event; missing tick past threshold; capture degradation; recovery **before** fault ACK; manual ACK; duplicate ACK refusal; journal/queue inconsistency. Gate must refuse staleness and unacknowledged degraded incidents even after recovery, and must not accept partial source observations.
4. Review the text emitted by `paper_monitor_systemd.py`; it only renders **inactive** unit text. Validate actual units with the intended systemd manager and `systemd-analyze verify` before any separately approved manual install. Validate boot/logout persistence and rollback/disable in a contained PAPER environment; never infer them from CI.
5. No notification delivery, human receipt or escalation can be marked PASS from the local queue. Use a separately approved external notifier, demonstrate durable transport, independent recipient receipt, controlled retry/deduplication and escalation when ACK is absent, then disconnect it during failure drills. Until implemented, mark the external-notification gate **NOT_RUN**.

## D. Backup/recovery and sustained operation

1. Stop native owners first. Create/check/verify-restore a **new** private paired bundle per `docs/PAPER_BACKUP.md`. Confirm independent storage media, retention, readability and expected SHA-256 *after transport*. Never treat the verified non-executing `.bin` bundle as an authorized runnable restored account.
2. Perform a separate host-disaster restore rehearsal in an isolated environment, with an explicit latest-state/old-owner-retirement contract before permitting **any** resumed writer. If this contract is absent, mark executable recovery **NOT_RUN**.
3. Capture 24h/72h/7d host-soak windows as separate evidence goals, not precompleted thresholds. Track source gaps/revisions, observation age, poll jitter, service restarts, CPU/RAM, disk growth, clock skew, error exits and operator response. Do not extrapolate short tests into long-duration success.
4. Exercise capacity planning near the documented 100,000 consumed-bar / 256 MiB limit on disposable data; preserve refusal and no silent account truncation.

## E. Independent live-trading gates (explicitly out of scope)

Before reconsidering live orders, design and verify a broker-authoritative reconciliation protocol covering client order keys, unknown submission outcome, partial/cancelled fills, fees, balances, external/manual trades, restart, rate limits, exchange timeouts, and position/ledger disagreements. Establish independently enforced max exposure, loss ceilings, kill-switch authority, least-privilege secrets and operational approvals. None of these is established by a PAPER backtest or synthetic monitoring drill.

## Go/no-go

| Gate | Starting decision |
|---|---|
| Offline synthetic monitoring integration | Verify against exact feature-head CI; **not host acceptance** |
| Intended-host source/runtime capabilities | NOT_RUN |
| Capture/account/service real soak and lifecycle | NOT_RUN |
| Monitoring scheduler + actual alert delivery/ACK/escalation | NOT_RUN |
| External-media backup and authorized disaster recovery | NOT_RUN |
| Independent broker reconciliation and live risk controls | NOT_IMPLEMENTED / NOT_RUN |
| Public dashboard authentication and browser acceptance | NOT_RUN |
| Live-production authorization | **DENIED** |

**Decision rule:** An unknown or failed critical gate blocks unattended deployment; the live-production authorization remains DENIED regardless of a passing PAPER-only gate. Preserve Draft PR #1 and do not merge into `main` as a substitute for verification.
