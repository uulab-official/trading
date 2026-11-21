# Toss token reuse and KIS VTS order preparation

## Intent and scope

Continue the approved stock paper-trading work with reusable Toss authentication
and a reviewable, offline KIS virtual-trading order contract. Keep Python 3.10+
and the existing standard-library tooling. These are implementation choices made
for this continuation, not new trading instructions from the user.

## Design decisions

- Toss snapshots prefer an operator-supplied `TOSS_ACCESS_TOKEN`. An existing
  token requires no client ID/secret and causes exactly one market-data GET.
- Token issuance is an explicit opt-in (`--issue-token`, function argument
  `issue_token=False`). Credentials alone no longer silently rotate a token.
  Supplied tokens always take precedence. Failure never triggers token reissue
  or a request retry. No new on-disk token cache is introduced.
- Validate request inputs and credential/header strings before network access.
  Reject redirects and environment proxies. Preserve generic CLI errors and
  exclude secrets from snapshot JSON and stdout/stderr.
- KIS order preparation is a pure, offline request-template builder: domestic
  six-digit stock codes, KRX, KRW, integer shares and positive integer limit
  prices, explicit per-order maximum notional. Buy/sell are supported, with
  current VTS transaction IDs. Market/credit/short orders are outside this scope.
- Templates contain environment-variable placeholders for credentials/account
  fields. They never read the environment, connect to a broker, or change cash,
  holdings, strategy cursors or existing intent journals. Local client IDs are
  correlation labels, not broker idempotency keys.
- A separate pure response classifier handles accepted acknowledgment,
  business rejection and unknown outcome. An acknowledgment is never a fill.
  All outcomes forbid automatic retry; caller-provided JSON is not authenticated
  broker evidence and cannot reconcile any ledger.
- A CLI writes new plan files exclusively with mode 0600; an existing target
  must not be overwritten. CLI output/error text must not expose environment
  values or raw response bodies.

## Approaches considered

An explicit existing-token input is sufficient for the current one-shot Toss
collector. A shared disk cache would add secret storage and process-locking
behavior that this collector does not otherwise need. The implementation uses
the existing-token approach, with deliberate one-shot issuance still available.

For KIS, an unsigned template plus response classification establishes a useful
tested contract before a sender is introduced. A networked order sender would
also require durable stock-specific order/fill events, authenticated lookup,
account binding and timeout reconciliation. The crypto-only intent journal is
not reused for stock quantities or partial-fill accounting.

## Evidence and limits

Use current official Toss FAQ/OpenAPI and KIS endpoint examples. Record source
links in operator docs. Toss KRX/NXT combined candles are not interchangeable
with a KIS KRX execution venue. Price ticks, market hours, instrument eligibility,
account buying power and executable fills remain broker-side checks, not claims
made by the offline planner. No credentials or external orders are needed for
this iteration's verification.
