# Trading continuation handoff — 2026-11-14 (v45)

## Resume point and delivery scope

Repository: `uulab-official/trading`; existing branch `feat/smc-market-structure`
and [Draft PR #1](https://github.com/uulab-official/trading/pull/1).
The verified v44 starting head was `dd1713276ea5a4580193fca12b62b17bde73e5c5`,
tree `bb6016aae7e9e4455aa939727eb94a2e38ed74f2`. Main remains
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user requested continued production-quality development. This increment
implements continuous public-data capture using the existing Upbit integration,
with an explicit export into the original historical paper workflow. Development,
testing, feature publication and Draft PR metadata updates continue under the
existing authorization. Main merge, public deployment, credentials and real
trading remain outside this delivery. No provider/account requests, broker
orders or external notifications were made during implementation or testing.

Independent remote inspection before work and at 2026-11-14T13:09:59Z verified
unchanged feature/main refs, an open Draft/unmerged PR without auto-merge, and
unchanged PR title/body. Use actual refs and final-head checks when resuming;
final publication details are recorded after this handoff commit in the PR and
ignored local delivery receipt.

## Implemented capture contract

### Storage and continuity

`apps/server/trading_bot/market_capture_store.py` creates a new private POSIX
capture directory containing `capture.sqlite3` and permanent `.capture.lock`.
Configuration is frozen: KRW spot market, supported minute unit, initial history
1–200, retention up to 5,000, and polling interval 5–300 seconds. Resume accepts
no configuration overrides. Creation never reuses an existing leaf.

Owned 0700 directories, 0600 single-link regular files and nonblocking exclusive
flock coordinate cooperating writers. Parent, directory, DB and lock identities
are checked throughout. Fixed application/schema identity and SQLite FULL
rollback transactions commit candles, provenance, state and counters together.
The store detects observed replacement; it is not a sandbox against every
privileged future filesystem race.

The initial batch must match the configured history count. Later batches contain
at most 200 rows, include the last committed candle and match all retained
overlap. Missing intervals, revised candles and regressing ends are rejected.
The collector refuses catch-up beyond 199 new intervals. No gap is filled.
The original Decimal/provenance validator is reused, with exact five-field
fixture shape, frozen configuration and retrieval/commit chronology checked
separately. The existing five-second close margin is a local acceptance policy,
not provider finality or authenticated market evidence.

Retention is an explicit rolling window. Retained count plus evicted count
equals accepted total; re-observing an unchanged candle does not count new bars.
Limits include 5,000 rows, 4,096 metadata bytes, 64 MiB per DB/sidecar, bounded
text, one million SQLite VM operations and a one-second cooperative observation
budget. Observers validate one read transaction and reject WAL, extra schema,
unstable bindings and nonempty journals. Only explicit writer resume can perform
normal SQLite rollback recovery. Hung OS/filesystem calls cannot be preempted
by cooperative work budgets.

### Transport, retries and stop/restart

`public_candle_transport.py` provides fixed-host HTTPS candle GETs with normal
TLS verification, no ambient proxy, credentials, Origin or redirects, and a
1 MiB response cap. One ten-second monotonic socket-I/O deadline covers TCP
attempts, TLS, request sends, headers, body and chunk framing. It creates no
background deadline worker. DNS or hung kernel calls themselves cannot be
forcibly interrupted, but their elapsed time consumes the same deadline.

The existing Upbit client retains its three-attempt policy and original
retryable HTTP set. Additive typed errors preserve the final attempt's cooldown
and distinguish transient transport/rate failures from HTTP 418, hard rejections
and invalid, ambiguous or over-30-second cooldowns. The existing importer's
`public_get` transport remains unchanged; only the new collector selects the
new transport.

The collector never infers retryability from error text. Unknown errors become
terminal `request_rejected`. Waits check stop intent in chunks of at most 0.1
seconds. Resume refuses a saved blocked phase, any terminal error even after
stopped, and an interrupted fetching phase. A saved retryable error waits from
`updated_at` for at least `max(poll_seconds, 30, backoff)`, because schema1 does
not persist Retry-After. Inspect uncertain/refused captures before deciding to
start any new capture; deleting locks or editing metadata is not recovery.

Signal handlers only record intent and previous handlers are restored. A received
typed failure is persisted before honoring a simultaneous signal. Interruption
inside acquisition/internal retry without a complete outcome preserves fetching
uncertainty. A signal on an already-refused resume cannot erase its refusal;
explicit nonretryability carried by a blocked phase is also retained. A valid
response arriving after stop intent is discarded. Regressing clocks never become
clamped successful timestamps; if a trustworthy stop cannot be recorded, prior
state remains and the observation reports `clock_error`. Saved phase never proves
collector process liveness.

### CLI, export and local observation

`tools/market_capture.py` provides `run`, `resume`, `check` and `export`.
Each completed command emits one JSON stdout document with fixed error envelopes.

| Operation | Exit meanings |
|---|---|
| Collection | 0 bounded success, 3 acquisition/continuity/clock/resume refusal, 2 invalid input/source/ownership, 4 storage uncertainty, 130/143 signals |
| Check | 0 current, 1 valid noncurrent, 2 invalid/unavailable |
| Export | 0 new fixture published, 2 invalid input/source, 4 storage/publication failure |

Bounded success records stopped, so a later check normally exits 1. A retryable
failure at the poll bound retains retrying and exits 3. Signal exit codes do not
imply that uncertain or blocked state has been cleared; inspect the observation.

Export reads a validated capture and creates a new original five-field fixture
outside it, optionally selecting the latest rows, within the original 5,000-bar
and 4-MiB limits. It recalculates subset count/hash and preserves original
provenance semantics. It never collects, creates an account, executes a strategy
or resumes one. Valid stale/stopped captures may be exported as historical data.

Export carries both observed source and destination-parent bindings through the
publisher's pinned scope before staging/linking and during final confirmation.
The shared publisher optionally enforces a private owned parent, uses private
staging/no-clobber linking, syncs file and directory, and preserves possible
output after uncertain publication. Existing replay/research semantics remain.

The dashboard adds `--market-capture DIRECTORY`, `/market-data.html` and
GET/HEAD `/api/market-capture`. Valid noncurrent observations return 200;
unavailable/unconfigured returns 503. Existing readiness schema1 retains exactly
`journal`, `replay`, `research` and `paper_session`; capture is independent.

Data age measures time since the last committed candle end; observation age
measures time since successful validation. Current requires saved running, data
and both ages within bounds. Clock error nulls both ages. The client validates
exact schema, decimal strings, microsecond chronology and observation identity.
It coalesces GETs, enforces a five-second/16-KiB request bound, pauses hidden tabs,
handles page-cache restoration, and preserves previous valid data with an
explicit warning after failure. It has no execution or credential controls.

## Operator commands

From the repository root, create a new capture and keep it in the foreground:

```bash
mkdir -p .private
chmod 700 .private
python tools/market_capture.py run --market KRW-BTC --unit 1 \
  --history 120 --retention 5000 --poll-seconds 15 \
  --directory .private/btc-capture-001
```

In another terminal:

```bash
python tools/market_capture.py check .private/btc-capture-001
python apps/dashboard/server.py --market-capture .private/btc-capture-001 --port 8765
```

Open `http://127.0.0.1:8765/market-data.html` locally. To explicitly run a
historical snapshot through the original engine:

```bash
python tools/market_capture.py export .private/btc-capture-001 \
  --output .private/btc-snapshot-001.json
python tools/paper_market.py inspect .private/btc-snapshot-001.json
python tools/paper_market.py run .private/btc-snapshot-001.json \
  --directory .private/btc-paper-001
python tools/paper_audit.py .private/btc-paper-001/run
```

Every destination must be new. Preserve failed captures and possible outputs.
Full lifecycle, storage and JSON contracts: [MARKET_CAPTURE.md](MARKET_CAPTURE.md).
The [paper operations runbook](PRODUCTION_PAPER_OPERATIONS.md),
[dashboard guide](WEB_DASHBOARD.md), README and production checklist agree on
this separate collection lifecycle and explicit historical execution handoff.

## Independent review and verification

The implementation checkpoint was `2ef5c2e`. Fresh whole-increment review found
two Important issue families, both reproduced before repair:

1. Signal/refusal/uncertain-resume state loss. Repairs `e344a2b` and `e2b6c00`
   preserve received refusal, interrupted acquisition, prior resume refusal,
   explicit nonretryability and signal-plus-regressed-clock outcomes.
2. Export source/destination substitution between validation and publication.
   Repair `e2b6c00` carries bindings into publication checks. Tests cover source
   relocation into the output parent, relocation while the output parent keeps
   its inode/path, and uncertain source movement after a link may have published.

Independent recheck on `e2b6c001acad2d7ee2043dd5ed1e65260b1929da` reran original
reproductions and 14 targeted methods. Both families are closed: refused resumes
made zero provider calls; uncertain state remained; replaced destinations were
refused before creation; source snapshots were preserved. No remaining Critical,
Important or Minor finding was reported. Separate store review also repaired
an observed DB substitution during SQLite connect before any initialization
write; foreign DB bytes remain unchanged.

Final local gates after the production repairs:

| Gate | Result |
|---|---|
| Development unittest discovery | 1,200 passed in 112.433 seconds |
| Package/server pytest | 309 passed plus 382 subtests in 15.06 seconds |
| Dependency-free Node clients | 103 passed in 324.890 milliseconds |
| Python 3.10 syntax target | All 14 changed Python files parse |
| Execution compatibility | All 16 fingerprinted source files byte-identical to v44 |

The pytest run retains 926 existing datetime deprecation warnings. Syntax checks
are not substituted for actual supported-runtime CI. Original account/recovery
and imported fixture schemas are unchanged. New tests run in the existing CI
matrices without removing any required gate.

A network-forbidden synthetic 90-bar capture/export/original durable MA replay
produced 10 simulated fills, ended `completed_halted`, and passed
`verified_completion` audit. The export SHA matched the original inspector and
frozen source; capture bytes were unchanged. Actual Python JSON passed the JS
client validator. A post-review export reproduced identical fixture bytes without
rerunning the completed account. Private receipts are in the ignored execution
ledger. This is controlled integration evidence, not provider-soak or profit
validation.

## Remaining production evidence and engineering

Continuous public-data collection is implemented. Continuous strategy/account
consumption remains separate work: it needs a durable consumption cursor,
strategy/risk state and next-open execution/recovery semantics. The original
engine still consumes an explicit frozen historical fixture; repeating that
replay is not a streaming execution service.

Real-provider/network/clock/disk soak, actual service deployment/rollback, shared-IP
operational measurement, alert delivery and backup/restore drills remain
unverified. Broker-authoritative reconciliation, credentials and real execution
are not present. Legacy live/Rust/Compose components are not an integrated
verified service. `is_production_ready()` remains false.

Actual browser visual/accessibility QA remains unverified. The prior unavailable
browser/refused local-file boundary was not bypassed; HTTP, Node and structural
tests are not visual inspection. No Windows/all-filesystem certification,
cryptographic provider authenticity or profitability is claimed.

Design: [v45 spec](superpowers/specs/2026-11-14-market-capture-design.md).
Plan: [v45 plan](superpowers/plans/2026-11-14-market-capture.md).

## Final publication discipline

This handoff precedes feature publication. Publish the exact tested tree through
an expected-head non-force update, preserving local checkpoint history. Verify
all applicable final-head CI and fresh PR metadata before updating the description.
Keep PR1 Draft/open/unmerged without auto-merge and main unchanged. Record final
refs, tree, parent, CI and metadata in the PR and ignored delivery receipt. On
resumption, inspect actual refs/checks; previous-head success does not verify a
later commit.
