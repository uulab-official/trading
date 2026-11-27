# Local paper operator dashboard

Use the same archive, executable, scenario template, session directory,
fixed start and failure budget as the paper-forward supervisor.

```sh
python tools/binance_paper_ops.py \
  --archive data/binance-history/btc.sqlite --template data/btc-paper-template.json \
  --binary apps/server/target/release/trading-server --session-dir data/btc-forward \
  --start-open-ms SESSION_START_UTC_MS --serve
```

Open http://127.0.0.1:8765/ on that machine. The HTML refreshes every 15 seconds.
The server binds only IPv4 loopback and rejects nonmatching Host headers.
It has no order controls, remote dependencies, arbitrary file serving, or
write routes. Do not expose it through a proxy or tunnel; this is a trusted
local operator interface, not a remotely authenticated service.

The default view reads the audited public archive, saved supervisor status
and the saved account metadata/head/row count. It labels the last successful
simulation as historical and distinguishes missing, halted, stale,
unsynchronized, risk-blocked, capacity-exhausted and reported-current states.
A matching head is NOT a full economic audit. The /api/status route emits the
same JSON; /healthz returns 503 when the local paper pipeline is not current
and unblocked. This is not a broker or live strategy health probe.
The program does not fetch new market data or launch an execution process
when serving HTTP.

Remove --serve for one-shot JSON, use --format html for a static report, or
add --deep-audit for an explicit one-shot read-only Rust replay against the
current archive and retained account checkpoints. The binary runs only
paper-forward --audit-only. Failure, including concurrent changes between
snapshots, produces an unavailable state rather than optimistic success.
A deep audit does not verify actual exchange fills or market profitability.

The local status file and archive are in the same failure domain unless the
operator independently retains copies. HTTP refreshes repeat the archive audit;
there is no latency or load guarantee for large histories. No daemon has been
installed or started by this change.

Tests: python -m unittest discover -s apps/server/tests -p test_binance_paper_ops.py.
