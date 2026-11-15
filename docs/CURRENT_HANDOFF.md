# Trading continuation handoff — 2026-11-15 (v46)

## Resume point and authorized delivery

Repository: `uulab-official/trading`; feature `feat/smc-market-structure`;
[Draft PR #1](https://github.com/uulab-official/trading/pull/1).
The independently verified starting head was
`1b1f56b5a06438d16c55255601920ffb43b03f94`, tree
`0b45473aba8de0394b25e5c2a17867455a080420`. Main remains
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user requested continued production-quality implementation. This increment
adds a continuously advancing PAPER account on top of v45 public candle capture.
Implementation, verification, feature publication and Draft PR metadata updates
continue under existing authorization. Main merge, deployment, credentials,
real orders and external notifications are outside this delivery. No provider
or broker requests are required or performed by the verification workflow.

At turn start, independent remote inspection confirmed matching feature/main,
an open Draft/unmerged PR without auto-merge, exact prior metadata, and all four
v45 CI runs successful. Always read the actual refs and current PR when resuming;
do not infer final publication identity from this pre-publication handoff.

## New continuous-account contract

### Original incremental execution

`apps/server/trading_bot/paper_stream_engine.py` exposes `initial_state`,
`validate_state`, and async `advance_state`. It uses the unchanged original
PaperRunner strategy factory and pending sizing, PaperExchange and OrderManager.
A detached original book carries cash and holdings through each bar, emits at
most one exact original fill, and is discarded after returning a candidate.
There is no broker adapter and no repeated historical account reset.

The latest 50–500 source candles warm indicators only. The required minimum is
max(50, slow period) for MA or max(50, RSI period + 1). **All bootstrap signals
are discarded.** Cursor starts at the latest bootstrap opening; cash starts at
the configured initial balance, holdings/fees/fills are zero, pending is null.
The first possible fill comes from a post-activation signal and its next bar.

The explicit state retains the latest original 500-candle buffer, MA crossover
flags/streak or RSI previous value, cash/holding/equity/cumulative fees, fill
count, next index, cursor, pending signal and original risk fields. Restore the
bounded buffer directly; do not re-run the tail through the initial 50-bar gate.
Original risk semantics remain: UTC candle-opening day, daily baseline at open,
loss/drawdown checks at close, daily reset only, sticky drawdown, and allowed exits.

This is **delayed next-open simulation**. The next candle's open is learned after
that candle closes. A fill's timestamp is model event time; event `applied_at`
is the later consumer commit time. Neither is a real exchange execution or a
contemporaneously tradable quote. Risk gates do not guarantee a maximum loss.

Source/config numeric bounds follow the original importer (magnitude up to 1e18;
config exponent -18..18). Account numbers are finite, nonnegative, at most 100
characters, magnitude at most 1e100, exponent -100..100. Preserve original
Decimal formatting for account/fill quantities. Unsupported precision is refused
without changing the prior account. All 16 historical fingerprinted source files
remain unchanged; historical recovery formats are not migrated.

### Atomic persistence and authoritative recovery

`paper_stream_store.py` owns a new private directory with `stream.sqlite3` and
permanent `.stream.lock`. One POSIX owner holds exclusive flock. Parent, directory,
DB and lock identities/permissions are pinned, including checks after SQLite
connect before settings/init. Forked handles cannot inherit authority.

Fixed SQLite schema/application identity, DELETE rollback journaling, FULL
synchronization and directory fsync commit the input event, optional fill,
account/strategy/risk state, cursor, source watermark and hash linkage together.
Detached candidates have no durable effect before that transaction. An uncertain
write/commit/fsync outcome latches the owner unusable; retain artifacts and reopen
explicitly. Never retry an unconfirmed candidate from memory.

Every bootstrap and consumed input/fill remains archived. Limits are 100,000
post-bootstrap bars and a 256-MiB database, with a conservative write-capacity
reserve and SQLite's hard page ceiling. At capacity the account stops with its
state/evidence preserved. There is no automatic pruning, rotation, new funding or
account recreation. Head/manifest bodies are bounded at 512 KiB, events at 16 KiB;
reads use bounded batches, SQLite VM work and cooperative time limits.

Explicit resume first checks frozen imported code/Python major/minor fingerprint,
then replays the archived bootstrap and every event into a separate in-memory
verifier. It compares all fills and state digests, event/source chronology and
hash chain, then the complete final account/risk/strategy state. Only a matching
owned head is marked verified and allowed to consume new source data. Replay
uses bounded memory; startup work grows with the capped journal.

`audit` reuses that verifier without writes or repair. It holds a nonblocking
shared owner lock and **refuses an active writer**. Stop the consumer and wait
for exit first. Passive `check`/HTTP observation remains available during normal
operation; it validates the head structurally and is explicitly not a full audit.
Read-only observers refuse nonempty journals; only explicit writer resume may
perform SQLite rollback recovery.

Checksums and replay establish consistency with archived inputs, not authenticated
provider truth, protection against coherent privileged rewrites, or prevention of
independently copied account directories operating under separate locks.

### Source continuity, freshness and stopping

`paper_stream.py` freezes physical source binding, capture ID/config and initial
source progress. The account directory must be outside the capture. Source data
is read-only. Source revision is an observation watermark, never the bar cursor.

Each admitted source must be validated, running and current under both v45 age
budgets. Stale/fetching/retrying/stopped/unreadable source waits with no account
transition. Terminal source failure, replacement, counter/revision regression,
revised history or required cursor overlap eviction blocks consumption.

Compare all available source overlap against archived bootstrap/consumed events,
up to the capture's 5,000 retained rows; the 500-row indicator buffer is a separate
limit. Require exact next-open timestamp and source count progression. Normal
rolling eviction is allowed while cursor overlap remains. Never invent missing
candles, skip to a recent row, or execute a pending signal after a missing interval.

A fresh source read, pinned binding and wall clock reauthorize each detached
candidate before commit. Stops discard an uncommitted candidate or preserve one
complete committed bar. Handlers record numeric intent only and restore previous
handlers. Waits check intent in at most 0.1-second chunks. Regressed clocks never
become clamped successful timestamps. Terminal errors cannot be cleared by stop
or resume, including a terminal reason accompanied by a different saved phase.
Clock faults are latched before releasing the held writer. If no new time is
trustworthy, that terminal write retains the exact last trusted `updated_at`
and changes no engine/event/source state. It never refreshes a successful time.
The returned observation uses the actual regressed time, or is null if no valid
time exists. Restoring the source/clock cannot silently clear the refusal.

## Operator commands and passive API

Create the v45 capture separately and keep it in the foreground:

```bash
mkdir -p .private
chmod 700 .private
python tools/market_capture.py run --market KRW-BTC --unit 1 \
  --history 120 --retention 5000 --poll-seconds 15 \
  --directory .private/btc-capture-001
```

After `market_capture.py check` reports current, start a new continuous account
in another terminal:

```bash
python tools/paper_stream.py run --capture .private/btc-capture-001 \
  --directory .private/btc-stream-001 --strategy ma_cross \
  --fast-period 3 --slow-period 5 --initial-balance 1000000
```

Configuration, including simulated friction and risk values, is frozen at creation.
No live mode, credential or source override is accepted. Default invocation stays
in the foreground; `--max-polls N` bounds an invocation without creating a new
account on resume. Stops retain positions and pending signals; they do not sell.

```bash
python tools/paper_stream.py check .private/btc-stream-001
python apps/dashboard/server.py --paper-stream .private/btc-stream-001 --port 8765
```

The local endpoint is GET/HEAD `http://127.0.0.1:8765/api/paper-stream`. Valid
noncurrent status is HTTP 200; unavailable is a fixed private 503 envelope.
No query/body write operation or new browser page exists. Existing readiness
schema1 retains exactly journal/replay/research/paper_session; this route is an
independent head observation. Saved phase never asserts process liveness.

Stop the account writer and wait for exit before:

```bash
python tools/paper_stream.py audit .private/btc-stream-001
python tools/paper_stream.py resume .private/btc-stream-001
```

Resume uses the original source/configuration/runtime and full replay. It does
not repair a blocked source, reset holdings or migrate to new code. Preserve the
entire account and capture directories when investigating refusal/uncertainty.

| Command | Exit meanings |
|---|---|
| run / resume | 0 bounded success, 3 source wait/block/clock/capacity, 2 invalid input/account/fingerprint/audit or ownership refusal, 4 storage uncertainty, 130/143 signal |
| check | 0 current head, 1 valid noncurrent, 2 unavailable |
| audit | 0 verified prefix, 2 invalid/mismatch/busy owner, 130/143 interruption |

Each completed operation emits one JSON stdout document; fixed errors omit
paths and private exception text. Full operator/schema/limits details are in
[CONTINUOUS_PAPER.md](CONTINUOUS_PAPER.md).

## Verification and review status

Task implementation evidence is recorded in the ignored plan ledger. Early
integration proved indicator-only activation, causal next-open fills, persistent
one-account resume, duplicate polling, stale-source waits, source replacement,
retention loss, real source rollback, pre/post-commit signals, and unconfirmed
commit recovery. Digest-consistent forged cash/risk/strategy/pending snapshots
are refused by full replay. Original MA and RSI parity each covers 80 bootstrap
plus 720 future bars and the 500-bar rolling boundary.

Final whole-increment local gates after independent review repairs:

| Gate | Observed result |
|---|---|
| Root unittest discovery | 1,285 passed in 132.889 seconds; no skips |
| Package/server pytest | 309 passed plus 382 subtests in 14.19 seconds |
| Dependency-free Node clients | 103 passed in 289.757 milliseconds |
| Python 3.10 grammar | All 10 changed Python files parse |
| Historical execution compatibility | All 16 fingerprinted files byte-identical |

The pytest gate retains 926 existing deprecation warnings. New engine parity
tests cover both original MA and RSI; real CLI/API integration includes actual
SIGINT/SIGTERM and read-only source preservation. Store tests exercise actual
process death/hot journals and confirmed versus unconfirmed commit boundaries.
Additional original-arithmetic regressions preserve sell operation ordering and
accept valid tiny actual fill prices/fees without relaxing order quantity bounds.

Fresh independent review found a clock-fault/refusal persistence gap: a source
identity refusal followed by regressed time could return without a durable block.
Three RED→GREEN tests now cover clock-only restart, simultaneous source movement,
and an invalid/out-of-range clock. The owned-store terminal latch preserves the
last trusted timestamp and the received refusal before ownership ends. A fourth
RED→GREEN test fixes SIGTERM's exit code when source binding fails after audit;
refusal persists first, then the command returns the signal status. The reviewer
independently verified the repaired cases and reported no remaining material or
minor finding. The original review checkpoint `92b591a` must not be published.

Additional independent temporary-directory checks confirmed retention loss during
resume verification and revised retained history outside the current 500-bar
indicator window are refused with the engine preserved. The full final regression
suite above includes the four committed review repairs.

A network-forbidden synthetic integration used 120 indicator-only bootstrap bars,
then consumed 120 new bars across two explicit resumes of one account. Capture
accepted 240 bars and evicted 40 while retaining 200. The account produced 18
simulated fills. Full archived audit returned `verified_prefix`; exact fills,
cash/holdings/fees and pending/risk state matched the original warmed PaperRunner.
Each consumer invocation left source DB bytes unchanged, and audit left account
DB bytes unchanged. Private receipts are in the ignored plan workspace. This
is controlled integration evidence, not provider soak or profit validation.

Final-head GitHub Actions and feature/main/Draft/metadata identity are verified
by the publication step after this immutable handoff checkpoint and recorded in
the PR and ignored delivery receipt. Local gates are not substituted for those
remote gates or for operational deployment/actual execution readiness.

## Remaining production work and evidence

Continuous PAPER account consumption is now implemented. The 100,000-bar archive
capacity is explicit; operational archival/migration to continue beyond it is not
implemented. Full-replay startup latency grows with account history; no worst-case
100,000-bar recovery SLA is established. Source loss or code/runtime changes require
investigation and an explicit future migration/recovery design, not an auto reset.

Real-provider/network/clock/disk soak, actual supervised service deployment and
rollback, shared-IP measurements, external alerts, and backup/restore drills remain
unverified. Broker-authoritative reconciliation, credentials and real execution
are absent. Legacy live/Rust/Compose components are not an integrated verified
service. `is_production_ready()` remains false.

Actual browser visual/accessibility QA remains unverified. The prior unavailable
browser/refused local-file boundary was not bypassed; HTTP/Node tests are not
visual inspection. No profitability, Windows/all-filesystem certification,
cryptographic provider authenticity or real execution readiness is claimed.

Design: [v46 spec](superpowers/specs/2026-11-15-continuous-paper-design.md).
Plan: [v46 plan](superpowers/plans/2026-11-15-continuous-paper.md).

## Publication discipline

Publish the exact tested tree to the existing feature ref with an expected-head
lease. Preserve main and Draft/unmerged/no-auto-merge state. Verify all applicable
PR and push workflows on the final head, inspect skipped image/release publication
jobs, then update only PR title/body after a fresh metadata comparison. Save exact
commit/tree, metadata hashes and CI receipts locally and independently verify the
final remote state. Do not force-push published history.
