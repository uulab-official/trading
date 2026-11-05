# Offline paper evidence comparison (v29)

## Design
Compare BEFORE and AFTER v28 evidence files without the original DB, writer,
network, or state restore. Validate both exact byte streams with existing evidence
and account validators. Reuse one bounded private-file read to avoid a validation
then reopening race. No mutation, new order, recovery, or account authorization.

Require matching account ID, session version, initial settings and optional policy.
Revision must not regress; equal revision requires equal payload digest. Original
trade sequence must be an exact prefix; existing order intents and creation times
must survive with nonregressing fills/timestamps/venue IDs/terminal state. Control
generations cannot regress or change content at the same generation. Gaps between
snapshots are allowed; neither timestamps nor generation gaps prove the unrecorded
path between them, process liveness, current prices or authenticated history.

Reapply only the new trade suffix to the earlier balances and fees using the
original PaperExchange Decimal context and operation order. Require identical final
balances/fees. Report exact stored-value differences, signed modeled fill cash
flows, and any finite-precision residual separately. No forced rounding to zero,
fee double charge, deposit inference, P&L attribution, or invented transport cause.
Distinct counts: new orders, changed acknowledgements, and newly recorded fills.
All records enter checks and totals; detail arrays are capped at 100 with counts.
Saved-mark valuation is labelled as such; missing marks yield null, never zero.

CLI: `paper_evidence.py compare BEFORE AFTER`. Stdout is one JSON report. Existing
export/verify behavior remains. Invalid input exits 2; individually valid but
incompatible observations exit 5 with a fixed reason code and no account details.
Full numeric comparison supports <=128-digit coefficients and exponents -120..120;
outside this analysis bound, comparison fails instead of silently rounding.
No HTTP route or new browser component is added. Evidence/checksums are not signed.

## Usage
With the repository's existing Python packages installed, create two v28 exports
at the times of interest. Export is opt-in; this command does not collect history
or open a trading account automatically. Keep actual files private.

```bash
# BEFORE and AFTER must be ordered by saved revision, not file modification time.
python tools/paper_evidence.py compare \
  .private/before.paper-evidence.json \
  .private/after.paper-evidence.json

python -m unittest discover -s tests -p 'test_order_paper_evidence_compare.py' -v
```

Same revision and payload: `same_snapshot`, even if capture times differ.
Later revision: `forward_snapshot`, including unchanged payloads written again.
Different accounts/settings, reversed revisions, rewritten history or failed
continuation checks: exit 5, no economic report. Invalid/unreadable input: exit 2.
A backward capture clock is a warning; it does not reorder the supplied files.

`cash.from_new_fills` sums signed modeled notionals and recorded fees. `cash.delta`
is the difference between stored balances. `rounding_delta` explains the residual
from the original finite-precision sequential arithmetic; it is not a deposit or
a balance correction. Fees and position quantities have the same distinction.
For example, a modeled 1-unit SELL at 125 with 0.1% fee changes cash by 124.875,
fees by 0.125 and units by -1. An acknowledgement-only recovery contributes zero
new fills, cash or fees. These are synthetic assumptions, not broker cost quotes.

An input is opened once, validated, and used from those exact captured bytes.
No assertion is made that a path still contains those bytes after the read. Each
input's original snapshot and envelope hash is included in the report. This is
not a continuous audit log: endpoint-compatible files can still omit intermediate
activity, and checksums cannot establish an honest writer or broker authenticity.
Comparison JSON itself contains private account and order identifiers.

## Verification scope
Tests exercise actual PaperTradingSession, existing export/verification and real
CLI processes, including USDT/KRW, multiple assets, lost acknowledgements, decimal
rounding, full counts/bounded details, valid-but-conflicting files, replacement
and redacted errors. No new browser UI, HTTP writes, actual provider, prolonged
load, physical-power-loss or other-OS verification is claimed.
