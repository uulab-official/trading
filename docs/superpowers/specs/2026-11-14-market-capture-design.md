# Continuous public candle capture design (v45)

## Goal and scope

Add a foreground, restartable collector for explicitly selected Upbit public KRW
minute candles, with bounded durable storage, overlap verification, a freshness
observer and immutable export into the existing historical paper workflow.
The user requested continued production-quality implementation. This is the
next missing data layer; it does not activate a broker, credentials, the legacy
live runner or continuous account execution.

Base: `dd1713276ea5a4580193fca12b62b17bde73e5c5`. Keep the existing feature branch
and Draft PR. Main remains `a5750019dcd7a980a28783258c06c7ef4706afc1`.
All 16 execution-fingerprinted files and original fixture/account schemas stay
unchanged. Python >=3.10; standard library for collection/storage/observation.

## Approach and decisions

1. Reuse UpbitHistoryClient with a durable rolling capture and explicit export:
   chosen by the implementer because the provider/validator already exist and
   finite paper execution has frozen-input recovery semantics.
2. Extend PaperRunner directly to streaming: deferred; this requires a new
   consumption/strategy/risk recovery protocol and next-open execution contract.
3. Backup/restore tooling first: useful but does not provide missing data
   continuity; copying an account also does not solve identity/owner duplication.

Use REST closed-candle polling, retaining the original five-second local close
margin. This is a local acceptance policy, not a provider finality guarantee.
Upbit omits candles when no trade occurs; missing intervals must be rejected,
never interpolated. Official references:

- https://docs.upbit.com/kr/reference/list-candles-minutes
- https://docs.upbit.com/kr/reference/websocket-candle
- https://docs.upbit.com/kr/reference/rate-limits

This is an explicitly bounded rolling window, not a complete historical archive.
Eviction counters and current window bounds remain visible. The original
historical runner consumes only an explicitly exported immutable fixture.

## Fixed capture configuration and state

`validate_config(value) -> dict` requires exactly:

```json
{"market":"KRW-BTC","unit_minutes":1,"bootstrap_bars":120,
 "retention_bars":5000,"poll_seconds":15}
```

Use original market/unit validation. Bootstrap is an integer 1..200; retention
is an integer bootstrap..5000; poll_seconds is an integer 5..300. These settings
are frozen at creation. Resume takes no configuration overrides.

`read_capture(directory)` returns exactly `schema_version=1`, `config`, `state`
and `fixture` (null before the first validated commit). State fields are:

- `capture_id`: 32 lowercase hex; `revision`: integer; `phase`: starting,
  fetching, running, retrying, blocked or stopped.
- `created_at`, `updated_at`: aware UTC ISO strings; nullable `last_attempt_at`
  and `last_verified_at` in the same format.
- `last_error`: null or one of transport_unavailable, rate_limited, rate_blocked,
  request_rejected, cooldown_required, data_rejected, history_conflict,
  catchup_limit, clock_regression.
- `consecutive_failures`, `successful_polls`, `accepted_bars_total`,
  `evicted_bars_total`: nonnegative safe integers <=2**53-1.

The fixture is the original exact five-field imported-market object. Provenance
has the original exact fields and `upbit_public_rest` provider. Capture metadata
is never added to its provenance. Its retained candle count plus evicted count
equals accepted total. Re-observing identical history does not increment accepted
or evicted counts; successful_polls and validation time may advance.

## Durable store and cooperating ownership

New module: `apps/server/trading_bot/market_capture_store.py`.

```python
validate_config(value) -> dict
read_capture(directory) -> dict
capture_writer(directory, *, config=None, now=None)  # context manager
# config supplied: create a NEW leaf directory; None: open existing capture.
writer.read() -> dict
writer.attempt(at) -> None
writer.apply(fixture, *, at) -> None
writer.fail(reason, *, at, retryable=False) -> None
writer.stop(at) -> None
```

`at`/`now` are aware UTC datetime values; now defaults to current UTC. The store
rejects regressing mutation timestamps. Exceptions: `CaptureUnavailable`
(ValueError), `CaptureConflict` (CaptureUnavailable) and `CaptureStorageError`
(OSError). Do not expose private paths/raw SQLite errors at CLI/API boundaries.

Use a private owned 0700 directory, private regular 0600 database and stable
0600 writer lock, with nonblocking exclusive flock for one cooperating writer.
Never unlink a lock or overwrite an existing capture on creation. Reject final
symlinks, special files, changed parent/file/lock binding and insecure ownership
or permissions. Preserve existing artifacts after failure.

Use SQLite transactions, synchronous FULL and rollback-journal mode; no WAL.
Candles and cursor/metadata commit atomically. Fixed schema/application identity,
bounded metadata (4096 bytes), <=5000 rows, database/sidecar <=64 MiB, bounded
text and 1,000,000 VM operations/one-second cooperative observer work budget.
Read-only observations use one read transaction and validate the full bounded
snapshot, with no repair, creation or partial successful results. Refuse WAL,
unexpected schema and journals requiring recovery in observer reads. The owning
writer may use normal SQLite rollback recovery on explicit resume, then validate.
Descriptor/name checks detect observed replacement, not privileged future races.

Apply validates the original fixture. For a nonempty capture it must include
the last committed candle, every overlapping retained row must match exactly,
and incoming end cannot regress. Any missing interval or conflicting overlap
rejects the entire batch before cursor advancement. Keep the latest configured
retention rows transactionally and increment the explicit eviction counter.
No rows beyond the retained bound are read into an unbounded Python collection.

Attempt records fetching and last_attempt_at; apply records running, clears
last_error/failure streak and updates last_verified_at to the validated fixture
retrieval time. Fail records retrying or blocked with a fixed reason; no candle
or successful cursor changes. Stop records stopped while retaining prior data
and error evidence. Saved phase is never process liveness.

## Transport and retry contract

New `packages/data/trading_data/public_candle_transport.py` supplies
`PublicCandleTransport(timeout=10.0, *, connection_factory=None).__call__(url)`.
Timeout must be finite 0.1..10 seconds. The optional factory is a Python test
injection, never a CLI endpoint option. Default fixed-host HTTPS with normal TLS,
no ambient proxy, credentials, Origin header or redirects; retain the existing
1 MiB response cap, exact allowed endpoint/query and JSON content type.
A total socket-I/O deadline interrupts trickled response headers/body; DNS/hung
kernel calls cannot be promised preemptible. No background worker survives a
completed request. Errors remain compatible with UpbitHistoryClient.

Extract/reuse `validate_public_url(url)` from the original public_get validation.
Retain original public_get and import CLI behavior. Add backward-compatible
MarketDataUnavailable attributes `reason`, `retryable`, `retry_after` with
conservative default unavailable/false/0. Preserve the three-attempt client cap.
Categorize exhausted connection/5xx as retryable transport_unavailable, exhausted
429 as retryable rate_limited, with bounded required cooldown. HTTP418, other
hard4xx, malformed/nonfinite/negative or >30-second Retry-After are terminal
rate_blocked/request_rejected/cooldown_required. Classify the final attempt too;
an outer polling loop cannot bypass provider cooldown/hard-stop behavior.
Existing base exception catches and original import CLI exits remain unchanged.

## Coordinator and command lifecycle

New `apps/server/trading_bot/market_capture.py`:

```python
collect_capture(directory, *, config=None, max_polls=None,
                stop=None, client=None, clock=None, sleep=None) -> dict
capture_status(directory, *, now=None) -> dict
export_capture(directory, output, *, count=None) -> dict
```

Return collect `{exit_code, observation}`. Injectable stop returns 0 or a signal
number; injected clock returns aware UTC; sleep is used through interruptible
wait chunks <=0.1 seconds. Default client is the existing UpbitHistoryClient
with the new bounded transport and interruptible pacing/backoff.

Each poll computes `safe_end=floor((UTC_ms-5000)/duration)*duration`.
Empty capture requests bootstrap_bars. Otherwise request the missing intervals
plus one overlapping committed candle, at most200 rows/199 new intervals per
poll. Even an unchanged boundary revalidates one overlapping candle at the
configured low poll rate; this is a new observation, not new market progress.
Recheck stop after fetch and before accepting data. On temporary exhausted
network/429 failure, persist retrying and wait at least max(poll_seconds,
retry_after, min(300, 2**min(consecutive_failures,8))) before the next cycle.
Terminal errors, rejected data, conflicting overlap, excessive catch-up and
clock regression stop acquisition without advancing candles. Do not parse
exception messages to infer retryability. Do not call any account/broker API.

Builder-review rulings: unknown/default provider errors map to terminal
request_rejected. Explicit resume refuses a saved blocked phase, any terminal
last_error (even after stopped), or an interrupted fetching phase without
contacting the provider. An interrupted request may have received a refusal
which was not committed; investigate before starting any new capture. A saved
retryable transport/rate failure waits from updated_at for at least
max(poll_seconds, 30, backoff), since schema1 does not persist Retry-After.
Bounded successful collection records stopped and exits0. A retryable failure
at the max-polls bound retains retrying and exits3. These are saved observations,
not evidence of a running collector.

Clock regression must not be hidden by clamping timestamps or rewriting history.
If a trustworthy stop timestamp cannot be persisted, retain the earlier state
and return a clock/error result; phase is explicitly not liveness. Storage
failure stops the process and never becomes a successful cursor/normal exit.
Signals only record intent; network stop waits at most the in-flight I/O budget
under ordinary functioning OS calls. Restore prior signal handlers.

Review repair: persist a received typed provider failure before honoring signal
intent. If stop interrupts client acquisition/internal retry before a complete
typed outcome arrives, preserve the fetching phase and refuse resume. Do not
rewrite an unknown request outcome as clean stopped with no error evidence.

`tools/market_capture.py` commands:

```bash
python tools/market_capture.py run --market KRW-BTC --unit 1 --history 120 --retention 5000 --poll-seconds 15 --directory NEW [--max-polls N]
python tools/market_capture.py resume DIRECTORY [--max-polls N]
python tools/market_capture.py check DIRECTORY
python tools/market_capture.py export DIRECTORY --output NEW_JSON [--count N]
```

Default run/resume continues until a signal; max-polls is integer1..10000 for an
explicit bounded run. Each completed CLI operation emits one JSON stdout result.
Run/resume wrap the final observation with operation=collect/read_only=false;
check is read-only. Check exits0 only for current,1 for valid noncurrent state,
2 for invalid/unavailable. Collect exits0 for a bounded successful collection,
3 for acquisition/continuity/clock stop,2 for input/ownership/invalid capture,
4 for storage/publication error,130/143 for signals. No raw exception text.

Export reads a stable validated window, optionally its latest count rows, and
publishes a NEW original fixture, <=5000 bars/4 MiB, using local_report_io.
Output is outside the capture under an existing owned/private parent. No
automatic collection, account creation or resume is part of export. Stopped or
stale valid captures may be exported as historical data; this is not a freshness
authorization. Retain all sources and possible output on publication uncertainty.

## Read-only status contract and dashboard

New independent GET/HEAD `/api/market-capture` with no query and optional server
`--market-capture DIRECTORY`. Do not add a fifth key to v44 schema1 readiness or
change its existing probe. Valid capture observations return200 even when stale;
unconfigured/invalid source returns503 with a fixed market_capture_unavailable
envelope. Existing origin/CSP/method policies apply. No HTTP mutation endpoints.

`capture_status` returns exactly:

- schema_version1, source=upbit_market_capture, mode=paper, read_only=true,
  live_enabled=false, scope=closed_public_candle_observation, observed_at.
- `status`: starting, catching_up, current, stale, retrying, blocked, stopped or
  clock_error; `configuration`: the frozen config; `capture`: the exact state.
- `window`: bars, first_open_ms, end_exclusive_ms, candles_sha256 (null except
  bars=0 before first capture).
- `freshness`: data_age_ms, observation_age_ms (nullable nonnegative ages),
  max_data_age_ms, max_observation_age_ms, clock_valid.
- `latest`: null or open_ms, close_ms, open, high, low, close, volume; decimal
  values remain strings.
- `boundaries`: orders=false, account_access=false,
  process_liveness=not_asserted, provider_authenticity=not_authenticated,
  gap_policy=reject, retention=rolling_window.

Data age measures now minus committed end; observation age measures now minus
last_verified_at. Max data age is duration+5000+poll_seconds*1000+10000;
max observation age is max(30000,2*poll_seconds*1000). Negative/future chronology
means clock_error with invalid ages null. Otherwise persisted blocked/stopped/
retrying/fetching/starting phases retain corresponding status (fetching becomes
catching_up); running is current only with data and both ages within budget,
otherwise stale. No current claim from a repeated HTTP result alone.

New market-data.html/css/mjs follows existing dashboard style, showing phase,
latest closed candle, window/retention, observation time, data age and failure
reason. A failed refresh retains last good data with an explicit stale warning.
Use textContent, no browser execution controls, credential inputs or remote
links. Pause hidden-tab polling, coalesce overlaps and bound fetch timeout.
Actual visual/browser QA remains unverified under the existing browser boundary.

## Verification and delivery

Use real temporary SQLite/locks/subprocesses and loopback transport fixtures;
provider network is injected, never real order execution. Cover overlap and gap
failures, restart, competing owners, transaction failure, retention/limits,
clock regression, hard/cooldown errors, total I/O deadlines, signal exits,
read-only observation, export accepted by original inspect/run, and API/Node
failure states. Preserve all old suites and required CI gates. Run complete
regression and an original-engine synthetic export-to-paper demonstration.
Do not call controlled tests a real provider soak or execution readiness.
Publish the exact tested tree through expected-head fast-forward and verify
final-head CI before updating the existing Draft PR.
