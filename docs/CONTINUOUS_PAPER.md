# Continuous paper account consumption (v46)

This foreground consumer continues one local paper account as the existing
public candle capture gains closed bars. The account, pending strategy signal,
risk state, cursor and financial event for each consumed bar commit together.
An explicit restart verifies the complete archived economic history before
continuing the same account.

This is **delayed next-open simulation**. A signal from one completed candle
can execute at the following candle's opening price only after that following
candle has closed and been captured. The fill timestamp is model event time;
the event's `applied_at` records the later observation and commit time. This
does not provide a timely quote, broker execution or a live trading service.

The operator surfaces are the CLI below and a passive local JSON API. This
increment adds no browser page or execution controls to the dashboard.

## Prerequisites

Use Python 3.10 or newer, the existing repository packages and an owned local
POSIX filesystem. Run from the repository root:

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
mkdir -p .private
chmod 700 .private
```

The consumer makes no provider request. Its separate capture process contacts
the public Upbit endpoint; [MARKET_CAPTURE.md](MARKET_CAPTURE.md) describes that
collector's transport, continuity, retention and refusal contracts. Credentials,
broker account access, real orders, external notifications and deployment are
outside this workflow.

## Start a current source and a new account

In the first terminal, create a fresh capture with enough initial history:

```bash
python tools/market_capture.py run --market KRW-BTC --unit 1 \
  --history 120 --retention 5000 --poll-seconds 15 \
  --directory .private/btc-capture-001
```

Leave the collector running. In a second terminal, check its local state:

```bash
python tools/market_capture.py check .private/btc-capture-001
```

Proceed when it returns `status=current` and exit code `0`. A successful
bounded capture saves `stopped`; it must be explicitly resumed before it can
supply current data to the consumer:

```bash
python tools/market_capture.py resume .private/btc-capture-001
```

Then, in a separate terminal, create the paper account in a new directory:

```bash
python tools/paper_stream.py run \
  --capture .private/btc-capture-001 \
  --directory .private/btc-stream-001 \
  --strategy ma_cross --fast-period 3 --slow-period 5 \
  --initial-balance 1000000 --poll-seconds 5
```

The new account directory must be **outside the capture**, not the capture
directory itself or any descendant. Use sibling directories as shown above.
Creation never overwrites an existing destination, including an empty directory
or a failed earlier attempt. Keep the consumer in the foreground. Record its exit
result through the supervisor or terminal you operate; a saved phase does not
establish that a process is alive.

### Activation and bootstrap

A new account takes the latest at most 500 contiguous retained candles. It
requires at least `max(50, slow_period)` for MA crossover or
`max(50, rsi_period + 1)` for RSI. The sample capture provides 120 bars, enough
for the sample MA settings. A longer configured indicator requires sufficient
retained history before activation.

Bootstrap feeds the original strategy to initialize its indicator state, then
discards **every historical signal**. Initial holding, total fees and fill count
are zero, and there is no pending order. Initial cash is the chosen balance.
The cursor starts at the last bootstrap candle. The first possible fill comes
from a post-activation signal followed by a subsequent bar; bootstrap never
creates retroactive trades.

### Options frozen at creation

Only `run` accepts source, strategy, cost, risk and polling settings. The source
determines the KRW spot symbol and timeframe. There is no live mode or symbol
override.

| Option | Default | Contract |
|---|---|---|
| `--capture` | Required | Existing current capture directory |
| `--directory` | Required | New private account directory |
| `--poll-seconds` | `5` | Integer `5..300`; frozen in the account |
| `--max-polls` | Foreground loop | Optional integer `1..10000`, for this invocation only |
| `--strategy` | `ma_cross` | Original `ma_cross` or `rsi` |
| `--initial-balance` | `1000000` | Positive finite KRW cash |
| `--position-size-pct` | `20` | Positive percentage, at most 100 |
| `--fee-pct` | `0.1` | Nonnegative percentage below 100 |
| `--slippage-pct` | `0.05` | Nonnegative percentage below 100 |
| `--max-daily-loss-pct` | `3` | Positive percentage, at most 100 |
| `--max-drawdown-pct` | `10` | Positive percentage, at most 100 |
| `--fast-period` / `--slow-period` | `3` / `5` | Positive integers, fast < slow, slow <= 500 |
| `--rsi-period` | `14` | Positive integer below 500 |

These defaults are implementation defaults, not recommended trading parameters.
Original execution friction and daily/lifetime risk rules apply. Risk halts
prevent new buys and permit exits; they do not liquidate holdings automatically.

To bound a new-account invocation deliberately:

```bash
python tools/paper_stream.py run \
  --capture .private/btc-capture-001 \
  --directory .private/btc-stream-bounded-001 --max-polls 4
```

A successful bounded run records `stopped`; it does not automatically restart.
Polling the same candle again cannot produce another economic event.

## Observe the account without advancing it

```bash
python tools/paper_stream.py check .private/btc-stream-001
```

The result reports the account and cursor, pending/risk state, saved consumer
control, last source progress and observation ages. Decimal values are strings.
A current result is a bounded observation at its reported time, not proof of
process liveness, a full economic audit or authorization to submit orders.

The consumer admits new work only from a capture snapshot whose phase is
`running` and whose data and observation ages both satisfy the capture's
freshness bounds. It uses that same validated snapshot for freshness and bars,
then checks freshness and pinned source identity again before committing.

Stale, fetching, retrying, stopped or temporarily unreadable source data makes
the account wait without an economic mutation. A failed observation cannot
authorize work from an earlier cached snapshot.

### Passive local API

```bash
python apps/dashboard/server.py \
  --market-capture .private/btc-capture-001 \
  --paper-stream .private/btc-stream-001 --port 8765
```

Read `http://127.0.0.1:8765/api/paper-stream` locally. The endpoint supports
GET and HEAD and accepts no query parameters. It returns HTTP `200` for a
valid observation, including a noncurrent one. An unconfigured, unavailable or
invalid account returns `503` with a fixed private error. HEAD performs the
same observation and sends no response body.

The existing server's loopback binding, Host/Origin checks, connection limits,
request budgets, no-store policy and read-only method rules apply. It has no
remote authentication and must remain local. The readiness schema remains
version 1 with exactly `journal`, `replay`, `research` and `paper_session`;
continuous paper and public capture each have an independent observation route.
A readiness result is not a stream freshness or account audit result.

## Stop, audit, then resume

Use Ctrl-C or SIGTERM to request a cooperative stop. Signal handlers only record
numeric intent. Work calculated but not yet committed is discarded; a confirmed
commit remains part of the durable account. A signal cannot clear a saved
blocking reason.

A full audit deliberately **refuses an active writer**. Stop the consumer, wait
for it to exit, then run:

```bash
python tools/paper_stream.py audit .private/btc-stream-001
```

A successful audit returns `status=verified_prefix` and exit code `0`. It
acquires the stopped account's shared audit lock, replays the archived bootstrap
and every consumed event through the original execution primitives, compares
fills and state digests, verifies the hash chain and checks the complete final
engine state. It never writes fills back into the account. Cancellation is
checked between events. Runtime grows with the bounded journal; working memory
does not grow with the full event history.

Audit validates the saved prefix. It does not make the source current, prove
provider authenticity or promise the account can resume after retention loss.
Passive `check` and the HTTP route perform bounded structural head validation;
they do not substitute for this full economic verification.

To continue after reviewing the account and source:

```bash
python tools/paper_stream.py resume .private/btc-stream-001
```

An explicitly bounded restart is:

```bash
python tools/paper_stream.py resume .private/btc-stream-001 --max-polls 2
```

Resume accepts no source, strategy, balance, friction, risk or polling override.
It verifies the full archived prefix **before any new bar**, then reads the
source again because capture retention may have advanced during verification.
It continues the existing account and pending signal without resubmitting any
saved fill.

The manifest freezes SHA-256 fingerprints of the imported execution, strategy,
data, capture, storage and consumer modules, plus Python implementation and
major/minor version. Resume requires the same fingerprint. Changing Python
3.10 to 3.12, or changing a fingerprinted source file, is not an automatic
migration. Preserve the original runtime and evidence; investigate a refusal
before planning a separately designed migration.

## Continuity, refusal and durable storage

The source's canonical path, capture ID, configuration and physical
parent/directory/database/lock identities are frozen. Replacing or relocating a
capture is not a way to substitute new history. Source descriptors stay pinned
and are checked around reads and before economic commits.

Source revisions and accepted/evicted counters must not regress. The same
revision must represent the same snapshot. Every available overlapping candle
must agree with the consumer's archived bootstrap/consumed history, including
its required cursor overlap. This checks all available overlap across the capture's
retained window of up to 5,000 rows, using indexed archived bootstrap/consumed
history; it is not limited to the latest 500-bar indicator window. Each new bar
must be the exact following interval. The consumer may catch up a finite
contiguous batch in order; it cannot jump a gap.

| Saved condition | Operator meaning |
|---|---|
| `source_unavailable` / `source_not_current` | Waiting; investigate availability/freshness before assuming progress |
| `source_identity_changed` | Source replacement or binding change; preserved terminal refusal |
| `source_history_conflict` / `source_regression` | History or progress is inconsistent; preserved terminal refusal |
| `retention_lost` | Required overlap is gone; the account cannot safely bridge the missing history |
| `source_blocked` | Capture has a terminal condition; saved terminal errors are refused even if its phase says stopped |
| `clock_regression` | UTC ordering is unreliable; timestamps are not clamped into success |
| `engine_rejected` | Engine rejected a proposed transition; preserve the saved prefix |
| `capacity` | Archive capacity reached; continuation needs separately designed archival/migration work |

When the clock regresses or cannot produce a valid timestamp, the owned store
persists a terminal fault while retaining `control.updated_at` exactly as the
last trusted time. This is a failure latch, not a fresh heartbeat or a successful
time clamp. Any already observed terminal source refusal is retained. The command
reports the actual regressed time; if no valid observed timestamp exists,
`observation` is null. Restoring the source or wall clock cannot clear that latch
on a later resume. Signal exit codes remain 130/143 while the refusal stays saved.

Account storage uses `stream.sqlite3` and permanent `.stream.lock` inside an
owned `0700` directory. Regular files are `0600`, have one link and are checked
against pinned ownership/identity. A cooperating second writer is refused.

Each accepted input bar, optional fill, full strategy/account/risk state, event
hash and cursor commits in one SQLite transaction using DELETE rollback
journaling and synchronous FULL, with directory synchronization. Detached
calculation has no durable side effect. An unconfirmed commit or directory
synchronization failure stops the owner immediately; it does not retry the
candidate. An explicit reopen determines the actual durable prefix.

Read-only observation never repairs a nonempty recovery journal. Explicit
writer resume may perform SQLite rollback recovery before validation. Preserve
the original directory after any uncertain exit. Do not delete permanent locks,
edit SQLite state, overwrite a database or clear a blocked phase to make a
restart pass.

A saved terminal consumer error also remains a resume refusal regardless of
whether its phase says blocked, stopped or waiting. A stop or signal cannot
erase that evidence. Inspect both phase and `last_error`; changing phase text
does not restore eligibility to consume another bar.

### Capacity and archival boundary

An account retains all financial events, up to **100,000 consumed bars** and a
**256 MiB database**. Bootstrap does not represent consumed trading events.
There is no automatic archive, event eviction or account reset. Capacity
produces a preserved terminal result. Do not create a replacement account and
describe it as uninterrupted continuation of the previous one.

Hashes detect internal consistency errors; without an external trust anchor,
they do not prove that a privileged owner has never coherently replaced the
database or restored an older complete copy. The durability contract assumes
supported local POSIX filesystem/SQLite behavior, not every filesystem or
privileged future race.

## JSON and exit codes

Each completed CLI operation emits one JSON result on stdout. Fixed errors
exclude exception text and filesystem paths. Runtime/import messages, if any,
go to stderr. Help is ordinary command-line help.

| Command | Exit | Meaning |
|---|---|---|
| `run` / `resume` | `0` | Requested bounded polling completed with a current source; consumer saved stopped |
| `run` / `resume` | `3` | Source waiting/refusal result; inspect the preserved reason and prefix |
| `check` | `0` | Valid current consumer observation |
| `check` | `1` | Valid noncurrent observation |
| `audit` | `0` | Verified archived prefix |
| Any command | `2` | Invalid arguments/input, ownership refusal or invalid/unavailable stream; audit mismatch also refuses |
| `run` / `resume` | `4` | Storage uncertainty or unexpected failure; preserve and inspect before restart |
| Interrupted operation | `130` / `143` | SIGINT / SIGTERM recorded; inspect the saved result |

A blocked account or saved terminal error remains refused on resume. A successful
audit does not clear that condition. A stopped consumer ordinarily produces a
`check` exit of `1`.

## Verification scope and remaining work

The regression suites use synthetic closed-candle capture and real original
engine/SQLite components with provider traffic forbidden. The configured runtime
CI jobs execute the continuous-paper tests after installing the existing packages:
Python 3.10/3.12 in Quant Regression integration and 3.10/3.13 in Order Outcome
Safety. This describes coverage configuration; final-head results must be checked
for the published revision and are recorded in [CURRENT_HANDOFF.md](CURRENT_HANDOFF.md).
Isolated CLI dispatch tests are only wiring evidence; they are separate from real
engine/store/runtime tests.

This workflow still needs real-provider/network/disk/clock soak, operational
supervision and alert delivery, backup/restore drills and deployment/rollback
validation. It makes no profitability claim. No broker/live integration,
public deployment, automatic archival or browser visual/accessibility QA is
established by these tests. `is_production_ready()` remains false.

Related references:

- [Public capture operations](MARKET_CAPTURE.md)
- [Historical paper operations](PRODUCTION_PAPER_OPERATIONS.md)
- [Continuous paper design](superpowers/specs/2026-11-15-continuous-paper-design.md)
- [Implementation plan](superpowers/plans/2026-11-15-continuous-paper.md)
