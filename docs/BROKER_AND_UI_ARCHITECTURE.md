# Broker adapters and dashboard architecture

## Adapter priority
1. Korea Investment & Securities (KIS) for Korean equities, and US equities only after confirming account and API permissions.
2. Upbit for KRW spot crypto.
3. Kiwoom REST for Korean equities.
4. Binance spot (jurisdiction and account eligibility dependent).

No adapter above is implemented or validated in this change. Official API specifications, rate limits, symbol conventions, authentication, token refresh, websocket reconnect, exchange calendars, market holidays, and order-state reconciliation must be verified before integration.

## Contracts
`packages/core/broker_interface.py` defines neutral instrument/order/receipt types and an explicit live guard. `paper_broker.py` is an in-memory interface stub only: accepted orders are NOT executed or reflected in account balances. Do not display stub results as real simulated performance.

## UI information architecture
- Overview: account/environment badge, equity, daily P&L, strategy health, stale-data warning, kill switch.
- Markets: watchlist, multi-timeframe candlesticks, overlays for confirmed swings/BOS/CHOCH/FVG, feed timestamps.
- Strategies: configurations, replay/backtest comparison, walk-forward metrics, versioned parameters.
- Orders: intent, risk approval, broker receipt, fill and reconciliation audit timeline.
- Portfolio: exposure, balances, realized/unrealized P&L, fees, drawdown.
- Settings: broker connection health, read-only API scopes, notifications, risk limits.

The frontend must read actual persisted server state, clearly label synthetic/paper/live modes, and never show invented performance. Mobile-first responsive layout; never put secret API credentials in browser storage. Cloudflare can host a static UI, but persistent trading execution requires a separately running trusted process.

## Production gates
Paper/live segregation, credentials security, request signing, explicit enablement, operator confirmation, independent kill switch, broker reconciliation, and failure-injection tests are required before any live orders.
