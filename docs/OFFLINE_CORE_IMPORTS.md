# Offline core imports and observer JSON output

The read-only paper-account observer must not initialize unrelated notification
or configuration integrations. Importing `trading_core` exposes metadata; public
classes/functions are resolved on first access and cached as the original object.
The existing `__all__`, `__version__`, direct submodule imports and
`from trading_core import OrderManager` API are preserved. `dir(trading_core)`
includes the public names without importing them. An unknown name raises
`AttributeError`; a requested feature's dependency failure is not hidden/cached.

This uses Python's module `__getattr__`/`__dir__` protocol (PEP 562):
https://peps.python.org/pep-0562/

The change is not a global silent mode. Explicit notification imports still emit
their own diagnostics. Importing `*` requests every exported feature and therefore
can load optional integrations; it is not the offline/quiet entry point.

## Reproduced remote defect

At baseline 5dcf166, Order Outcome Safety run 41562958661 failed
`test_cli_missing_file_fails_without_creation_or_path_leak`. A warning from the
optional Telegram module preceded the JSON on stderr. The CLI's JSON assertion is
correct and stays unchanged. Suppressing logging or accepting only the last line
would hide the integration defect, so this repair isolates imports instead.

## Use

With the latest development branch and existing packages installed:

```bash
python tools/paper_session_status.py --database .private/control-demo-001.sqlite
python apps/dashboard/server.py --paper-session .private/control-demo-001.sqlite --port 8765
```

Open `http://127.0.0.1:8765/paper-account.html` locally. The endpoint is
`GET /api/paper-session`. The reader and UI are part of the prior v25 commit.
The existing CLI returns one JSON report on stdout and code 0 on success; missing,
corrupt or inaccessible state returns only `{"error":"paper_session_unavailable"}`
on stderr and code 2. It never creates a missing account or submits orders.
The supplied snapshots are not fresh market prices or proof of process liveness.

## Verification

```bash
python -m unittest discover -s tests -p 'test_order_import_boundaries.py' -v
python -m unittest discover -s tests -p 'test_order_*.py' -v
node --test tests/paper_account_client.test.mjs
```

The import-boundary tests start isolated stdlib-only child interpreters. Loader
fixtures test all legacy export mappings and explicit error/warning propagation;
actual OrderManager/PaperExchange/PaperTradingSession/observer code covers the
paper path. Remote CI separately exercises installed full-repository packages.
No live-provider, browser-to-server, long-duration or profitability claim follows.
