# Provenance-preserving historical paper handoff (v37)

## Design
Bridge the existing Upbit closed-history import format to the existing durable
MA/RSI executor, without stripping data_provenance or changing fingerprinted
execution/recovery modules. This is offline historical simulation, not live
market streaming, broker integration or authenticated market-data attestation.

`paper_market.py inspect FILE` reads one bounded regular local file once, checks
its exact bytes and the existing provider fixture contract, and returns a compact
report. Require exact top-level fields and existing canonical decimal strings,
1–5000 bars, <=4MiB, contiguous closed candles and non-future retrieval time
relative to the caller's UTC clock. Historical age is reported, not rejected as
stale live quotes. No sorting, gap filling, timestamp rewriting or network access.
An imported gap may be legitimate no-trade history; this conservative path still
refuses it. No-data is not a zero-price candle.

`paper_market.py run FILE --directory NEW` validates source and configuration
BEFORE any output, then publishes the exact captured source as `source.json` and
a private `source-manifest.json`. It invokes original run_durable_replay in `run/`
with resumable=True. Source snapshot and manifest exist before activation. The
manifest binds raw SHA-256, canonical candle hash, provenance, selected strategy
configuration and the normalized execution input SHA-256. The original source
path is not stored. Changes to the original source after capture do not change
execution; changes to the bundle snapshots before/after execution are refused.

The bundle and each output must be new. Preserve partial bundles on failure,
never overwrite/retry them. Existing v36 cooperative shutdown wraps run CLI.
Recovery/preflight/audit/dashboard use the child `run/` directory. No wrapper
resume or automatic retry. Original recovery checks frozen run/input.json, not
provider authenticity or the outer provenance snapshot. A source checksum is
consistency only; authenticated=false remains explicit. A completion result
cannot establish profitability, executable liquidity or permission to trade.

## Reviewed references (2026-11-11)
- https://docs.upbit.com/kr/reference/list-candles-minutes
  Upbit documents UTC candle openings, exclusive `to`, and absent no-trade bars.
  Existing importer retains its conservative gap-rejection policy.

## Limits
POSIX local filesystem only, existing dependencies and original fill/risk model.
No source credential fields accepted, live flag, public server or external data
request. Acquisition is the separate import_upbit_history.py command. Samples in
tests are synthetic provider-shaped fixtures, not authenticated exchange data.

## Use
Existing repository package installation is required; no new dependency.
Acquisition is a separate opt-in public-data GET, not account access. An example
with assumed (not account-quoted) costs is:

```bash
mkdir -p .private
chmod 700 .private
python tools/import_upbit_history.py --market KRW-BTC --unit 60 --count 720 \
  --output .private/btc-hourly-001.json
python tools/paper_market.py inspect .private/btc-hourly-001.json
python tools/paper_market.py run .private/btc-hourly-001.json \
  --directory .private/market-study-001 --strategy ma_cross \
  --initial-balance 1000000 --fee-pct 0.1 --slippage-pct 0.05
python tools/paper_audit.py .private/market-study-001/run
python apps/dashboard/server.py \
  --paper-session .private/market-study-001/run/account.sqlite --port 8765
```

The local UI remains `/paper-account.html` and `/paper-orders.html`. Do not expose
it publicly. `source.json` preserves every original byte (including formatting).
The source manifest binds it to normalized `run/input.json`, not to a live broker.
The acquisition command can fail on legitimate no-trade gaps rather than invent
missing candles. API docs reviewed above do not guarantee current reachability.

Only when the nested run is incomplete:

```bash
python tools/paper_preflight.py .private/market-study-001/run
python tools/paper_resume.py .private/market-study-001/run
# Separate explicit saved simulated-fill acknowledgement recovery when needed:
python tools/paper_resume.py .private/market-study-001/run --reconcile-saved-fills
```

The wrapper does not resume the outer directory. Partial source publication before
child creation is not a resumable strategy run; retain it and use a new directory.
SIGINT/SIGTERM use the existing cooperative controller; no hard stop deadline or
SIGKILL/power-loss protection. A stop request never authorizes a replacement order.

CLI stdout is one JSON document, diagnostics stderr. Exit0: validated historical
input or completed_halted run;2: invalid input/options or failed operation;3:
halt/publication outcome needs inspection;130: SIGINT/cancellation;143: SIGTERM.
A failure after original result publication can leave a valid nested result;
never delete it to retry. Recovery/audit do not prove upstream authenticity or
revalidate the outer source manifest. A short history can validly produce zero
trades due to original warm-up; no minimum statistical sample is asserted.

## Verification
`python -m unittest discover -s tests -p 'test_order_market_run.py' -v`
Tests use real original import parsing/engine/SQLite/audit, synthetic provider
responses, and a real process SIGTERM followed by explicit original recovery.
No live API success, other-OS, prolonged operation or maximum-throughput claim.
