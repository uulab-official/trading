# KIS VTS offline order preparation

This tool creates a reviewable JSON template for a domestic stock limit order.
It performs **no network requests**, does not issue tokens or submit even a
virtual-trading order, and does not change cash, holdings, strategy cursors or
order-intent journals. No KIS credentials or account values are needed.

The template always identifies `mode: "paper"`, `provider: "kis_vts"`,
`submitted: false` and `live_orders_enabled: false`. These fields describe this
offline preparation step; they are not broker confirmation.

## Create a plan

Run from the repository root with Python 3.10 or newer:

```bash
python3 tools/kis_vts_order_plan.py \
  --mode paper \
  --client-order-id local-stock-001 \
  --symbol 005930 \
  --side buy \
  --quantity 2 \
  --limit-price 70100 \
  --max-notional 140200 \
  --output kis-vts-plan.json
```

This is a synthetic preparation example, not a market price recommendation.
Its gross order value is exactly `2 × 70100 = 140200` KRW. Use `--side sell`
for a general sell template. The tool does not check whether an account owns
the shares or has sufficient buying power.

The output directory must already exist. The tool creates the file exclusively
with creation mode `0600` (owner access only, possibly stricter under the
process umask). An existing file, directory, symlink or dangling symlink is
refused. Existing output is never truncated or replaced. Invalid arguments
create no output or directories. A write failure attempts to remove only the
new partial file, checking its identity before cleanup so an observed
replacement is retained. Use an output directory you control.

Exit code `0` means the offline plan was written. Exit code `2` means preparation
or saving was refused. Errors are intentionally generic and do not print raw
arguments, paths, environment values or exception text. There is no token cache
and no credential environment variable is read. The standalone CLI loads the
pure module directly, avoiding the unrelated exchange and notification clients
in the `trading_core` package initializer; it runs without third-party packages.

## Supported inputs

These are **application validation bounds**, not assertions about broker order
limits, valid stock listings or orders an account can execute.

| Input | Accepted value |
| --- | --- |
| `client_order_id` | 1–64 ASCII characters; first a letter or digit, remaining letters, digits, `.`, `_` or `-` |
| `symbol` | Exactly six ASCII digits, preserving leading zeros |
| `side` | Exact lowercase `buy` or `sell` |
| `quantity` | Python integer from `1` through `9999999999`; booleans and fractional values are rejected |
| `limit_price` | String containing 1–19 ASCII digits, first digit `1`–`9` |
| `max_notional` | Explicit string containing 1–29 ASCII digits, first digit `1`–`9` |
| `market` | `KRX` only |
| `currency` | `KRW` only |

The CLI also requires canonical positive digit text for `--quantity`. Price and
budget strings cannot contain signs, whitespace, leading zeros, decimal points,
exponents, Unicode digits, `NaN` or infinity. The core API does not coerce floats
or numeric price arguments into strings. Bounded lengths are checked before
converting decimal text to integers.

The maximum notional is a per-order **gross** cap: exact integer arithmetic
checks `quantity × limit_price <= max_notional`. Fees, taxes, reserves, existing
orders and aggregate exposure are outside this cap. These checks do not validate
price ticks, market hours, instrument eligibility, account status, price limits,
buying power or sellable holdings. Six-digit stock scope also excludes seven-
character ETN identifiers supported elsewhere in the broker API.

## Request template contract

The official KIS example defines the cash-order path, VTS buy/sell transaction
IDs, uppercase body fields and string quantities/prices. See the
[official cash-order implementation](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_cash/order_cash.py).
The prepared request is fixed to:

| Part | Prepared value |
| --- | --- |
| Base URL | `https://openapivts.koreainvestment.com:29443` |
| Method and path | `POST /uapi/domestic-stock/v1/trading/order-cash` |
| Buy `tr_id` | `VTTC0012U` |
| Sell `tr_id` | `VTTC0011U` |
| `custtype` | `P` |
| `ORD_DVSN` | `00`, limit order |
| `EXCG_ID_DVSN_CD` | `KRX` |
| `SLL_TYPE` | `01` for general sell; empty for buy |
| `CNDT_PRIC` | Empty |
| `PDNO`, `ORD_QTY`, `ORD_UNPR` | Validated symbol, share count string and limit price string |

Credential and account fields remain literal placeholders:

| Field | Literal template value |
| --- | --- |
| `authorization` header | `Bearer ${KIS_VTS_ACCESS_TOKEN}` |
| `appkey` header | `${KIS_VTS_APPKEY}` |
| `appsecret` header | `${KIS_VTS_APPSECRET}` |
| `CANO` body field | `${KIS_VTS_CANO}` |
| `ACNT_PRDT_CD` body field | `${KIS_VTS_ACNT_PRDT_CD}` |

These placeholders are documentation in a JSON object, not an environment-
expansion mechanism. The planner has no HTTP transport or option to bind them.
Its request template is unsigned and has not been verified with a broker account.

`client_order_id` appears only in local plan metadata with
`client_order_id_scope: "local_correlation_only"`. It is absent from the request
headers and body. It does not provide broker idempotency, deduplication, a
submission reservation or protection from a future duplicate send. Reusing a
client ID in another plan is not detected by this stateless tool.

## Classify a supplied response without treating it as fill evidence

The separate pure API is:

```python
from trading_core.kis_vts_order import classify_order_response

classification = classify_order_response(
    {
        "rt_cd": "0",
        "output": {
            "KRX_FWDG_ORD_ORGNO": "06010",
            "ODNO": "0001569157",
            "ORD_TMD": "155211",
        },
    },
    http_status=200,
)
```

This is a synthetic caller-supplied response. Classification never fetches or
authenticates broker evidence, and the CLI has no response-processing mode.

| Supplied status and data | `status` result |
| --- | --- |
| Integer HTTP `200`, exact string `rt_cd: "0"`, valid canonical acknowledgment | `accepted_acknowledgment` |
| Integer HTTP `200`, exact string `rt_cd: "1"` | `business_rejected` |
| Any other HTTP status, noninteger status, unsupported code, malformed payload or invalid acknowledgment | `unknown` |

`business_rejected` means the supplied response reports a business failure; it
is not independent proof that no order or fill exists. The parser deliberately
does not generalize other nonzero codes into this state. The current official
authentication helper confirms success only for the exact string `"0"`; this
module's narrower `"1"` label is a conservative application convention.

Every classification includes:

```json
{
  "fill_state": "unknown",
  "verified_fill": false,
  "safe_to_retry": false,
  "response_source": "caller_supplied_unverified_response"
}
```

An accepted acknowledgment does not establish that an order remains unfilled:
execution could already have occurred. Neither acceptance nor a reported
failure authorizes an automatic retry or a ledger update. This parser has no
authenticated account binding, order lookup, request correlation, order date,
partial-fill quantities, executed prices or fee information.

The [official checker](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_cash/chk_order_cash.py)
uses uppercase `KRX_FWDG_ORD_ORGNO`, `ODNO` and `ORD_TMD` keys. The
[public endpoint schema](https://apiportal.koreainvestment.com/api/apis/public/detail?accessUrl=%2Fuapi%2Fdomestic-stock%2Fv1%2Ftrading%2Forder-cash)
lists string capacities of 5, 10 and 6 respectively. The classifier accepts
1–5 ASCII digits for the forwarding organization and 1–10 for the order number,
preserving leading zeros rather than assuming exact padding. A zero-only order
number is rejected. Order time must contain exactly six ASCII digits with valid
`HHMMSS` clock ranges; that is a local syntactic gate, not proof of freshness or
trading-session validity.

Missing keys, lowercase-only acknowledgment keys, conflicting lowercase aliases
and nonobject output become `unknown`. The only optional rejection message field
retained is `msg_cd`, limited to 1–8 uppercase ASCII letters/digits. Free-text
`msg1`, raw error bodies, arbitrary account fields and claimed fill fields are
never copied into the result. The caller must supply already-decoded JSON and
reject duplicate keys at decoding time; duplicates cannot be recovered after a
normal JSON decoder has overwritten them in a dictionary.

## Verification and remaining integration work

Run the focused offline checks:

```bash
python3 -m unittest discover -s packages/core/tests -p test_kis_vts_order.py
python3 -m unittest discover -s apps/server/tests -p test_kis_vts_order_plan.py
```

The tests cover exact request contracts, oversized and noncanonical inputs,
integer arithmetic beyond floating-point precision, ambiguous responses, secret
suppression, output permissions/collisions, symlinks and partial-write cleanup.
They also launch the actual CLI in a separate Python process with sockets and
DNS disabled, site packages unavailable, and synthetic credential/proxy values
in the environment. No real credentials or broker orders are used.

A future sender needs its own account-bound authentication, durable stock order
and fill records, authenticated status/fill lookup, partial-fill accounting and
ambiguous-outcome reconciliation before transmission or retry can be considered.
The existing crypto intent journal is not repurposed by this implementation.
Toss combined KRX/NXT candles do not establish a KRX executable price, so this
planner also does not turn a market-data snapshot into an execution guarantee.

Contract sources were reviewed on 2026-11-21. Additional primary source:
[official KIS authentication helper](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/kis_auth.py).
