# KIS VTS read-only stock quote probe (broker sandbox preflight)

This is a conservative integration step toward Korea Investment &
Securities' official VTS (virtual trading) environment. It only uses:

- **Host:** `https://openapivts.koreainvestment.com:29443`
- **Method:** `GET`
- **Path:** `/uapi/domestic-stock/v1/quotations/inquire-price`
- **TR ID:** `FHKST01010100`

The live trading host `openapi.koreainvestment.com:9443`, account
balances, order creation, cancellation, WebSocket trading, and token
issuance are **not implemented** in this tool.

The KIS official API documentation and official sample application
provide the VTS host, current-price route, KRX code J and
six-digit domestic stock identifiers.

## One-shot virtual-host current price

An authorized user must obtain their VTS account and token through
the **official KIS developer process**. Locally set the following
environment variables in a private session. Never paste their actual
values into ChatGPT or commit them to Git:

```sh
export KIS_VTS_ACCESS_TOKEN="local-vts-bearer-token"
export KIS_VTS_APPKEY="local-vts-appkey"
export KIS_VTS_APPSECRET="local-vts-appsecret"

python tools/kis_vts_readonly_quote.py \
  --mode paper --allow-vts-readonly \
  --symbol 005930 \
  --output data/kis-005930-vts-quote-001.json
```

An existing output is never replaced. The request performs exactly one
GET to the VTS current-price route, with no automatic retry, pagination
or token issuance. Redirects are rejected and a bounded successful
JSON response must identify `rt_cd=0` and a finite positive
`output.stck_prpr` price.

The saved artifact contains **only** a current price and observation
time, with `validated_completed_bars: false`. A current quote does
not establish that a full minute bar has completed and must not be fed
directly into the completed-bar MA strategy or considered a broker fill.
No credential, account number, token, request headers, or raw upstream
response is copied to the saved output.

## Next gates before virtual order tests

1. Verify official VTS access, scopes, account configuration and rate
   limits on the actual operator-owned account.
2. Build authenticated read-only account/filled-order reconciliation,
   with exact brokerage evidence; keep trade submission disabled.
3. Implement partial-fill event accounting, market filters, limits,
   adverse execution/timeout handling, and idempotency behavior.
4. Restrict any eventually enabled order adapter to the **VTS host**
   with independently reviewed operator activation.
5. Test every negative case: lost ACK, stale quote, rate limit, broker
   outage, partial fill, cancel after fill, and unexpected restarts.
6. Keep real-market order paths absent until full security and
   operational signoff.

### References

- KIS official API portal: https://apiportal.koreainvestment.com/apiservice
- Official sample quote code:
  https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_price/inquire_price.py

**This is a read-only development probe, not an authenticated
end-to-end demo or evidence that a VTS order has been placed.**
