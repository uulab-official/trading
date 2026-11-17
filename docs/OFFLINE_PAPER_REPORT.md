# Offline paper report (standalone HTML)

This report is an inspectable **offline-paper-only** dashboard for the
existing deterministic fixture runner. It is **not** the Rust or legacy
SQLite trading dashboard and is never a live exchange connection.

## Local use

Run from the repository root on a POSIX local filesystem with the
documented Python offline dependencies installed.

```sh
./test-trading.sh
python tools/paper_checkpoint_audit.py \
  config/examples/offline_fixture.json \
  data/owner-trial/checkpoint.json

python tools/paper_report_html.py \
  config/examples/offline_fixture.json \
  data/owner-trial/checkpoint.json \
  --output data/owner-trial/report.html
```

Open `data/owner-trial/report.html` locally with a browser. If your
paper runner used a different checkpoint path, pass that exact path.

The report includes:

- Cash, marked spot equity, base holdings, return, fees, pending signal,
  and the daily-loss and drawdown entry-block flags
- Recorded simulated fills with UTC timestamps and client-order IDs
- A **fixture close-price** chart (deliberately not labeled portfolio equity)
- SHA-256 digest of the audited checkpoint and explicit paper-only warning

Before generating any HTML, it independently replays the processed
fixture prefix and verifies the entire persisted economic and risk state.
If either source changes between audit and rendering, hash verification
aborts. The report never repairs or writes the checkpoint; it only
creates/replaces the explicitly specified HTML output. Output cannot
point to the checkpoint or fixture. The output is written through a
temporary file and atomically renamed with owner-only permissions for
new files on POSIX systems. If replacing an existing file, inspect
existing ownership/permissions; do not publish the report to an
internet-accessible directory.

The generated HTML has inline CSS and SVG, no JavaScript, no CDN,
no external imagery or remote requests, and a restrictive CSP.
It is an offline snapshot, **not** a continuously updating service.

## Validation

```sh
python tools/offline_checks.py
python tools/offline_checks.py --include-backtest
```

The dashboard test covers simulated-fill rendering, unchanged checkpoint
bytes, balance tampering, output collision protection, static/CSP output,
and HTML escaping. The suite blocks network and DNS.

## Release blockers

This dashboard cannot demonstrate live broker connectivity,
order reconciliation, slippage accuracy, strategy profitability,
feed freshness, or production readiness. Real order activation remains
out of scope until the separate release gates in
[OFFLINE_CHECKPOINT_AUDIT.md](OFFLINE_CHECKPOINT_AUDIT.md)
are independently validated.
