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
