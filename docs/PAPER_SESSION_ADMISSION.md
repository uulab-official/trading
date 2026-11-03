# Local paper-session admission (v23)

## Scope

`PaperTradingSession` now validates deterministic local execution before storing
SUBMITTING. The ordinary OrderManager has a protected no-op admission hook; only
the session's private subclass uses it. No network, credentials, provider adapter,
automatic winner, automatic retry, live activation or existing-bot default change.

The check uses a detached original PaperExchange, validates its hypothetical
acknowledgement through OrderManager, and validates the projected account/order
bundle. It does not publish those simulated trades. Funds (including modeled
fees/slippage), inventory, supported order type/currency, order-ID bounds,
chronological fills and snapshot limits must pass. Original pricing and fee
arithmetic is unchanged; successful admission is NOT a reservation or a live
broker authorization.

The manager checks duplicate IDs and existing uncertainty first. Then, under the
admission lock, it checks a detached proposed order. A deterministic local failure
raises `PaperOrderRejected` (ValueError) with no new order, account mutation or DB
revision. The unsubmitted ID may be used for a corrected request. An accepted ID
cannot be reused for a different intent. Actual failures AFTER intent persistence
remain UNKNOWN; they must not be converted to local rejection or blindly retried.
A commit that takes long enough to expire the quote can therefore leave an
unfilled UNKNOWN. Existing explicit reconciliation does not require a fresh quote.

## Optional immutable policy for new accounts

```python
from decimal import Decimal
from trading_core.paper_session import PaperTradingSession

async with PaperTradingSession(
    '.private/guarded-paper.sqlite', create=True,
    initial_balance=Decimal('1000'), fee_pct=Decimal('0.1'),
    max_quote_age_ms=30_000, max_order_notional=Decimal('200'),
) as session:
    # Supply genuine caller-observed epoch-ms quote timestamps, not invented
    # timestamps that relabel old prices. There is no provider connection here.
    ...
```

Both values are optional, not recommendations. Quote age must be an integer from
1 through 86,400,000 ms. Notional must be a positive bounded Decimal in the
account's quote currency. The ceiling applies to buys AND sells using quantity
multiplied by the original modeled fill price (including slippage, excluding
fees). Funds checks separately include fees. An oversized exit may need a smaller
explicit request. This is NOT portfolio risk, daily-loss or slippage-liquidity
modeling. Equality to either limit is accepted; rounded UI values are never used.

Policy is stored in an explicit version-2 SQLite row. Version-1 accounts remain
readable and receive deterministic preflight but no silently added freshness or
notional policy. Reopening accepts no policy overrides; pass only the path.
`session.admission_policy` returns a detached copy; all-None means no optional
policy. Existing accounts are not migrated. Do not erase/refund an account to
change risk policy. Older code cannot read policy-bearing version-2 rows.

## Price freshness

When quote-age checking is enabled:

- Caller timestamps are positive Unix milliseconds, not seconds, and may not be
  later than the local wall clock or older than the configured maximum age.
- Per-symbol timestamps must increase. Different prices at the same timestamp or
  older observations are rejected. Exact repeats do NOT renew freshness.
- A freshly opened session has NO valid receipt. Restored marks remain available
  for history/valuation but cannot authorize a new order, even if recent. Supply
  a strictly newer observation. Merely rereading/reposting an old quote is not it.
- Receipt age includes source age on arrival PLUS elapsed monotonic time. The
  maximum of that age and current wall age is used. Backward clocks fail closed.
- Quote and notional checks run again in execution before the account effect is
  committed. Failed ticker persistence does not establish a receipt.

`time.time_ns` and `time.monotonic_ns` provide the two local clock readings; the
caller/provider timestamp is not cryptographically authenticated. A same-user
program can lie about prices or edit/re-hash all database contents. This design
protects normal cooperating application flows, not malicious local code. It does
not claim suspend, physical power-loss, remote filesystem or live-feed validation.

## Compatibility and limitations

The v22 test that created UNKNOWN from a missing quote now injects a post-admission
adapter timeout instead. It still checks that an unresolved intent cannot bypass
the manager through the adapter. No safety test was removed or skipped.

PaperExchange remains immediate-full-fill spot market simulation; this does not
add limit matching, partial fills, deposits, shorting, real KIS/CCXT requests,
server authentication or a UI. Full snapshots and additional detached validation
are synchronous and bounded, not a high-throughput execution engine. Existing
POSIX store ownership and failure latches remain. Source/receipt checks are not
trusted attestations. Do not claim prices are current when displaying old marks.

## Run

With the existing project packages installed, from repository root:

```bash
mkdir -p .private
chmod 700 .private
python tools/paper_admission_demo.py --database .private/admission-demo-001.sqlite
python -m unittest discover -s tests -p 'test_order_paper_admission.py' -v
python -m unittest discover -s tests -p 'test_order_*.py' -v
```

The demo requires a new path. It uses synthetic prices, a 1000 USDT balance,
assumed 0.1% fee and 200 USDT order ceiling; no API key or external service.
It rejects a 300-unit notional, executes a 100-unit buy, reopens without refilling
cash, rejects a new exit using the restored mark, accepts a newer synthetic quote,
and exits. Expected: two fills, cash 999.8, fees 0.2. This is software evidence,
not investment performance.
