# Stock paper trading (Toss Securities / KIS integration plan)

## What works now

`packages/core/trading_core/stock_paper.py` provides a **credential-free,
offline stock cash-book emulator**. It is NOT connected to Toss Securities,
Korea Investment & Securities, or a real brokerage account. Callers must
supply prices explicitly. It supports:

- Independent KRW and USD cash books (no implicit FX conversion)
- Whole-share buy/sell simulations, with cash/holding checks and no leverage
- Explicit simulated fees and equity marked using caller-supplied prices
- Repeated client IDs deduplicated only when their complete terms match
- Strict refusal of missing marks, unaffordable purchases and short selling

The emulator is in-memory and intentionally does not claim crash-safe
portfolio persistence, authentic order fills, or investment profitability.
The existing crypto offline runner and this stock book are currently
**separate accounting paths**, not yet one multi-asset dashboard.

## Brokerage options verified on 2026-11-20

### Toss Securities

Official Open API: https://developers.tossinvest.com/

Toss Securities' official 2026-08-26 announcement confirms the general
availability of a combined domestic/US stock Open API. Official developer
documentation describes OAuth 2.0, market data, candles, quotes, account
assets, order creation/cancellation and WebSocket order events.
The documented API targets **live accounts**; a dedicated broker
sandbox / virtual-money order endpoint has NOT been confirmed.

**Proposed approach:** Keep Toss in public/documented-data inspection
mode until its authentication, pricing, availability, permission rules,
and data-license terms are independently verified. Pipe validated
historical market data through a local **paper** execution engine.
Never use actual Toss order endpoints for a paper test.

### Korea Investment & Securities (KIS)

Official API portal: https://apiportal.koreainvestment.com/apiservice

KIS documents separate VTS (virtual trading) and real trading
environments. This is the appropriate future option for testing
real broker-like order API semantics without exposing actual capital.
Do not assume a Toss sandbox exists because KIS provides one.

## Production steps still required

1. Confirm Toss market-data authentication and scope, obtain user-controlled
   access through official authorization, and fetch read-only candles.
2. Validate Korean market session/holiday/auction rules, US extended
   hours, symbols, stock splits, trading halts, FX, tax and fees.
3. Feed **completed** market bars only, preserve provenance and time zone,
   and apply causal next-bar simulated execution rather than current-bar
   close lookahead.
4. Persist whole-share paper portfolio state with transactionally sound
   cash/fills/checkpoints, idempotency and crash recovery.
5. Reconcile actual sandbox fills, partial cancellations and account
   balances in KIS VTS before any broker integration is considered safe.
6. Implement multi-asset authenticated operator dashboard and alerts.
7. Keep actual-money order endpoints disabled until security reviews,
   risk budgets, operational drills and explicit operator approval.

Run the fully offline targeted regression suite:

```sh
python tools/offline_checks.py
```

A passing synthetic test is evidence of calculation correctness under
its assumptions, not of investment returns or live order compatibility.

## Durable stock paper milestone (2026-11-20)

`packages/core/trading_core/stock_paper_ledger.py` now persists
simulated stock fills in a local SQLite WAL ledger. Before every new
fill, it replays the existing ordered log into the original cash
simulator, compares each saved execution record with the reconstructed
result, then commits one new record under `BEGIN IMMEDIATE`. This
makes identical client-ID replays duplicate-free and prevents two
local writers from overspending the same cash balance.

**Manual paper-only commands:**

```sh
python tools/stock_paper_cli.py \
  --mode paper --database data/stock-krw-paper.sqlite \
  --currency KRW --initial-cash 1000000 --fee-pct 0.1 \
  trade --client-id demo-buy-001 --symbol 005930 \
  --side buy --shares 1 --price 80000

python tools/stock_paper_cli.py \
  --mode paper --database data/stock-krw-paper.sqlite \
  --currency KRW --initial-cash 1000000 --fee-pct 0.1 \
  report --mark 005930=81000
```

All prices are **operator-supplied synthetic assumptions** here. The
report refuses to value holdings unless every held symbol is given
an explicit positive mark. It does not fetch Toss prices, model stock
splits, tick sizes, markets, actual fees/taxes, exchange holidays,
partial broker fills, FX, or account balances.

The database configuration (currency, initial cash, fees) is fixed
upon initialization; mismatched restarts are rejected. The stored
execution log provides local crash persistence but not signatures
against a hostile database administrator. Archive and operational
backup procedures for this new stock database are still pending.
The crypto paper ledger remains separate.

## Official Toss reference

- Official OpenAPI index:
  https://openapi.tossinvest.com/openapi-docs/latest/api-reference/README.md
- Official market data:
  https://developers.tossinvest.com/docs/market-data
- Official OAuth:
  https://developers.tossinvest.com/docs/auth

Toss market data needs OAuth access, even for read-only price queries,
and requires registered allowed client IPs per the official guide.
Do **not** use the order-creation endpoints when testing paper
transactions. A production read-only connector must be separately
written and verified against the canonical OpenAPI schema and
market-data permissions before connecting these prices to the ledger.

## Offline Toss 1-minute candle adapter (2026-11-20)

The documentation-backed adapter in
`packages/data/trading_data/toss_stock_candles.py`
accepts **caller-saved** JSON responses from Toss
`GET /api/v1/candles?symbol=...&interval=1m`.
It requires no OAuth, network access, secrets, or real account.

Toss 1m candles use **the candle CLOSE timestamp** (e.g. a bar
covering 09:29–09:30 carries `timestamp=09:30`), whereas the old
cryptocurrency fixture uses an opening timestamp. This adapter
converts 1m close timestamps into Unix-ms **opening** timestamps,
rejects timezone-less or duplicate timestamps, and drops bars too
close to the caller-supplied observation time.

Run after manually saving the read-only market response locally:

```sh
python tools/toss_stock_fixture.py \
  --input local-toss-minute-response.json \
  --output data/toss-005930-paper-bars.json \
  --symbol 005930 --currency KRW --interval 1m \
  --observed-at 2026-03-25T09:31:10+09:00
```

The output is `mode: paper` only, annotated as
`operator_supplied_response_not_authenticated`; it **does not**
place or simulate orders by itself. New files are created exclusively,
never overwrite source or a preexisting candidate, and no token or
account response fields are copied to the paper output.

**Use genuine 1m responses only.** Toss 1d candles use local-midnight
trading-date timestamps and cannot be safely interpreted as 1m.
Market-session holes are not automatically filled. Before any
end-to-end strategy backtest, add official exchange-calendar validation,
replay the data from a recorded provenance/source, and verify the
data adjustment choice (`adjusted=true/false`).

This adapter is not a Toss OAuth connector or proof that a user's
credentials/IP allow API access. A future live read-only connector
must confirm the canonical JSON schema, allowed IP, query budget,
token handling, and market-data terms before production operation.

## Opt-in Toss read-only OAuth quote collector (2026-11-20)

A separate `tools/toss_readonly_snapshot.py` collector now implements
exactly **two** documented Toss Open API calls: OAuth token issuance
(`POST /oauth2/token`) and one stock 1-minute-candle read
(`GET /api/v1/candles`). It does **not** include any private-account,
balance, or order endpoint. Automatic HTTP redirects are refused.
There is no live trading mode and no automatic periodic service.

This is opt-in and requires the operator's own valid Toss developer
credentials and allowed client IP configuration. Never put credentials
in command-line options, Git, logged URLs, a public website, or ChatGPT.
Use secured local environment variables:

```sh
# Set locally in your private shell/session; do not paste credentials here.
export TOSS_CLIENT_ID="your-local-client-id"
export TOSS_CLIENT_SECRET="your-local-client-secret"

python tools/toss_readonly_snapshot.py \
  --mode paper --allow-network-readonly \
  --symbol 005930 --currency KRW \
  --count 100 --adjusted true \
  --output data/toss-005930-authenticated-paper-bars.json
```

Only the normalized paper OHLCV records are saved; OAuth secrets,
bearer token, and raw market/account response are never placed in
the resulting file. Every invocation issues a new OAuth token, which
can invalidate previously issued tokens for the same client according
to Toss's official token contract. **Do not** run this on a frequent
scheduler until safe token caching/reuse, rate limiting, IP authorization,
429 behavior, and multi-process coordination are implemented.
The collector makes no automatic retry. Invalid prices, malformed
currency, timezone-less timestamps, and no completed bars fail closed.

The real API path has **not** been exercised against a connected Toss
account, so successful data retrieval and production behavior remain
unverified. Tests inject an offline fake network adapter and ensure only
the OAuth and read-only candle URLs are invoked. Data acquisition is
separate from the stock paper ledger, and imported bars do not trigger
simulated orders automatically.

Official source of truth: <https://openapi.tossinvest.com/openapi-docs/latest/api-reference/README.md>
and <https://openapi.tossinvest.com/openapi-docs/latest/api-reference/Apis/MarketDataApi.md>.
