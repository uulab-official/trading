# Upbit public candle backfill — review-only candidate

This command addresses one outstanding paper-trading limitation: the public
API's 200-bar page size. If a collector is stopped long enough for its latest
response to no longer overlap saved candles, the regular paper cycle correctly
**refuses to advance**. Do not delete the archive or disable its historical
equality checks.

The tool in `tools/upbit_paper_backfill.py` follows Upbit's documented
`to` parameter as an **exclusive UTC cutoff**. It requests at most
`--pages` older public pages, verifies that historical pages move backwards,
requires an identical overlapping saved candle, and insists on contiguous
intervals. It never invents missing prices or permits changed saved OHLCV.

## Generate a separate recovery candidate

First stop the supervisor and preserve an immutable paper-pair backup.
Then run:

```sh
python tools/upbit_paper_backfill.py \
  --archive data/upbit-btc-1h-archive.json \
  --candidate data/upbit-btc-1h-recovery-candidate.json \
  --market KRW-BTC --unit 60 --pages 3 --count 200
```

The original archive and checkpoint are **never** replaced by this command.
A candidate filename must be distinct and must not already exist. The
candidate contains `mode: paper`, `manual_promotion_required: true`,
the count of appended candles, and the number of historical pages fetched.
One bounded request is made for the most recent complete candle window;
up to three additional requests are made for backfill.

## Independent pre-promotion review

After creating a candidate, verify the original archive and paper
checkpoint, compare every historical candle, and independently replay
the saved economic state against the candidate:

```sh
python tools/review_paper_backfill.py \
  --archive data/upbit-btc-1h-archive.json \
  --checkpoint data/upbit-btc-1h-paper.json \
  --candidate data/upbit-btc-1h-recovery-candidate.json
```

The command returns `candidate_verified_for_manual_review` only after
the complete original prefix, contiguous appended prices, manual-review
metadata, and persisted paper ledger all agree. It reports
`promotion_performed: false` and does not write to the archive,
checkpoint or candidate. It uses local cooperative locks and may create
persistent sibling lock files. The result is **not** an approval to trade.

**Operator review is mandatory.** Before using a candidate, verify its
OHLCV values against the public exchange records, confirm chronological
consistency, investigate any missing intervals, and test a copy of the
portfolio checkpoint with this new history in an isolated location.
The built-in runner compares the saved prefix and risk/economic state.
Only after successful independent verification should an operator replace
the archive **while supervision is stopped**. Keep the old archive,
checkpoint, report and a rollback copy.

## Explicit refusal conditions

- Historical page budget exhausted without an identical overlap
- Conflicting saved price, duplicated timestamp, wrong market/timeframe
- Unaligned UTC open, missing candle interval, inconsistent OHLCV
- Public API outage or unexpected result structure
- Recovery candidate path collides with source or an existing file

These failures are deliberately not "healed" by synthetic prices.
Upbit documents that intervals with no trades can be omitted, so a gap
may be legitimate market data rather than a network fault. In both
cases this conservative tool refuses it; alternative missing-bar
research must be separately validated before any trading decision.
No unattended promotion or catch-up trading is enabled.

References:
- https://docs.upbit.com/kr/reference/list-candles-minutes
- https://docs.upbit.com/kr/docs/how-to-download-candle-data

Run `python tools/offline_checks.py` for mock-only regression tests.
The public API and long-duration operational behavior remain separately
unverified in the current CI results.
