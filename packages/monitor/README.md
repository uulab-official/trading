# trading-monitor

Monitoring and notification components for the
[trading monorepo](../../README.md), supporting Python 3.10 and newer.

The importable package provides Telegram notification helpers. The adjacent
`web_dashboard.py` contains the separate FastAPI/WebSocket dashboard service;
that service's deployment dependencies are not included in this package.

Install the workspace dependencies described in the root README, then run
`python -m pip install -e packages/monitor` from the repository root.

Run `python -m pytest packages/monitor/tests/` for the local checks. Timestamp
regressions replace Telegram sending with an asynchronous mock and invoke the
web handlers directly with stub route registration. They do not start an HTTP
server or send external messages.
