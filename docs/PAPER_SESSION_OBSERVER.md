# Read-only durable paper account monitor (v25)

## Purpose
Observe the current persisted local paper-account bundle without opening a
PaperTradingSession, claiming its writer lease, normalizing orders, querying a
broker, or changing its operating mode. Existing account versions 1, 2 and 3
remain unchanged. This is an opt-in view, not live trading or a process monitor.

The monitor reuses the existing bundle/accounting validators. One read covers
account balances, saved marks, costs, order intents, policy and operating control
from the same row. It does not combine independent polling responses.

## Run on the current development branch
Install the repository packages as for existing demos; this monitor adds no new
runtime dependencies. Use normal Python, not -S, with the existing package setup.

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
mkdir -p .private
chmod 700 .private
# Optional synthetic example. This MUST be a new path, never an existing account.
python tools/paper_control_demo.py --database .private/control-demo-001.sqlite
# Read-only CLI; it also works while the account owner is running.
python tools/paper_session_status.py --database .private/control-demo-001.sqlite
# Server binds to 127.0.0.1 only; do not expose/proxy publicly.
python apps/dashboard/server.py --paper-session .private/control-demo-001.sqlite --port 8765
```

Open `http://127.0.0.1:8765/paper-account.html`. The JSON endpoint is
`GET /api/paper-session`, with no path/query parameters. The CLI prints one JSON
report and exits 0 on a validated observation; errors print only a fixed JSON
error to stderr and exit 2. No output file, DB or lock is created by the reader.

The account owner still owns all submission, reconciliation and mode changes.
There are deliberately no web execution, recovery or control buttons.

## What the screen means
- **Operating mode** is the stored decision, NOT evidence the process is alive.
- **Cash/fees/holdings** come from the validated persisted paper account. Quote
  currencies are retained; no conversion or double subtraction of fees.
- **Marked equity** uses stored prices, NOT current market prices or realizable
  liquidation value. Missing positive-holding marks produce null, never zero.
- **Quote age** uses the original saved source timestamp, not the refresh time.
  Fresh quote receipts live in the owner process and are not observable here.
- **Unresolved orders** include stored SUBMITTING and UNKNOWN. The reader never
  turns SUBMITTING into UNKNOWN or a lost acknowledgement into a confirmed fill.
  A simulated account fill may already exist while its acknowledgement is unknown.
- **Revision** is a decimal string so JavaScript does not lose integer precision.
  Amounts remain decimal strings. Display truncation after eight decimal places
  uses an ellipsis, with the full source value in a tooltip and the JSON.
- **Read completed** means a saved bundle was checked, not that trading is safe.

Counts use the entire bounded account (up to 10,000 orders). Order details and
holdings are limited to 100 items each; unresolved orders are shown first.
Truncation is explicitly reported. A missing/busy/corrupt source is HTTP 503,
not an empty account. The browser retains the last good response only with a
visible previous-inspection warning; it never presents stale control as current.

## Read isolation and failure boundaries
The reader opens `mode=ro&cache=private`, enables query_only, and copies one row
inside a short transaction. SQLite's ordinary locking stays enabled. There is no
`immutable=1`, `nolock`, write-store constructor, lock-file open, migration or
repair. The transaction is closed before expensive bundle validation, limiting
writer blocking. A rollback-journal writer may briefly conflict with a reader;
contention fails closed and can be retried by the next explicit/periodic read.

A private regular file, normal rollback-journal header, exact schema, bounded
payload, digest, account arithmetic and version agreement are required. The
reader has a 64 MiB DB-file bound and retains the existing 16 MiB payload bound.
WAL is rejected before SQLite opens it; this reader does not create WAL sidecars.
A hot rollback journal requiring recovery is not repaired by this observer.

The first successful observation pins inode, account identity, immutable settings
and policy. Later reads reject replacement, observed revision regression,
same-revision/different-content and control-generation regression. Restart the
monitor deliberately to select another account. These are consistency checks,
not signed provenance or a defense against malicious same-user modification.
Absolute source paths and raw DB error details are not returned over HTTP.

Optional replay/research readers are loaded only for their own routes; absence
of an unrelated reader does not prevent the paper-account observer from running.
Existing server Host/Origin/Fetch-Site checks, CSP, no-store and no-write responses
are retained. These browser-origin controls are not authentication.

SQLite references: https://www.sqlite.org/isolation.html and
https://www.sqlite.org/uri.html (mode=ro and immutable caveats).

## Verify
```bash
python -m unittest discover -s tests -p 'test_order_*.py' -v
node --test tests/paper_account_client.test.mjs
```

Tests use synthetic local accounts and actual SQLite/localhost HTTP. No real
provider success, profitability, long-duration operation, cross-platform or
browser-to-server end-to-end verification follows merely from these tests.
