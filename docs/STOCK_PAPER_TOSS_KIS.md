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
The crypto and stock engines use separate accounting paths. A static
[combined audit view](PAPER_PORTFOLIO_DASHBOARD.md) can display them without
aggregating currencies. The SQLite wrapper described below provides
transactional stock persistence and backup verification.

## Brokerage options verified on 2026-11-21

### Toss Securities

Official Open API: https://developers.tossinvest.com/

Toss Securities' official 2026-08-26 announcement confirms the general
availability of a combined domestic/US stock Open API. Official developer
documentation describes OAuth 2.0, market data, candles, quotes, account
assets, order creation/cancellation and WebSocket order events.
The documented API targets **live accounts**; a dedicated broker
sandbox / virtual-money order endpoint has NOT been confirmed.

**Approach:** Use the opt-in read-only collector described below only
with operator-provisioned credentials and an allowed IP. The canonical
OAuth and candle contracts are documented; successful account access,
availability, pricing and data permissions still require operator-side
verification. Pipe validated historical market data through a local
**paper** execution engine. Never use actual Toss order endpoints for a
paper test.

### Korea Investment & Securities (KIS)

Official API portal: https://apiportal.koreainvestment.com/apiservice

KIS documents separate VTS (virtual trading) and real trading
environments. This is the appropriate future option for testing
real broker-like order API semantics without exposing actual capital.
Do not assume a Toss sandbox exists because KIS provides one.

## Production steps still required

1. Provision user-controlled Toss credentials and an allowed IP, verify
   market-data permissions, and validate actual read-only candle responses.
2. Validate Korean market session/holiday/auction rules, US extended
   hours, symbols, stock splits, trading halts, FX, tax and fees.
3. Feed **completed** market bars only, preserve provenance and time zone,
   and apply causal next-bar simulated execution rather than current-bar
   close lookahead.
4. Validate the implemented SQLite persistence and backup/recovery workflow
   on the actual operating filesystem, and establish recovery targets.
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
against a hostile database administrator. Standalone stock backups and replay verification are implemented; see
[stock backup guide](STOCK_PAPER_BACKUP_MONITOR.md). Operational recovery
drills and recovery targets still need validation.
The crypto paper ledger remains separate.

## Official Toss reference

- Canonical OpenAPI JSON (v1.2.28 inspected on 2026-11-21):
  https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
- Official FAQ, including candle timing and auction exceptions:
  https://openapi.tossinvest.com/openapi-docs/faq.md
- Official OpenAPI index:
  https://openapi.tossinvest.com/openapi-docs/latest/api-reference/README.md
- Official market data:
  https://developers.tossinvest.com/docs/market-data
- Official OAuth:
  https://developers.tossinvest.com/docs/auth

Toss market data needs OAuth access, even for read-only price queries,
and requires registered allowed client IPs per the official guide.
The token request uses form encoding and client credentials; there is
one valid access token per client, and a new token invalidates the old
one. The candle response contains `result.candles`, ordered newest
first, with up to 200 rows and a `result.nextBefore` pagination cursor.
Do **not** use the order-creation endpoints when testing paper
transactions. The read-only collector below still requires actual
response and market-data permission verification before production use.

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

The ordinary eligibility delay remains **5 seconds** after the source
close (configurable with `safety_seconds`). The official FAQ documents
an exception: for domestic stocks not traded on NXT, the **15:30 Korea
time** row can accumulate closing-auction and after-hours closing-price
trades until **16:00**. The adapter has neither NXT eligibility metadata
nor a field for a variable aggregate interval or later availability.
It therefore conservatively **excludes every KRW source-close 15:30
row at all observation times**, including after 16:00 and on later days.
Changing `safety_seconds` cannot admit the row. It recognizes equivalent
timestamps expressed in UTC or another explicit offset. Other KRW
minutes and all USD rows retain the ordinary delay and unchanged
source-close-to-UTC-open conversion.

This missing source-close 15:30 row is intentional. In the normalized
opening-timestamp output it produces a gap at **15:29 Korea time**.
Do not fill that gap from the original aggregate or treat that aggregate
as a normal one-minute row: even after the observation-time value has
settled, replaying it as available at 15:30 would expose trades from
later in the afternoon and introduce lookahead.

**Bounded assumption:** This exclusion protects the documented 15:30
aggregate case; it is not an exchange calendar or proof that every
accepted row is immutable. It also discards ordinary 15:30 rows for
NXT-eligible stocks because the supplied schema cannot distinguish
them. The adapter does not reaggregate the data or validate a supplied
response's provenance. The FAQ describes KRX+NXT integrated domestic
data and late-reported US trades that can revise historical minute
bars, so the ordinary safety delay is not a general guarantee against
subsequent revisions.

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

## Opt-in Toss read-only quote collector with token reuse (2026-11-21)

`tools/toss_readonly_snapshot.py` reuses an operator-supplied access token
for exactly **one** stock 1-minute-candle GET (`GET /api/v1/candles`).
It requires no client ID or secret when a token is supplied. OAuth token
issuance (`POST /oauth2/token`) is available only with explicit
`--issue-token` opt-in and no supplied access token. These are the only
two reachable API routes; there are no account, balance or order calls.
Automatic redirects and environment-configured HTTP proxies are refused.
There is no live trading mode or automatic periodic service.

Use an access token provisioned through your own trusted local token
workflow and an allowed client IP. Never put tokens or credentials in
command-line options, Git, logged URLs, a public website, or ChatGPT.
The token environment variable contains the token itself, without a
`Bearer ` prefix:

```sh
# Set locally in your private shell/session; do not paste credentials here.
export TOSS_ACCESS_TOKEN="your-locally-provisioned-access-token"

python tools/toss_readonly_snapshot.py \
  --mode paper --allow-network-readonly \
  --symbol 005930 --currency KRW \
  --count 100 --adjusted true \
  --output data/toss-005930-authenticated-paper-bars.json
```

The supplied token always takes precedence over client credentials and
`--issue-token`. This includes credentials left in the environment from
an earlier workflow: they are not used or validated on the reuse path.
A supplied empty or malformed token is rejected before any request,
even when issuance was opted into; it never triggers a fallback.

### Deliberate one-shot issuance and migration

**Existing commands that set only `TOSS_CLIENT_ID` and
`TOSS_CLIENT_SECRET` now fail before network access.** Prefer adding
`TOSS_ACCESS_TOKEN` from your existing token workflow. If a new issuance
is deliberate, unset that variable and add `--issue-token`:

```sh
# Issuing a new token immediately invalidates the client's previous token.
unset TOSS_ACCESS_TOKEN
export TOSS_CLIENT_ID="your-local-client-id"
export TOSS_CLIENT_SECRET="your-local-client-secret"

python tools/toss_readonly_snapshot.py \
  --mode paper --allow-network-readonly --issue-token \
  --symbol 005930 --currency KRW \
  --count 100 --adjusted true \
  --output data/toss-005930-new-token-paper-bars.json
```

The [official Toss FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)
states that each client has one active access token and issuing another
immediately invalidates the previous one. Coordinate deliberate issuance
with other processes using that client. A newly issued token remains in
memory for this invocation; it is not printed, returned in the snapshot,
or saved in a disk cache. This one-shot option does not provision a
reusable token file for later runs.

Python callers have the same migration: use
`fetch_readonly_minute(..., access_token=existing_token)`, or explicitly
set `issue_token=True` with client credentials. The default is
`issue_token=False`. If both paths are configured, the supplied token
wins and only the candle GET is sent.

### Failure handling and saved evidence

All credential and request inputs used by the selected path are checked
before HTTP. Supplied observation timestamps must be timezone aware.
Bearer tokens follow the
[RFC 6750 section 2.1 character grammar](https://www.rfc-editor.org/rfc/rfc6750#section-2.1)
with a local limit of 4,096 characters. Issuance credentials are bounded
to 4,096 printable ASCII characters each and must not be blank. These
are collector validation limits, not claims about broker token sizes.
Control characters, line breaks and header injection are rejected.

The collector never retries, refreshes or reissues a token after a failed
GET, including HTTP 401, 403, 429 or 5xx responses and malformed payloads.
For a revoked or expired token, stop and update it through the operator's
token workflow; the collector does not change other processes' token
state to recover. Reuse removes per-snapshot rotation, but rate limiting,
expiry coordination, 429 handling and multi-process scheduling still
need an operating design before frequent unattended collection.

Only normalized paper OHLCV records are saved. OAuth credentials, bearer
tokens and raw market/account responses are excluded, and CLI failures
use generic text that does not echo upstream errors. Snapshot files are
created exclusively with mode `0600`; existing output paths, including
dangling final symlinks, are rejected before any HTTP. An existing non-directory
ancestor also fails before HTTP; this check does not guarantee later write
permissions or prevent filesystem changes after preflight. There is no token
cache. Invalid prices, malformed currency, timezone-less source
timestamps and no completed bars fail closed.

The real API path has **not** been exercised against a connected Toss
account, so successful data retrieval and production behavior remain
unverified. Tests use offline transports and cover GET-only reuse,
explicit POST+GET issuance, failed-request behavior, and proxy/redirect
refusal. Data acquisition is separate from the stock paper ledger, and
imported bars do not trigger simulated orders automatically.

Official source of truth: <https://openapi.tossinvest.com/openapi-docs/latest/api-reference/README.md>
and <https://openapi.tossinvest.com/openapi-docs/latest/api-reference/Apis/MarketDataApi.md>.

## Causal single-stock paper backtest (2026-11-20)

With a previously normalized Toss 1m paper fixture — either an offline
saved response converted using `toss_stock_fixture.py`, or a separately
opted-in **read-only** OAuth quotation snapshot — run the independent
cash-only MA-cross research tool:

```sh
python tools/stock_paper_backtest.py \
  data/toss-005930-paper-bars.json \
  --initial-cash 1000000 \
  --fee-pct 0.1 --allocation-pct 20 \
  --fast 5 --slow 20
```

This strategy evaluates an MA crossover using **completed bars only**
(up to the prior bar) and simulates any resulting buy/sell at the
**following available bar open**. The tool never queries account
balances or creates orders. It includes synthetic fees and a
whole-share affordability check, and marks remaining positions using
the final supplied close. This is a standalone backtest whose results
are not automatically persisted to the separate manual stock ledger.
A full forward-paper scheduler must be built with a market calendar,
exchange session coverage, delayed feeds, checkpointing, and operator
alerts before unattended evaluation.

The report includes `mode: paper`, `live_orders_enabled: false`,
cash, holdings, marked equity, return and simulated fills.
Observed historical performance **cannot establish future returns**;
real spreads, bid/ask impact, actual commissions, taxes, FX, splits,
corporate actions, queue priority and market closures are not modeled.


### KIS VTS quote-only preflight

The initial VTS integration is deliberately limited to a single
current-price GET on the virtual-trading host. It does not authenticate
or execute any orders and is distinct from the completed-bar stock
paper backtest. See [KIS VTS read-only quote runbook](KIS_VTS_READONLY_QUOTE.md).
