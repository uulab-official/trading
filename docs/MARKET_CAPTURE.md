# Public closed-candle capture (v45)

The foreground collector maintains a bounded, restartable window of Upbit public
KRW minute candles. It verifies continuity before saving data, exposes the age
of both the data and its last successful validation, and exports an immutable
historical fixture for the existing paper runner. Collection and observation
do not open an account, consume credentials, send orders or execute a strategy.

Use Python 3.10 or newer and an owned local POSIX filesystem. The supported
dashboard remains on `127.0.0.1`. The collector uses Python's standard library;
the separate historical paper workflow still needs the existing repository
packages and engine dependencies described in
[PRODUCTION_PAPER_OPERATIONS.md](PRODUCTION_PAPER_OPERATIONS.md).

## Create one capture

Run these commands from the repository root. The last command contacts the
fixed public Upbit candle endpoint. A new capture directory is mandatory, and
creation never overwrites an existing directory, including an empty or failed
one.

```bash
mkdir -p .private
chmod 700 .private
python tools/market_capture.py run --market KRW-BTC --unit 1 \
  --history 120 --retention 5000 --poll-seconds 15 \
  --directory .private/btc-capture-001
```

Keep the command in the foreground. It normally runs until Ctrl-C or SIGTERM;
it does not install a service or activate the legacy Docker deployment. Record
the process ID and exit result in the process supervisor you operate. A saved
`running` or `fetching` phase alone cannot establish that this process is alive.

| Option | Accepted values | Meaning |
|---|---|---|
| `--market` | `KRW-` followed by 1–20 uppercase letters/digits, excluding `KRW-KRW` | Explicit KRW spot pair |
| `--unit` | `1`, `3`, `5`, `10`, `15`, `30`, `60`, `240`; default `1` | Candle duration in minutes |
| `--history` | Integer `1..200`; default `120` | Initial closed-candle request |
| `--retention` | Integer from history count through `5000`; default `5000` | Maximum saved rolling window |
| `--poll-seconds` | Integer `5..300`; default `15` | Minimum regular delay between polls |
| `--max-polls` | Optional integer `1..10000` | Explicit bounded run instead of continuous operation |

These settings are frozen in the capture. `resume` takes no market, timeframe,
retention or polling overrides. If different settings are needed, create a new
capture under a new name after reviewing why the change is needed.

An explicitly bounded example is:

```bash
python tools/market_capture.py run --market KRW-BTC --unit 1 \
  --history 120 --retention 5000 --poll-seconds 15 \
  --directory .private/btc-capture-bounded-001 --max-polls 4
```

Reaching this limit after a successful poll saves `phase=stopped` and exits `0`.
Reaching it after a retryable acquisition failure exits `3` and retains
`phase=retrying` and the failure evidence. A successful bounded run therefore
normally produces a subsequent `check` exit of `1`, because stopped is a valid
noncurrent state.

## Continuity, storage and retry behavior

Every poll uses the most recent candle boundary at least five seconds behind
the local UTC clock. That margin is a local acceptance policy, not a promise
that the provider will never revise its data. The existing importer validates
the market, interval, OHLCV values and exact decimal strings. No missing interval
is filled or reordered to make a batch pass.

After the first commit, each request includes the last saved candle and at most
199 new intervals. All overlapping retained rows must match, and the new batch
must be contiguous. Conflicting overlap, missing intervals, a regressing end
boundary or excessive catch-up stops acquisition before advancing the committed
window. An unchanged boundary still revalidates one saved candle; this may
advance `successful_polls` and `last_verified_at` without adding any new bars.

Upbit documents that intervals without trades can have no candle. This path
deliberately rejects such gaps; absence is not a zero-volume fabricated candle.
See the provider's [minute candle reference](https://docs.upbit.com/kr/reference/list-candles-minutes)
and [request-limit reference](https://docs.upbit.com/kr/reference/rate-limits).

The store uses a private `0700` directory, `0600` database and stable writer
lock. A cooperating second writer is refused. Candles, counters and saved phase
commit in one SQLite transaction using rollback-journal mode and synchronous
`FULL`. Retention removes the oldest rows within that transaction and increases
`evicted_bars_total`. The visible count plus the evicted count equals
`accepted_bars_total`; this is a rolling window, not a complete archive.

Readers validate one bounded snapshot without repair. The limits include 5,000
rows, 4,096 bytes of metadata, a 64 MiB bound on the database and each sidecar,
and bounded observer work. An observer refuses WAL, changed file bindings,
unexpected schema and journals requiring recovery. Explicit writer resume may
perform normal SQLite rollback recovery before validating the capture. Do not
delete a lock, change permissions, edit SQLite rows, or copy files over an
existing capture to get around a refusal. Retain the original artifacts.

The public transport permits only the fixed HTTPS candle host/path/query. It
uses normal TLS, no ambient proxy, authentication or redirect, and a response
cap of 1 MiB. A socket-I/O deadline covers response headers and body; ordinary
network requests are bounded to ten seconds each. The history client retains
its three-attempt cap. DNS or a hung kernel/filesystem call is not promised to
be forcibly preemptible.

| Failure category | Result and operator meaning |
|---|---|
| `transport_unavailable`, `rate_limited` with retry permitted | Save `retrying`; wait at least the configured polling interval, the required accepted cooldown and the bounded exponential backoff |
| `rate_blocked` | Provider block is terminal; do not turn a service restart into an immediate retry loop |
| `request_rejected` | Hard or unclassified provider/request failure is terminal |
| `cooldown_required` | A malformed, negative, nonfinite or over-30-second cooldown is terminal for this bounded client; investigate the provider requirement |
| `data_rejected` | Rejected schema, values, interval or continuity; keep the last committed window |
| `history_conflict` | A previously retained candle changed; preserve both the capture and available operational evidence |
| `catchup_limit` | More than 199 new intervals would be needed; the bounded overlap request cannot bridge this interruption |
| `clock_regression` / status `clock_error` | UTC ordering is inconsistent; invalid age comparisons are left unavailable |

The outer collector uses explicit exception policy fields, not exception text,
to decide retryability. An unclassified provider error becomes terminal
`request_rejected`. A blocked phase remains blocked even if its reason name is
normally retryable. The permitted retry delay is at least
`max(poll_seconds, retry_after, min(300, 2**min(consecutive_failures, 8)))` seconds.

## Check freshness and observe locally

Checking is a local read and makes no provider request:

```bash
python tools/market_capture.py check .private/btc-capture-001
```

The direct result is a version-1 observation with
`source=upbit_market_capture`, `mode=paper`, `read_only=true`,
`live_enabled=false` and `scope=closed_public_candle_observation`. It contains
the fixed configuration, saved capture state, retained window, latest closed
candle and freshness bounds. Decimal values remain strings. The boundary fields
explicitly say that orders and account access are false, process liveness is
not asserted, provider authenticity is not authenticated, gaps are rejected,
and retention is a rolling window.

Freshness has two independent ages, measured at `observed_at`:

| Field | Calculation / budget |
|---|---|
| `data_age_ms` | Observation time minus the last committed candle's exclusive end |
| `observation_age_ms` | Observation time minus the last successful fixture validation time |
| `max_data_age_ms` | Candle duration + 5,000 + polling interval in milliseconds + 10,000 |
| `max_observation_age_ms` | Maximum of 30,000 and twice the polling interval in milliseconds |

For one-minute candles with 15-second polling, these budgets are 90 seconds and
30 seconds respectively. Only saved `phase=running` with data and both ages in
budget can report `current`. A successful HTTP request with no new candle cannot
reset the data age. Invalid chronology reports `clock_error`,
`clock_valid=false`, and both ages `null`.

| Status | Interpretation |
|---|---|
| `starting` | No initial successful capture yet |
| `catching_up` | Saved phase is `fetching`; this may also remain after an interrupted process |
| `current` | Both freshness budgets passed at the reported observation time |
| `stale` | Saved running data exceeded at least one budget |
| `retrying` | A retryable failure and retry state were saved |
| `blocked` | A terminal failure was saved |
| `stopped` | A cooperative stop or successful explicit poll limit was saved |
| `clock_error` | The clock prevents a reliable age comparison |

In a separate terminal, start the passive local observer:

```bash
python apps/dashboard/server.py --market-capture .private/btc-capture-001 --port 8765
```

Open `http://127.0.0.1:8765/market-data.html` on the same computer. The page shows
the saved phase, failure reason, latest closed OHLCV, window bounds, retention
counters, API observation time and both ages. It requests status every ten
seconds while visible and pauses hidden tabs. Concurrent refresh requests are
coalesced. Each browser request has a five-second total timeout and 16 KiB body
limit. Failed, malformed, changed-identity or regressing-revision responses keep
the last validated report and show an explicit previous-observation warning.
All values and ages are labeled as of the API observation time.

The independent GET/HEAD `/api/market-capture` endpoint accepts no query.
Valid observations return HTTP `200` even when stale, stopped, retrying or
blocked. An unconfigured or invalid capture returns HTTP `503` with the fixed
`market_capture_unavailable` error envelope. Existing origin, method and CSP
rules apply; there are no collection or account mutation endpoints. Refreshing
the page reads the capture only.

The v44 `/api/readiness` contract still lists exactly `journal`, `replay`,
`research` and `paper_session`. Capture is observed separately. A dashboard
configured with only a capture can serve this page successfully while readiness
returns `503` with `not_configured`; that result is expected for the unchanged
four-source readiness contract. The liveness probe covers the observer process,
not the collector.

## Stop and resume deliberately

Ctrl-C and SIGTERM request cooperative stop; their handlers only record intent.
An in-flight ordinary socket operation remains subject to its I/O deadline,
and interruptible pacing/backoff checks stop intent in chunks of at most 0.1
seconds. A response received after stop intent is not accepted as a new candle
commit. A successful stop saves `stopped` and retains previous data and error
evidence. Signals exit `130` for SIGINT and `143` for SIGTERM. Previous handlers
are restored.

If a terminal provider failure arrives with a signal, its reason is saved before
the stop, so restart cannot erase the refusal. If the signal interrupts the
client before a complete typed outcome reaches the collector (including an
internal retry/cooldown wait), the saved phase remains `fetching` and explicit
resume refuses it. Exit `130`/`143` still records the signal; inspect the final
observation before making a recovery decision.
Signal intent during an already-refused resume also preserves its original
state. A blocked failure whose reason is usually transient keeps the blocked
phase, so shutdown cannot silently change its explicit retry policy.

SIGKILL and power loss cannot execute a stop finalizer. If a trustworthy stop
timestamp cannot be saved after clock regression, the earlier phase may remain;
the returned clock/error result is evidence to investigate, not permission to
reinterpret that phase as liveness.

Read the capture and confirm the prior process has exited before an explicit
resume:

```bash
python tools/market_capture.py check .private/btc-capture-001
python tools/market_capture.py resume .private/btc-capture-001
```

Resume reuses the saved configuration and still acquires the single-writer lock.
It refuses saved `fetching`, any `blocked` phase and any retained terminal
`last_error`, including a terminal error preserved inside `stopped`. These
refusals exit `3` and preserve the existing observation. Resolve the cause and
review the evidence; when a new capture is appropriate, use a new directory
instead of resetting the old state. A supervisor must not blindly restart a
terminal refusal. Resume does not make a data gap repairable.

For a retained retryable `transport_unavailable` or `rate_limited` error, resume
waits from saved `updated_at` for at least the maximum of the configured polling
interval, 30 seconds and the saved failure backoff, less time already elapsed.
The conservative 30 seconds preserves the largest accepted cooldown because
the schema does not store the original Retry-After value. Restarting cannot
shorten this wait.

## Export to the historical paper workflow

Export reads one validated saved window and publishes a **new** original-format
fixture. Its existing owned parent must have mode `0700`, and the output must
be outside the capture. It never collects additional data or creates/resumes a
paper account. A stopped or stale but valid capture can be exported as historical
data; export is not a freshness authorization.

```bash
python tools/market_capture.py export .private/btc-capture-001 \
  --output .private/btc-history-001.json
python tools/paper_market.py inspect .private/btc-history-001.json
```

Add `--count N` to export only the newest `N` retained rows, with
`1 <= N <= retained bars`. Omitting it exports the full retained window. An empty
capture cannot be exported. The original five-field fixture schema and original
provenance fields are unchanged; capture metadata remains outside the fixture.
The bounds are 5,000 bars and 4 MiB. An existing output is never overwritten.
Preserve any output and the source on publication uncertainty before deciding
what to do next.

A successful export report contains exactly `schema_version`, `source`, `mode`,
`live_enabled`, `read_only`, `operation`, `scope`, `capture_id`,
`capture_revision`, `bar_count`, `end_exclusive_ms`, `candles_sha256` and
`fixture_sha256`. Its source is `upbit_market_capture_export`, operation is
`export`, scope is `historical_fixture_export`, and `read_only=false` records
that a new file was published. The source capture remains a read-only input.
The report does not expose a private output path. Hashes establish consistency,
not provider authentication.
The export carries the observed source directory and output-parent bindings
through publication. An observed relocation or substitution is refused before
writing, or reported as uncertain if a destination might already have been
published. Preserve all artifacts in that case.

To perform a separate historical simulation explicitly:

```bash
python tools/paper_market.py run .private/btc-history-001.json \
  --directory .private/btc-paper-001 --strategy ma_cross \
  --initial-balance 1000000 --fee-pct 0.1 --slippage-pct 0.05
python tools/paper_audit.py .private/btc-paper-001/run
```

The simulation freezes the exported source and uses the original finite paper
engine. Fees and slippage above are assumptions. Later collector updates do not
append to this account. A short window can produce zero trades due to strategy
warm-up; the run is not a prospective streaming strategy. See
[MARKET_PAPER_RUN.md](MARKET_PAPER_RUN.md) for source manifests, nested `run/`
auditing and explicit historical recovery.

## Command results and failure exits

Each completed operation emits one JSON stdout document. Successful `run` and
`resume` handling returns a collection envelope with `operation=collect`,
`mode=paper`, `read_only=false`, `live_enabled=false`, `exit_code` and the final
`observation`. `check` emits the observation directly. Failures use fixed error
codes without raw provider/SQLite messages or private paths.

| Command | Exit code | Meaning |
|---|---|---|
| `run`, `resume` | `0` | Explicit bounded collection completed and saved stopped state |
| `run`, `resume` | `3` | Acquisition, continuity, catch-up, clock or saved-resume refusal; inspect retained state |
| `check` | `0` | Valid `current` observation |
| `check` | `1` | Valid observation in any other status |
| `export` | `0` | New historical fixture published |
| All | `2` | Invalid arguments, ownership/configuration refusal or invalid/unavailable capture |
| Mutating operation | `4` | Storage/publication failure or unconfirmed outcome; retain artifacts |
| Collection | `130`, `143` | SIGINT / SIGTERM requested stop; inspect final observation |

A nonzero exit is never evidence that the directory can safely be deleted and
reused. No automated reset, account retry or trade authorization follows from
any of these commands.

## Verification limits

The accompanying tests use controlled synthetic provider-shaped candles, real
temporary stores/locks, local transport fixtures and the original historical
engine. The client has Node coverage for exact schema/decimal values, stale
retention, request coalescing, deadlines, body limits and visibility lifecycle.
These checks are not a real-provider soak, authenticated market attestation,
maximum-throughput measurement or live-execution readiness result. Actual
browser rendering and visual QA remain unverified in this implementation
session. Deployment, service supervision, remote access and continuous strategy
execution require their own implementation and operating evidence.
