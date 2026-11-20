# Local crypto + stock PAPER HTML dashboard

The project now supports an optional **static, private HTML screen**
from the existing independent crypto/stock checkpoint auditors. This
is deliberately not a hosted broker dashboard, not an API service,
and not a true liveness check. All values represent offline simulated
economic state at the time the input records were processed.

## Produce an independently audited JSON snapshot

From the project root:

```sh
python tools/paper_portfolio_status.py \
  --crypto-fixture data/upbit-btc-1h-archive.json \
  --crypto-checkpoint data/upbit-btc-1h-paper.json \
  --stock-fixture data/toss-005930-paper.json \
  --stock-database data/stock-strategy.sqlite \
  --stock-initial-cash 1000000 --stock-fee-pct 0.1 \
  --fast 5 --slow 20 --allocation-pct 20 \
  > data/paper-portfolio-audit.json
```

The status tool independently replays each provided portfolio and
exits nonzero if any economic/strategy state is inconsistent. Never
display a stale previous output as evidence that a current poll passed.

## Render local HTML

```sh
python tools/paper_portfolio_report_html.py \
  data/paper-portfolio-audit.json \
  --output data/paper-portfolio-dashboard.html
```

Open the output locally in a web browser. It has a restrictive Content
Security Policy, inline CSS, and no JavaScript, external resources,
forms, tracking calls, tokens, or connection to real account systems.

Each card independently shows its underlying simulated stock or crypto
results. It intentionally **never sums KRW, USD, and USDT** without an
operator-defined verified FX rate and independent risk/audit policy.
Prices, balances and returns must not be presented as live.

It will reject a `live` mode marker, a non-audited state, ambiguous
portfolio types, and input files larger than 1 MB. The output is
created with exclusive file creation; it refuses replacement of an
existing report so previous audit evidence is preserved.

This is a lightweight local operator visualization and is NOT an
authenticated production monitoring service. Real-time state,
alerting, dashboard authentication, historical equity curves, cloud
deployment, KIS VTS orders, and real brokerage account reconciliation
remain unimplemented or unverified.
