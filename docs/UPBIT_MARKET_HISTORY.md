# Public closed market history → existing paper replay

This adds an **explicit historical-data importer**, not an always-on feed or an
Upbit trading adapter. No API key, balance endpoint, private account or order API
is used. The original PaperRunner/PaperExchange still owns simulated execution.

## Reproduce (macOS/Linux local filesystem)

From the repository root on the PR branch, install the existing engine packages:

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
# Network is used only in this explicit acquisition command. Choose a NEW output.
python tools/import_upbit_history.py --market KRW-BTC --unit 60 --count 720 \
  --output /tmp/uulab-btc-history.json
# All subsequent replay/report operations use that local file, without networking.
python tools/paper_replay_dashboard.py /tmp/uulab-btc-history.json \
  --checkpoint /tmp/uulab-btc-checkpoint.json --report /tmp/uulab-btc-report.json \
  --initial-balance 1000000 --fee-pct 0.1 --slippage-pct 0.05
python apps/dashboard/server.py --replay-report /tmp/uulab-btc-report.json --port 8765
```

Open `http://127.0.0.1:8765` locally. These fee/slippage values are explicit
**example modeling assumptions**, not a claim about your exchange account fees.
Different imports/experiments require different filenames. Identical replay of an
existing fixture retains the report/checkpoint identity and does not repeat fills.
Native Windows checkpoint execution still falls outside the original POSIX-lock
contract. Do not publicly expose the local dashboard.

Optional `--end 2026-10-30T00:00:00Z` supplies a historical exclusive boundary.
Timezone is mandatory. The boundary rounds DOWN to the interval; it cannot be
later than the current time minus the five-second safety margin. The machine
clock must be correct; this margin does not measure clock skew against Upbit.

## Provider contract and conservative restrictions

- Fixed HTTPS `api.upbit.com/v1/candles/minutes/{unit}`; GET only, no redirects,
  arbitrary URL, Authorization, Cookie or Origin headers supplied by this client.
- KRW spot pairs only. Units: 1, 3, 5, 10, 15, 30, 60, 240 minutes.
- 1–10,000 bars, at most 200 rows/page and 100 pages. The UTC candle OPEN field
  defines timestamps; the provider's last-tick timestamp is not a bar start.
- Prices/volumes use bounded Decimal strings. Conflicting duplicates, invalid
  market/unit/OHLC, non-finite JSON, duplicate JSON keys, future/unclosed bars,
  non-progressing pages and insufficient history fail closed.
- Internal missing intervals AND a missing latest interval reject the dataset.
  No prices, volumes or synthetic candles are filled. Upbit legitimately omits
  no-trade intervals, so a sparse market may be refused; this is deliberate.
- 0.2-second per-client pacing; shared-IP limits still apply. Three attempts per
  page for transient connectivity/429/selected 5xx; at most 30 seconds of numeric
  Retry-After. 418 and unrecognized/long cooldowns stop, without retrying early.
  Remaining-Req `sec=0` adds a one-second pause. No background retry loop.
- Response <=1 MiB/page, fixture <=8 MiB, 10-second HTTP timeout. Data is validated
  before atomic create-if-absent publication. Existing files/symlinks are refused;
  a racing unrelated file is never replaced. No partial success report.

## Provenance and offline boundary

The fixture contains its provider, pair, interval, fixed exclusive end, retrieval
UTC time, bar count, gap policy and SHA-256 of canonical OHLCV rows. The exporter
rechecks all these before the existing runner can write a checkpoint. The report
and UI carry only a bounded checked annotation. Legacy local fixtures continue to
work, retain their float parsing semantics and are NOT automatically labeled as
market data. Explicit malformed annotations are rejected, not silently stripped.

Checksums detect accidental changes relative to the recorded hash; anyone able to
rewrite both data and hash can forge them. `authenticated=false` is mandatory:
metadata is a source record, **not signed exchange proof**. Report calculations
are still synthetic fills on historical bars, not real trades or performance.
No depth, order-book queue position, partial fills or live liquidity is modeled.

## Exit codes

0: complete immutable fixture. 2: invalid/incomplete history or existing output.
3: provider/network/cooldown failure. 4: local filesystem failure. 130: interrupted.
Preserve existing files. Do not remove broker orders or alter balances in response
(this command has no broker access).

## Official references reviewed 2026-10-31

- https://docs.upbit.com/kr/reference/list-candles-minutes
- https://docs.upbit.com/kr/v1.6.4/reference/rate-limits
- https://docs.upbit.com/kr/reference/rest-api-guide

The provider documents the 200-candle page limit, exclusive `to`, candle opening
UTC timestamp, omitted no-trade intervals and shared-IP candle request limits.
No upstream implementation code was copied. Recheck provider policy before use.

## Verification commands

```bash
python -m unittest discover -s tests -p test_upbit_history.py -v
python -m unittest discover -s tests -p test_upbit_import_cli.py -v
python -m unittest discover -s tests -p test_market_provenance.py -v
python -m unittest discover -s tests -p test_market_history_integration.py -v
node --test tests/market_provenance_client.test.mjs
```

All test responses are synthetic/provider-shaped; automated tests do not contact
an exchange. The integration suite uses the actual existing PaperRunner and
forbids socket/DNS calls during replay. Existing reservation and replay ledgers
remain distinct; this increment does not reconcile real broker orders.
