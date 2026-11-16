# Open-source benchmark (2026-10-31)

Research references only. No upstream code copied.

- Freqtrade: https://github.com/freqtrade/freqtrade — strategy configuration, dry-run and backtesting.
- QuantConnect LEAN: https://github.com/QuantConnect/Lean — event-driven backtest and execution architecture.
- NautilusTrader: https://github.com/nautechsystems/nautilus_trader — order lifecycle and backtest/live parity.
- Hummingbot: https://github.com/hummingbot/hummingbot — exchange connectors and market data handling.
- Microsoft Qlib: https://github.com/microsoft/qlib — factor research and ML pipelines.
- KIS official API: https://github.com/koreainvestment/open-trading-api — Korean and overseas equities examples, authentication and websocket.
- CCXT: https://github.com/ccxt/ccxt — exchange metadata and crypto APIs.

## Adopt
Keep one event model, broker adapter boundary, durable idempotent order journal, paper-first operation, independent risk gate, and explicit data provenance. Evaluate costs, walk-forward out-of-sample performance, trading calendars and stale feeds. The UI must display actual persisted state rather than placeholder performance.

## Before live operation
Require tests for duplicate order IDs, transport timeouts, crash recovery, reservation concurrency, reconciliation, broker sandbox integration and operator-approved activation. Current implementation does not meet these gates.

## License review
Check each upstream license and attribution requirements before copying code or adding a dependency.
