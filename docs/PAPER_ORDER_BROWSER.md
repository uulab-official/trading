# Revision-pinned paper order browser (v26)

## Run
The existing `/paper-account.html` summary retains its 100-row bound. Its new
link opens `/paper-orders.html`, which navigates every persisted order up to the
existing 10,000-order account limit. It never submits, cancels, reconciles,
normalizes or writes orders. Local paper simulation only.

```bash
python apps/dashboard/server.py --paper-session .private/control-demo-002.sqlite --port 8765
```
Open `http://127.0.0.1:8765/paper-orders.html`. Use an existing private session
file and keep the server local. No migration, new dependency or live activation.

## Filtering and exact values
The screen shows 50 rows per page and supports all, unresolved, submitting,
unknown and filled. These are the existing immediate market-fill session states,
not a new partial-fill exchange. Totals cover the whole validated account.
Unresolved orders come first; each group sorts by descending update time then
client ID, so equal timestamps have a deterministic order.

Quantities remain Decimal strings, including scientific notation like `1E-21`.
They are not converted through JavaScript Number. BigInt validates quantity
conservation. An account fill may coexist with UNKNOWN and confirmed fill zero
when its acknowledgement was lost; observation does not confirm that response.

## Consistency contract
The first page returns revision (decimal string) and SHA-256 content digest.
Every continuation requires BOTH. Any changed account commit returns HTTP 409
`paper_session_changed`, without a partial page. The UI keeps the previous page
with a warning and disables navigation until an explicit restart at page one.
Filter changes also start at page one. Missing/busy/corrupt sources are HTTP 503,
not zero-order results. Invalid inputs are HTTP 400. Corruption is validated
before an ordinary revision conflict is reported.

ANY commit, including quote updates, operating mode changes and reopen
normalization, can invalidate navigation. Rapidly updated accounts can require
repeated restarts. No historical snapshot cache, cross-revision merge, automatic
retry or immutable audit archive is provided. Stored status is not process
liveness, current venue evidence, fresh prices or order authorization.

## API and Python
`GET /api/paper-session/orders?offset=0&limit=50&status=all`

Continue with `revision=<first revision>&digest=<first digest>`. The limit is
1–100, offset is 0–10,000, and positive out-of-range offsets are invalid.
Canonical integers only: no leading zeros, signs or whitespace. Unknown or
duplicate parameters, including database-path parameters, are rejected.

```python
first = reader.read_orders(limit=50, status='unresolved')
if first['orders']['has_next']:
    page = reader.read_orders(offset=50, limit=50, status='unresolved',
        expected_revision=first['revision'], expected_digest=first['digest'])
```

The existing parameter-free summary endpoint is unchanged. Both views share
the original validated-row reader and nonblocking read gate. No writer lease,
normalization, repair or account initialization occurs. Identity/schema/hash/
accounting checks and existing size bounds remain. Reads may briefly contend
with the writer. No source paths are returned. Existing localhost Host/Origin,
no-store, CSP and GET/HEAD-only rules remain; these are not authentication.
These checks do not defend against malicious same-user database rewriting or
prove physical power-loss durability.

## Verify
```bash
python -m unittest discover -s tests -p 'test_order_paper_pages.py' -v
node --test tests/paper_orders_client.test.mjs
python -m unittest discover -s tests -p 'test_order_*.py' -v
```
Tests use synthetic accounts and actual SQLite/localhost HTTP. Offline browser
rendering uses fixtures and is separate from HTTP testing, not browser-to-server
end-to-end evidence. No real provider or profitability verification is claimed.
