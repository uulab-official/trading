# KIS VTS read-only daily-order snapshot

`tools/kis_vts_order_snapshot.py` collects a bounded set of cumulative order
observations from the Korea Investment Securities virtual-trading service.
It uses one fixed daily-inquiry GET route. It does not issue tokens, submit or
cancel orders, infer fills, reconcile balances, or change the stock-order journal.

This increment was verified with synthetic responses and forbidden socket/DNS
access. **No authenticated broker request or actual account validation was
performed.** The implementation is an observer; its output is not execution
evidence for the local simulation.

## Operator command

The operator must already have the five environment values below. The observer
does not acquire, refresh, cache, or write these values to a file.

| Environment variable | Input |
| --- | --- |
| `KIS_VTS_ACCESS_TOKEN` | Existing VTS access token, without the `Bearer ` prefix |
| `KIS_VTS_APPKEY` | VTS application key |
| `KIS_VTS_APPSECRET` | VTS application secret |
| `KIS_VTS_CANO` | Eight ASCII digits identifying the account |
| `KIS_VTS_ACNT_PRDT_CD` | Two ASCII digits identifying the account product |

Use a new output filename in an existing private directory. Choose one ISO date
within the past 30 Korean calendar dates, including today. The example date must
be changed once it falls outside that window.

```bash
python tools/kis_vts_order_snapshot.py \
  --mode paper \
  --allow-vts-readonly \
  --date 2026-11-21 \
  --account-ref paper-vts-review \
  --max-pages 20 \
  --output /existing-private-directory/kis-vts-orders.json
```

`--mode paper`, `--allow-vts-readonly`, `--date`, `--account-ref`, and `--output`
are required. `--max-pages` defaults to 20 and accepts integers from 1 through
20. There is no production-host, order, cancellation, token-issuance, journal,
or account-reconciliation option.

`account_ref` is an opaque local label, not an authenticated account binding.
It starts with an ASCII letter, is at most 64 characters, and contains only
letters, digits, `.`, `_`, and `-`. A label containing the supplied full account
number or a supplied credential is refused. Use a descriptive alias that carries
no account information. The output always sets `account_binding_verified=false`.

The command reserves the destination exclusively with mode `0600` before making
any request. This detects collisions, inaccessible destinations, missing
parents, non-directory ancestors, and final symlinks before account access.
An existing file, including a dangling final symlink, is never replaced. The
private reservation is empty while collection runs; consume the file only after
exit status 0. JSON is written only after complete pagination and validation.
Handled collection/write failures remove the command's own output inode, and
an output replaced concurrently is preserved rather than mistaken for success.
A process forcibly killed during writing can leave an incomplete file; this is
not an atomic-publish filesystem protocol.

Exit status 0 means a complete, locally validated observation file was created.
Exit status 2 means refusal; stdout/stderr use fixed messages and never echo
arguments, paths, account queries, credentials, cursors, or broker messages.
Request inputs and the output destination are checked before HTTP. There is no
retry or token refresh after any failure.

## Fixed request and paging contract

The only built-in transport destination is:

```text
GET https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/inquire-daily-ccld
tr_id: VTTC0081R
```

The query selects the same date for `INQR_STRT_DT` and `INQR_END_DT`, all sides
with `SLL_BUY_DVSN_CD=00`, all execution categories with `CCLD_DVSN=00`, reverse
order with `INQR_DVSN=00`, and all funding categories with `INQR_DVSN_3=00`.
`EXCG_ID_DVSN_CD=KRX` is explicit because VTS supports KRX. `PDNO`, `ODNO`,
`ORD_GNO_BRNO`, and `INQR_DVSN_1` start empty. Both cursor query parameters start
empty. The request includes existing bearer/app credentials, `custtype=P`, and
JSON content headers. No request body is sent.

| Response `tr_cont` | Observer action |
| --- | --- |
| `M` or `F` | Copy both exact body cursor strings into the next uppercase query parameters; set request `tr_cont=N` |
| `D` or `E` | Finish collection |
| Missing, blank, or any other value | Refuse the entire result |

The first request has an empty `tr_cont` header. Cursors are opaque: their
leading/trailing spaces are retained, and they are never included in output.
A continued page must supply both nonblank printable ASCII cursor strings,
each at most 100 characters. A repeated cursor pair is refused. Terminal pages
may contain account-bearing cursor values; those values are discarded.

Local bounds are 15 records per VTS page, 20 pages, 300 records total, a
2,000,000-byte response body per page, and a ten-second transport timeout per
request. A caller may lower the page limit. Reaching a limit while continuation
is still indicated refuses the entire result; it never returns a truncated
result with `pagination_complete=true`. HTTP status must be 200 and body
`rt_cd` must be the string `"0"` on every page.

The built-in urllib opener disables environment proxies and all redirects,
including explicit HTTP 308 refusal on Python 3.10. The host, route, transaction
identifier, method, and account query shape cannot be selected through CLI flags.

## What the output means

Each successful result includes these fields:

| Field | Meaning |
| --- | --- |
| `schema_version=1` | Local snapshot format |
| `mode="paper"`, `live_orders_enabled=false` | No live-order capability is enabled |
| `provider="kis_vts_readonly_order_snapshot"` | The fixed observer implementation |
| `market="KRX"`, `currency="KRW"` | Supported observation scope |
| `account_ref` | Operator-supplied local alias |
| `account_binding_verified=false` | Alias was not authenticated against a broker account |
| `inquiry_date` | Requested ISO calendar date |
| `observed_at_utc` | Collection start, captured and validated before the first GET |
| `pagination_complete=true` | The bounded sequence reached a documented `D`/`E` terminal page |
| `atomic_snapshot_verified=false` | No assertion that all pages represent one unchanged account state |
| `economic_reconciliation_verified=false` | No reconciliation to individual executions, cash, positions, or a journal |
| `pages_fetched`, `row_count` | Page and retained-record counts, not unique trading intents or trade counts |
| `rows` | Allowlisted cumulative broker observations |

Pagination is sequential. Orders can change or appear during collection;
terminal pagination does not make the result an atomic, point-in-time account
snapshot. `observed_at_utc` is neither the final-response time nor a broker
execution timestamp.

The collector requests all account records with `PDNO=""` but supports only
six-digit domestic stock codes in the response. **Any unsupported record,
including a seven-character ETN identifier, refuses the whole snapshot.** It
does not silently omit that record and claim to have collected the account.

### Retained row fields

| Fields | Observation |
| --- | --- |
| `ord_dt`, `ord_gno_brno`, `odno` | Broker order date, numbering branch, order identifier |
| `orgn_odno` | Original-order reference, preserved verbatim even when empty or all zeros |
| `pdno` | Six-digit domestic symbol |
| `sll_buy_dvsn_cd`, `ord_dvsn_cd` | Side code and order-division code |
| `ord_qty`, `ord_unpr` | Reported order quantity and price |
| `tot_ccld_qty`, `tot_ccld_amt`, `avg_prvs` | Reported cumulative execution quantity, amount, and average price |
| `rmn_qty`, `cncl_cfrm_qty`, `rjct_qty` | Reported remaining, cancellation-confirmed, and rejected quantities |
| `cncl_yn`, `ord_tmd` | Raw cancellation flag and order clock value |
| `excg_id_dvsn_cd` | Retained as `KRX` only when this newer response field is present |

All fields in this table except the exchange field are required. The older
official response example omits the exchange field, so absence is preserved.
If present, the portal's mixed-case `excg_id_dvsn_Cd` becomes the lowercase key.

Identifiers retain leading zeros. Repeated identities within or across pages
are refused, including records with changed quantities. The identity key is
date plus numerically normalized branch/order identifiers, so changing zero
padding cannot bypass the duplicate check. This is a local integrity policy;
the public schema does not promise uniqueness or fixed zero-padding rules.
Different child order identifiers with a shared original reference remain
separate observations of related history.

`orgn_odno` links are not extra executed orders. No row is converted into a
journal event, and quantities/amounts of linked rows are never summed.
The observer also discards `output2` summaries rather than repeatedly adding
summary totals from multiple pages. An absent or all-zero original reference
does not prove that a record may be inserted as an original order in a journal.

### Strict local acceptance policies

The public schema supplies field names and maximum string lengths but leaves
some numeric precision, flag, and time semantics unspecified. The observer
uses the following conservative policies; they are **not claims of a complete
broker enum or wire grammar**:

- Quantities must be nonnegative ASCII integer strings of at most 10 characters.
  Order price and cumulative amount must be nonnegative ASCII integer strings
  of at most 19 characters.
- `avg_prvs` accepts a nonnegative plain decimal string of at most 19 characters,
  including the decimal point. No exponent, sign, `NaN`, or infinity is accepted.
  The public source does not establish its fractional scale. Normalization
  removes redundant leading zeros and trailing fractional zeros using string
  operations, preserving the exact numeric value without float arithmetic.
- Branch identifiers contain 1..5 ASCII digits. Order identifiers contain
  1..10 ASCII digits and must be nonzero. Original-order references may be empty
  or contain 1..10 digits; no absent-sentinel semantics are inferred.
- Side codes must be `01` or `02`; order-division codes must be two ASCII digits.
  Cancellation flags are accepted only as empty, `Y`, or `N`. The portal's
  example explicitly contains an empty cancellation flag, but gives no full
  cancellation enum.
- `ord_tmd` must be six ASCII digits forming a valid `HHMMSS` clock value.
  The schema labels this as order time and gives length six; it does not define
  an execution timestamp or a complete clock grammar.
- Exact duplicate JSON keys are refused at every object depth. Header and body
  casing is normalized; conflicting case aliases are refused. Numeric JSON
  literals are bounded before conversion on Python 3.10, and nonfinite values
  are refused. Required numeric observations must still be JSON strings.

The observer does not force accounting identities between quantities, amounts,
and flags because linked-order and cancellation propagation are not established
by this endpoint's public contract. Unexpected data is either preserved within
the accepted shape or causes refusal; it never becomes an inferred fill.

The official rejected-order example has order quantity 10, executed quantity 0,
remaining quantity 0, rejected quantity 10, and a blank cancellation flag.
**Zero remaining quantity therefore does not establish a fill.** Order time
is not execution time. This endpoint does not supply documented individual
execution identifiers or an exhaustive lifecycle-state enum.

Contacts (`ctac_tlno`), IP addresses (`inqr_ip_addr`), employee identifiers
(`ordr_empno`), descriptive messages/names, account fields, cursors, and other
unneeded raw values are not emitted. The fixed output contains only the
allowlisted fields and local collection metadata.

## Python API and offline verification

```python
fetch_order_snapshot(
    inquiry_date,
    *,
    account_ref,
    token,
    appkey,
    appsecret,
    cano,
    acnt_prdt_cd,
    max_pages=20,
    opener=_open_vts_readonly,
    now=None,
)
```

`now`, when supplied, is a callable returning a timezone-aware `datetime`.
`opener(request, timeout=10)` is injectable for fake-HTTP tests; a custom opener
must preserve the transport safety contract. The function returns a complete
dictionary or raises a fixed `ValueError` with upstream exception context
suppressed. It has no journal parameter, imports, file writes, or order-state
mutation.

Run the focused standard-library tests from the repository root:

```bash
python -m unittest discover -s apps/server/tests -p test_kis_vts_order_snapshot.py -q
```

The suite exercises exact request fields, continuation and terminal signals,
empty responses, case conflicts, duplicate JSON/order identities, zero-padding,
private-field filtering, numeric and date boundaries, incomplete pagination,
transport failures without retry, proxy/redirect refusal, and output collisions
and replacement. It also launches the actual CLI with `-S`, fake HTTP, and
socket/DNS guards, and checks existing journal/ledger bytes remain unchanged.

Remaining real-account validation includes actual VTS authorization, currently
returned record shapes, continuation behavior for a populated account, changed
orders during pagination, and documented representations of amendments,
cancellations, rejections, and fractional averages. Such validation must be
separately authorized and must not turn this collector into an order sender or
an automatic accounting bridge.

## Primary contract sources

Contract research used the public KIS portal and official repository at commit
`74b0a082fa67a0be5bf53f561719c853890ed2df` (reviewed 2026-11-21):

1. [Pinned official daily-inquiry example](https://github.com/koreainvestment/open-trading-api/blob/74b0a082fa67a0be5bf53f561719c853890ed2df/examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py)
   — VTS host/transaction choice, explicit query parameters, 15-row VTS page
   limit, and continuation cursor use.
2. [Official public daily-inquiry API page](https://apiportal.koreainvestment.com/apiservice-apiservice?/uapi/domestic-stock/v1/trading/inquire-daily-ccld)
   and [public schema JSON](https://apiportal.koreainvestment.com/api/apis/guide/property/bc51f9f7-146f-4971-a5ae-ebd574acec12)
   — request/response definitions, `F/M` and `D/E` meanings, field lengths,
   KRX-only VTS scope, and the rejected-order example.
3. [Pinned official output-checking example](https://github.com/koreainvestment/open-trading-api/blob/74b0a082fa67a0be5bf53f561719c853890ed2df/examples_llm/domestic_stock/inquire_daily_ccld/chk_inquire_daily_ccld.py)
   — inspected alongside the portal; the observer uses the portal's canonical
   `cncl_cfrm_qty`, not the example mapping's `cnc_cfrm_qty` typo.
4. [Official amendment/cancellation inquiry schema](https://apiportal.koreainvestment.com/api/apis/guide/property/d4537e9c-73f7-414c-9fb0-4eae3bc397d0)
   and [official cancellation schema](https://apiportal.koreainvestment.com/api/apis/guide/property/4bfdfb2b-34a7-43f6-935a-e637724f960a)
   — context for related original/child identifiers; these endpoints are never
   called by this tool.

The current official inquiry sample's recursive positional argument order does
not match its signature. This implementation builds each next query explicitly.
Its terminal check also requires documented `D/E`, instead of treating every
non-`F/M` response as complete. Those stricter local policies are intentional.
