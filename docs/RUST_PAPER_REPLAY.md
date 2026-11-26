# Rust offline paper experiment: strategy → delayed fill → risk → ledger → report

## Run without credentials or network

```sh
cd apps/server
mkdir -p /tmp/paper-example
cargo run --locked -- paper-replay --input config/paper_replay_example.json --output-dir /tmp/paper-example
cargo run --locked -- paper-replay --input config/paper_replay_example.json --output-dir /tmp/paper-example --html > /tmp/paper-report.html
```

The bundled prices, asset DEMO and rules are synthetic. The command is local-only,
never starts master/worker, never opens sockets and does not use broker credentials.
The HTML is a self-contained static report, not the live web dashboard.

## Contract and model

- Versioned bounded JSON scenario: one base/quote market, exact decimal OHLCV,
  static market rules and quote scale, account/risk policy, execution model and up
  to eight independent MA/RSI strategy candidates. At most 240 contiguous,
  fixed-interval UTC candles. All bars and all strategy parameters are validated
  before account files are written. Reject off-grid, missing, duplicate, out-of-order,
  misaligned or malformed OHLCV and prices too large for the existing indicators.
- Warmup bars update indicators but never trigger orders or equity accounting.
  All candidates use identical evaluation bars, initial cash and risk policy.
  One independent ledger per strategy; no shared simulated funds or market impact.
- Only closed candles produce signals. A signal schedules an intent at least one
  bar later. For this single-position long-only model, buy only when flat, sell
  existing inventory, one pending intent per strategy. Later signals do not
  replace a pending intent. Intents beyond the sample expire.
- At the scheduled opening mark simulate adverse tick-rounded slippage. A
  market-style order is represented by a bounded synthetic IOC at that price,
  not by an unconstrained exchange market order. Limit-style IOC derives its
  price from the signal close plus/minus configured offset; only the delayed
  opening price can cross it. No intrabar execution based on high/low ordering.
- Previous *completed* bar volume × participation cap (integer floor) is a
  liquidity proxy; the current bar's volume and close are not used at its open.
  Model partial fills, zero liquidity and immediate residual cancellation.
  Fee = ceil(gross quote atoms × fee bps / 10000); reserve fee ceiling and verify
  static order limits, exact tick/lot and ledger cash/inventory. No assumed fill
  probability, order book depth, maker rebates, exchange taxes or live latency.
- Observe risk at evaluation open, after an attempted execution and at close.
  Existing journaled risk guards pre/post simulated buys. Observed breaches
  latch buy halt. Inventory-reducing sells stay available. All accepted accounting
  and risk events replay through the existing ledger. Quotes and timestamps are
  historical inputs, not authenticated realtime observations.

## Persistence and recovery

The complete canonical scenario (including market rules/units, strategy parameters
and every bar) is SHA-256 bound to the account identity. Each strategy gets
`paper-<strategy-id>.sqlite` in a trusted, already existing dedicated output
directory. Case-insensitive duplicate IDs and unsafe names are refused.
All simulations finish in memory before writing. Each new DB is initialized by a
single verified event transaction. Rerun the identical scenario to verify full
event and snapshot equality or create missing account outputs. A changed scenario,
tampered account or incomplete DB fails rather than resetting it. Use a new output
directory for a different scenario. Different strategy DBs are not cross-file
atomic; if one creation fails, inspect the failed file before retrying. No
automatic process-level ownership or online backup scheduler is introduced.

## Reporting and evaluation limitations

JSON and static HTML report candidate results in original input order, marked
equity, realized and unrealized P&L, fees, fills, return basis points, sample
maximum drawdown, buy halt, remaining inventory, close equity curve and
attempts with signal/open timestamps and outcomes. Open inventory is marked at
the last close (not force liquidated); return includes modeled fees. JSON
integer atom fields may exceed JavaScript's safe integer range; use an
integer-preserving parser. The HTML renders amounts as exact decimal integers.
No annualized return, Sharpe, or claim of statistical edge.
Warmup is not training; independent out-of-sample dataset selection and
walk-forward optimization are separate requirements. Python backtest, portfolio
report and supervisor paths remain unchanged.

## CI and remaining production integration

```sh
cd apps/server
cargo test --locked --test paper_sim_test
cargo test --locked
```

New tests cover next-bar execution, partial/cancel and fees, no current-bar
volume lookahead, limit gaps, slippage, warmup/expiry, risk halt, independent
strategies, invalid data, exact replay recovery and the executable JSON/HTML
path. The existing Docker CI runs Rust tests and an explicit network-disabled
fixture smoke test. This is implementation scope, not a claim of passing CI.

Remaining: verified real historical imports, walk-forward split/selection,
continuous observation with reconnect/backfill, incremental journals beyond
the existing 2,048-event cap, safe live supervisor, atomic external checkpoint
publication and backup retention, live dashboard integration, broker
reconciliation and independent multi-day paper validation. Live orders remain
disabled; never use the simulator's synthetic fills as actual execution evidence.

## Rolling walk-forward evaluation

```sh
cargo run --locked -- paper-walk-forward --input config/paper_replay_example.json --train-bars 4 --test-bars 2 --min-train-fills 1 --max-train-drawdown-bps 10000
```

Predeclared candidates are compared only on training bars; ranking uses exact final marked equity with equal initial funds. Halted candidates and those failing the explicit fill-count or drawdown requirement are excluded. Ties prefer lower drawdown then lexical ID. Each nonoverlapping test window starts with fresh funds and earlier indicator warmup only. No eligible candidate means a skipped fold, not zero return. At most 32 complete folds are evaluated; trailing bars are validated but not scored. Reports include exact input/config digests, training evidence and selected test returns. Means are independent fold averages, not compound or annualized returns. Repeatedly changing candidate definitions after reading test results destroys holdout isolation. Use a final untouched dataset and independent longer-history validation before any deployment.

Regression: `cargo test --locked --test paper_walk_forward_test`. The new network-disabled Docker smoke gate also invokes the CLI. Test execution is pending CI.

## 저장 원장 읽기 전용 운영 감사

```sh
cargo run --locked -- paper-audit --input config/paper_replay_example.json --output-dir /tmp/paper-example
cargo run --locked -- paper-audit --input config/paper_replay_example.json --output-dir /tmp/paper-example --html > /tmp/paper-audit.html
```

원본 시나리오에서 전략·모의 체결·위험 이벤트를 다시 계산하고 각각의 SQLite 원장과
전체 이벤트 및 최종 계좌를 대조한다. 누락 또는 다른 이력은 오류이며 신규 계좌 생성,
수정, 복구, 주문 전송은 하지 않는다. JSON 및 HTML에 검증된 이벤트 수, 남은 원장
용량, 외부 보관용 checkpoint를 표시한다. 여러 계좌는 각각 읽기 스냅샷을 사용하므로
동일 시각의 실시간 포트폴리오 또는 현재 시세 건강 상태를 증명하지 않는다.
`cargo test --locked --test paper_audit_test` 및 네트워크 차단 Docker CLI 검증 대상이다.
