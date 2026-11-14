# Revision-pinned paper order detail (v27)

## Design
Extend the read-only order browser with one exact-ID detail observation, bound to
both revision and digest from the displayed list. Reuse PaperSessionObserver's
single-row read, account validation, identity binding and nonblocking read gate.
Do not open a writer, change a schema, execute an order or perform reconciliation.

The report separates manager acknowledgement from the saved simulated account
fill. A saved fill with UNKNOWN/SUBMITTING does not become a confirmed order.
A missing fill is null, not a zero-priced fill and not proof of venue rejection.
Expose the existing fill ID, quantity, average price, modeled notional, recorded
fee/currency and source timestamp, not raw adapter output. Values are strings.
Notional uses the original PaperExchange Decimal context. It is not P&L or a cash
balance delta. Never infer a transport error or a historical timeline from state.

The page shows explanatory evidence categories and a non-executable follow-up
message, not a recovery button. Creation/update timestamps are local manager
record times; fill timestamps are source simulation times. They are not one clock.

Existing summary and order-page contracts remain unchanged. The new detail GET
requires client_id, revision and digest. Invalid query:400; missing ID in a valid
matching snapshot:404; changed snapshot:409; unavailable/corrupt source:503.
The UI discards details on page/filter/restart changes and ignores superseded
or timed-out responses. No unanchored retry, dynamic HTML injection or public
hosting. The same-origin controls are not user authentication.

## Limitations
Local simulation only, one immediate fill per order. No broker provenance,
profitability, live execution, signed audit log, partial-fill engine or ongoing
process monitoring. The underlying full-bundle validation remains bounded but
linear in stored orders. No historical snapshot retained.

## Run
On the development branch with existing packages installed:

```bash
python apps/dashboard/server.py --paper-session .private/existing-account.sqlite --port 8765
```
Open `http://127.0.0.1:8765/paper-orders.html` and select **상세 보기**. The existing
account is read only. A changed snapshot requires **최신 기록으로 처음부터**;
there is no automatic switch to newer detail or automatic order retry.

Python use (the record must belong to the chosen page):

```python
from trading_core.paper_session_observer import PaperSessionObserver
reader = PaperSessionObserver('.private/existing-account.sqlite')
page = reader.read_orders(status='unresolved')
if page['orders']['items']:
    key = page['orders']['items'][0]['client_order_id']
    detail = reader.read_order(key, expected_revision=page['revision'],
                               expected_digest=page['digest'])
```

HTTP: `GET /api/paper-session/order?client_id=<encoded-id>&revision=<page-revision>&digest=<page-digest>`.
All three parameters are mandatory. Path selection, unknown/duplicate parameters,
cross-origin calls and writes remain denied. HEAD returns no body. Not-found
responses are emitted only after the final source-file identity check completes.

## Contract and checks
`source=durable_paper_order_detail`, `schema_version=1`, `mode=paper`,
`read_only=true`, `live_enabled=false`. Metadata and the complete order object
must match the displayed list. `fill` is null or contains `venue_order_id`,
`quantity`, `average_price`, `notional`, `fee_amount`, `fee_currency`, `timestamp_ms`.
`evidence.classification` is one of `confirmed_fill`, `unconfirmed_account_fill`,
`unresolved_without_account_fill`. `cause=not_recorded` and `may_resubmit=false`
are deliberate limits; no transport diagnosis can be recovered from this schema.

The client uses the unchanged shared order-page validator and checks detail
identity, exact quantity, positive finite price, original 28-significant-digit
half-even notional arithmetic, fee currency and evidence consistency. Displayed
amounts are original strings; no Number conversion for quantities/prices/fees.
Time rendering requires a safe, representable millisecond timestamp. Unsupported
numeric/time representations fail closed rather than being rounded silently.

```bash
python -m unittest discover -s tests -p 'test_order_*.py' -v
node --test tests/paper_account_client.test.mjs tests/paper_orders_client.test.mjs tests/paper_order_detail_client.test.mjs
```

The new tests use synthetic accounts and actual SQLite/localhost HTTP. Offline
Chromium rendering uses the same modules with synthetic responses; it is separate
from actual HTTP evidence when browser-to-localhost access is blocked. No live
provider, trading activation or profitability claim follows from these tests.
