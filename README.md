# Trading Bot Monorepo

코인·국내 주식·미국 주식 모의매매 개발 모노레포

## 바로 실행하는 모의 테스트

```sh
# API 키 없이 합성 55개 분봉으로 실행 → 재시작 → 감사 → 백업 → HTML 검증
python tools/stock_paper_smoke.py

# 주문 결과 불명 → 부분 체결 → 취소 경합 → 재시작·중복 통지 검증
python tools/stock_order_lifecycle_smoke.py

# 네트워크와 DNS를 차단한 전체 오프라인 검증 (백테스트 의존성 필요)
python tools/offline_checks.py --include-backtest
```

주식 전략은 KRW와 USD 원장을 각각 저장하며, 같은 데이터를 다시 실행해도
가상 체결이 중복되지 않습니다. 토스는 기존 토큰을 재사용하는 읽기 전용
시세 수집, KIS는 모의투자 서버의 읽기 전용 현재가·일별 주문 내역 조회와
오프라인 지정가 주문 준비 기능을 제공합니다. 별도 주식 주문 이벤트 저널은
부분 체결·취소·결과 불명 상태를 SQLite에 저장하고 재시작 때 재검증합니다.
실제 인증·시세 대조와 KIS 모의 주문 체결은 아직 검증되지 않았습니다.

- [주식 주문 생명주기·KIS 주문 조회 검증 결과](docs/STOCK_ORDER_LIFECYCLE_VERIFICATION_2026_11_21.md)
- [주식 주문 이벤트 저널·합성 시나리오 실행](docs/STOCK_ORDER_LIFECYCLE.md)
- [KIS 모의 계좌의 읽기 전용 일별 주문 조회](docs/KIS_VTS_ORDER_SNAPSHOT.md)
- [토큰 재사용·KIS 주문 준비의 최신 검증 결과](docs/BROKER_PREPARATION_VERIFICATION_2026_11_21.md)
- [모의 원장·재시작·백업 검증 결과](docs/VERIFICATION_2026_11_21.md)
- [주식 모의매매 실행·감사·HTML 가이드](docs/STOCK_PAPER_FORWARD_RUNBOOK.md)
- [토스 시세 계약과 15:30 집계봉 제외 정책](docs/STOCK_PAPER_TOSS_KIS.md)
- [KIS 모의 지정가 주문 준비·응답 분류](docs/KIS_VTS_ORDER_PLAN.md)

### API 키 없이 KIS 모의주문 준비

```sh
# 예시 가격으로 요청 양식만 생성합니다. 주문을 전송하지 않습니다.
mkdir -p data
python tools/kis_vts_order_plan.py --mode paper \
  --client-order-id demo-005930-buy-001 --symbol 005930 --side buy \
  --quantity 1 --limit-price 70000 --max-notional 100000 \
  --output data/kis-vts-buy-plan.json
```

계좌번호와 인증 정보는 환경변수 이름으로 표시됩니다. 생성된 파일은 검토용
요청 양식이며, 같은 client ID를 지정해도 증권사 중복 방지가 보장되는 것은
아닙니다. 주문 접수·거절·결과 불명 분류기는 체결이나 잔고를 변경하지 않습니다.

토스 반복 수집은 로컬 실행 환경의 `TOSS_ACCESS_TOKEN`을 재사용합니다.
기존 client ID/secret만 넣던 실행은 이제 새 토큰 발급에 `--issue-token`을
명시해야 합니다. 토큰 갱신이 다른 프로세스의 인증에 영향을 주므로 오류 후
자동 재발급은 하지 않습니다.

### API 키 없이 부분 체결·취소·재시작 테스트

```sh
# 결과만 출력하고 임시 DB는 정리합니다.
python -B -S tools/stock_order_lifecycle_smoke.py

# 새 디렉터리에 합성 DB와 상세 JSON 보고서를 남깁니다.
mkdir -p data
python -B -S tools/stock_order_lifecycle_smoke.py --output-dir data/order-lifecycle-demo
python -B -S tools/stock_order_journal.py --mode paper status \
  --database data/order-lifecycle-demo/journal.sqlite --account-ref demo-stock-orders
```

고정 합성 시나리오는 이벤트 14개, 중복을 제외한 체결 4건을 만듭니다.
매수 10주 중 5주 체결 후 잔여 5주를 취소하고, 이후 5주 매도가 취소 대기 중
모두 체결되는 경합을 검증합니다. 최종 현금 변화는 비용 차감 후 5,285원,
주식 수량 변화는 0주입니다. 이 수치는 합성 입력의 계산 결과이며 실제 잔고나
투자 성과가 아닙니다. [상세 실행 결과](docs/examples/stock_order_lifecycle_report.json)를
확인할 수 있습니다.

KIS 일별 주문 조회는 기존 VTS 인증 정보로 별도 실행합니다. 모든 페이지를
검증한 관측값만 저장하며, 누적 주문 내역을 개별 체결로 바꾸거나 위 저널에
자동 반영하지 않습니다. [조회 가이드](docs/KIS_VTS_ORDER_SNAPSHOT.md)에
환경변수·명령·제한 사항을 정리했습니다.

## Verified offline paper workflow

The deterministic offline paper runner is the tested entrypoint for this revision.
Start with the [personal offline trial guide](PERSONAL_TRADING_GUIDE.md) for exact
commands, expected output, and duplicate-free resume. It uses supplied synthetic
candles, not an ongoing market-connected paper service. The existing Rust and
Python dashboards do not consume this runner’s JSON checkpoint.
See [OFFLINE_PAPER_RUNNER.md](docs/OFFLINE_PAPER_RUNNER.md) for local fixture replay,
simulated fees/slippage, marked holdings, risk limits, and validated checkpoint
resume. Run `python tools/offline_checks.py` for the targeted offline checks.

The existing backtest engine now has separate
[offline accounting regressions](docs/OFFLINE_BACKTEST_ACCOUNTING.md). Run
`python tools/offline_checks.py --include-backtest` with pandas/numpy available
to check causal fills, spot balances, fees, marked equity, and repeatable runs.

Audit an existing checkpoint without changing its paper state using
`python tools/paper_checkpoint_audit.py config/examples/offline_fixture.json data/owner-trial/checkpoint.json`
(see [checkpoint audit and production blockers](docs/OFFLINE_CHECKPOINT_AUDIT.md)).

The stock strategy now verifies independently calculated paper equity
**before committing** fills and its bar cursor, and offers a separate
read-only maximum-drawdown audit via
`python tools/stock_paper_risk_audit.py`. This is retrospective paper
risk analysis, not real-time broker-side loss protection. See
[stock paper runbook](docs/STOCK_PAPER_FORWARD_RUNBOOK.md).

A **zero-credential synthetic end-to-end stock demo** can be exercised
using `python tools/stock_paper_smoke.py`. It verifies 55 explicitly
synthetic 1m candles, strategy replay, duplicate refusal, read-only
audit, SQLite WAL backup/deep verification and static report generation.
No real quotes or orders are involved.

A KIS **virtual-trading host read-only quote probe** is available as
`tools/kis_vts_readonly_quote.py`; it requires explicit opt-in, uses only
one VTS GET current-price route, and does not create orders or produce
completed-bar strategy signals. See
[KIS VTS read-only operator guide](docs/KIS_VTS_READONLY_QUOTE.md).

A private static **crypto + stock PAPER dashboard** can now be
rendered from an independently audited JSON snapshot, without any
web server, JavaScript, account credentials, exchange order calls or
currency aggregation. See
[local paper portfolio dashboard](docs/PAPER_PORTFOLIO_DASHBOARD.md).

The stock PAPER SQLite strategy can now be **backed up consistently**
and independently replay-verified; the separate crypto and stock
portfolios can be inspected through one **read-only, nonaggregating**
CLI. See [stock backups and unified paper monitor](docs/STOCK_PAPER_BACKUP_MONITOR.md).
Neither function enables real broker orders or continuous hosting.

An **append-only stock paper strategy replay** now links
Toss-normalized completed candles to a transactionally persisted
SQLite strategy cursor and simulated fills; `--audit-only` verifies
the ledger without updates, and a static Korean HTML snapshot can be
generated from verified state. See
[stock forward paper operation runbook](docs/STOCK_PAPER_FORWARD_RUNBOOK.md).
This does **not** run a background trading process or submit broker orders.

For local Toss Korean/US equity paper research, the repository now has
**three deliberately separated paths**: saved JSON/opt-in OAuth **read-only
1m candles**, deterministic next-open **stock MA paper backtesting**, and a
durable SQLite **manual stock cash ledger**. No path submits actual orders.
See [stock paper + Toss/KIS runbook](docs/STOCK_PAPER_TOSS_KIS.md).

```sh
# First obtain an operator-approved Toss read-only 1m paper fixture.
python tools/stock_paper_backtest.py data/toss-005930-paper-bars.json \
  --initial-cash 1000000 --fast 5 --slow 20

# Separately, try a manual virtual fill in a persistent local paper book.
python tools/stock_paper_cli.py --mode paper \
  --database data/stock-krw-paper.sqlite \
  --currency KRW --initial-cash 1000000 --fee-pct 0.1 \
  trade --client-id example-buy-001 --symbol 005930 \
  --side buy --shares 1 --price 80000
```

For **stock trading experiments**, a separate credential-free
`StockPaperBook` now simulates whole-share KRW/USD transactions,
fees, marked equity, and duplicate-order refusal. The Toss Securities
Open API supports Korean and US stocks, but this stock emulator does
**not** call Toss order APIs or use real money. KIS has a separate
official virtual-trading environment for future broker-level testing.
See [stock paper and Toss/KIS integration plan](docs/STOCK_PAPER_TOSS_KIS.md).

A **deterministic offline broker emulator** now exercises synthetic partial fills,
lost acknowledgments, cancellation and duplicate-submission refusal.
It does not connect to any exchange or approve real orders.
See [sandbox order fault-injection limits](docs/SANDBOX_ORDER_LIFECYCLE.md).

A **durable SQLite order-intent journal** now exists as an offline-only
foundation for future broker sandbox integration. It records ambiguous
client order IDs and prevents duplicate or conflicting order intentions;
there is **no authenticated broker or live order implementation**.
Run `python tools/order_intent_preflight.py data/sandbox-intents.sqlite`
against an **existing** local journal to check SQLite integrity and
unresolved order intentions without creating or modifying the journal.
An exit code of `3` blocks further intents; even exit `0` does not
approve live trading. See [order intent journal safety design](docs/ORDER_INTENT_JOURNAL.md).

Create an immutable **offline paper portfolio backup** only after economic
replay validation with
`python tools/paper_backup.py create --archive data/upbit-btc-1h-archive.json --checkpoint data/upbit-btc-1h-paper.json --destination data/backups/paper-001`.
Verify file hashes with `python tools/paper_backup.py verify data/backups/paper-001` or independently replay backed-up paper economics with `python tools/paper_backup.py verify data/backups/paper-001 --deep`.
See [paper backup and manual recovery runbook](docs/PAPER_BACKUP_RUNBOOK.md).
This does not restore state or enable live orders.

Run an **opt-in paper-only supervisor** with an atomic health file and a
fail-closed consecutive-failure halt:
`python tools/paper_supervisor.py --mode paper --loop --market KRW-BTC --unit 60 --archive data/upbit-btc-1h-archive.json --checkpoint data/upbit-btc-1h-paper.json --heartbeat data/upbit-btc-1h-health.json`.
Probe freshness with
`python tools/paper_healthcheck.py data/upbit-btc-1h-health.json --max-age-seconds 7200`.
See [paper supervisor runbook](docs/PAPER_SUPERVISOR_RUNBOOK.md).
A local emergency STOP marker at `data/upbit-btc-1h-paper.json.STOP` prevents any future cycles; see the runbook for safe recovery.
This supervises **simulated trades only**. The live broker is not enabled.

Run one **public quotation → archived paper replay** cycle with
`python tools/upbit_paper_cycle.py --market KRW-BTC --unit 60 --archive data/upbit-btc-1h-archive.json --checkpoint data/upbit-btc-1h-paper.json --initial-balance 1000000`
(see [integrated paper cycle guide](docs/UPBIT_PAPER_CYCLE.md)). This still does not place real trades.

For interrupted public candle history beyond one 200-bar window, generate a
**separate, bounded, read-only recovery candidate** with
`python tools/upbit_paper_backfill.py --archive data/upbit-btc-1h-archive.json --candidate data/upbit-btc-1h-recovery-candidate.json --market KRW-BTC --unit 60 --pages 3`.
See [manual gap-recovery runbook](docs/UPBIT_PAPER_BACKFILL.md). This
never auto-promotes history or executes real orders.

Maintain an **append-only, overlap-verified public candle archive** using
`python tools/upbit_paper_archive.py --market KRW-BTC --unit 60 --archive data/upbit-btc-1h-archive.json`
(see [cumulative public paper history](docs/UPBIT_PAPER_ARCHIVE.md)).

Capture **public Upbit KRW candle snapshots** without accounts or order APIs via
`python tools/upbit_public_paper_snapshot.py --market KRW-BTC --unit 60 --count 200 --output data/upbit-btc-1h.json`
(see [paper-only public snapshot guide](docs/UPBIT_PUBLIC_PAPER.md)). This is a one-shot API read, not a continuous trading service.

Import caller-supplied historical candles with strict validation using
`python tools/import_ohlcv_csv.py history.csv --symbol BTC/KRW --timeframe 1h --output data/local-history.json`
(see [offline CSV import contract](docs/OFFLINE_CSV_IMPORT.md)). Source authenticity and bar completeness remain operator responsibilities.

Generate a **static, local-only paper dashboard** with
`python tools/paper_report_html.py config/examples/offline_fixture.json data/owner-trial/checkpoint.json --output data/owner-trial/report.html`
(see [offline dashboard guide](docs/OFFLINE_PAPER_REPORT.md)). The output is an audited
snapshot, not live monitoring or an activated trading service.

Run `python tools/offline_trade_report.py` for a network-forbidden report of the
local synthetic fixture using [fee-inclusive FIFO trade reporting](docs/OFFLINE_REALIZED_REPORTING.md).
It also includes [elapsed-time CAGR](docs/OFFLINE_ANNUALIZATION.md) with period,
units, undefined-value handling, and an explicit extrapolation warning.
Checkpointed paper replay now protects saved progress with one-writer ownership
and tested interruption/retry behavior on local POSIX filesystems.
The installed `trading-paper` command and module entrypoint share successful JSON
output and documented failure/recovery exit codes; Python callers keep the result
dictionary API.

The older quick-start, live/Rust deployment, and legacy backtest instructions below
are not evidence of production readiness. Live market data is explicitly
unavailable in the restored offline data package; no real trading was validated.

[![CI/CD](https://github.com/uulab-official/trading/actions/workflows/ci.yml/badge.svg)](https://github.com/uulab-official/trading/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://github.com/uulab-official/trading/pkgs/container/trading)

## 🎯 주요 특징

- **⚡ Zero-Downtime Deployment**: 무중단 배포 지원
- **🔒 Security First**: 설정과 인증정보 분리, 환경변수 지원
- **📱 Telegram Integration**: 실시간 알림 및 원격 제어
- **📊 Advanced Monitoring**: Prometheus + Grafana 지원
- **🧪 Backtesting**: 전략 검증 및 최적화
- **🐳 Docker Ready**: 컨테이너 기반 배포

---

## 📁 프로젝트 구조

```
trading/
├── apps/
│   └── server/                # Python paper CLI/레거시 봇 및 Rust 서버
├── packages/
│   ├── core/                  # 거래소, 주문, 리스크, 포트폴리오
│   ├── data/                  # WebSocket, 캔들, 인디케이터
│   ├── strategy/              # 전략 인터페이스 및 구현
│   ├── backtest/              # 백테스팅 엔진
│   └── monitor/               # 모니터링 유틸리티
├── config/
│   ├── templates/             # 설정 템플릿
│   ├── examples/              # 예시 설정
│   ├── active/                # 활성 설정 (Git 제외)
│   └── secrets/               # 인증 정보 (Git 제외)
├── infra/
│   ├── docker/                # Docker 설정
│   └── scripts/               # 배포 스크립트
└── docs/                      # 문서
```

---

## 🚀 개인 체험 시작

[개인용 오프라인 모의거래 가이드](PERSONAL_TRADING_GUIDE.md)를 따라
저장소 루트에서 Python CLI를 실행하세요. 합성 캔들 90개를 재생하고
모의 체결 2건과 체크포인트 재실행을 확인합니다. 거래소 계정·API 키·Docker는 필요 없습니다.

`infra/docker/docker-compose.yml`은 별도의 Rust 서버를 띄웁니다.
이 명령으로 Python 체험 결과가 대시보드에 연결되지 않습니다.
[현재 대시보드 경계](docs/WEB_DASHBOARD.md)를 먼저 확인하세요.

이하 설정·배포·실거래 관련 내용은 기존 구성 참고 자료이며,
이 체험의 다음 단계나 운영 준비 완료를 뜻하지 않습니다.

---

## 📖 문서

| 문서 | 설명 |
|------|------|
| [PERSONAL_TRADING_GUIDE.md](PERSONAL_TRADING_GUIDE.md) | 현재 검증된 개인용 오프라인 체험 |
| [CUSTOMER_SETUP.md](CUSTOMER_SETUP.md) | 기존 서비스 설치 참고 (현재 체험 경로 아님) |
| [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) | 실전 전환 체크리스트 |
| [ZERO_DOWNTIME_DEPLOYMENT.md](docs/ZERO_DOWNTIME_DEPLOYMENT.md) | 무중단 배포 가이드 |
| [TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md) | 텔레그램 설정 |
| [MONITORING_SETUP.md](docs/MONITORING_SETUP.md) | 모니터링 설정 |
| [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) | 프로젝트 요약 |

---

## ⚙️ 설정

### 설정 파일 구조

```yaml
# config/active/bot_config.yml
bot:
  id: "my-trading-bot"
  name: "My Trading Bot"
  mode: paper              # paper 또는 live
  timezone: "Asia/Seoul"

exchange:
  id: binance
  sandbox: true            # true: 테스트넷
  testnet: true

telegram:
  enabled: true
  notifications:
    trade: true
    error: true
    daily_report: true
    position: true

risk:
  max_daily_loss_pct: 2.0
  max_positions: 2
  risk_per_trade_pct: 0.5
  max_leverage: 1.0

strategies:
  - name: "BTC_RSI"
    enabled: true
    type: rsi
    symbol: "BTC/USDT"
    timeframe: "1h"
    params:
      period: 14
      oversold: 30
      overbought: 70
```

```yaml
# config/secrets/secrets.yml (절대 Git에 커밋하지 마세요!)
binance:
  api_key: "YOUR_API_KEY"
  api_secret: "YOUR_API_SECRET"

telegram:
  bot_token: "YOUR_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"
```

### Config CLI 명령어

```bash
# 설정 초기화
python -m trading_core.config_cli init

# 설정 검증
python -m trading_core.config_cli validate

# 텔레그램 설정
python -m trading_core.config_cli telegram-setup

# 텔레그램 테스트
python -m trading_core.config_cli test-telegram

# 현재 설정 보기
python -m trading_core.config_cli show
```

---

## 📊 사용 가능한 전략

| 전략 | 설명 | 파라미터 |
|------|------|----------|
| **RSI** | 과매수/과매도 | `period`, `oversold`, `overbought` |
| **MA Cross** | 이동평균선 교차 | `fast_period`, `slow_period` |
| **Breakout** | 저항/지지선 돌파 | `lookback_period`, `breakout_threshold` |

---

## 🔄 무중단 배포

### 배포

```bash
# 새 버전 배포 (Zero-downtime)
./infra/scripts/deploy.sh v1.1.0

# 또는 Docker Hub에서
export VERSION=v1.1.0
docker-compose -f infra/docker/docker-compose.yml up -d
```

### 롤백

```bash
# 롤백 실행
./infra/scripts/rollback.sh

# 또는 특정 백업으로 롤백
./infra/scripts/rollback.sh backup_20260115_120000.db
```

---

## 📱 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `/status` | 봇 상태 확인 |
| `/portfolio` | 포트폴리오 조회 |
| `/positions` | 포지션 목록 |
| `/stop` | 봇 중지 (관리자 전용) |

---

## 🧪 백테스팅

```bash
# 백테스트 실행
python -m trading_backtest run \
  --symbol BTC/USDT \
  --strategy rsi \
  --start-date 2024-01-01 \
  --end-date 2024-12-31

# 파라미터 최적화
python -m trading_backtest optimize \
  --symbol BTC/USDT \
  --strategy rsi \
  --param-grid '{"period": [10, 14, 20]}'
```

---

## 📊 모니터링

### 기본 모니터링

```bash
# 실시간 로그
docker-compose logs -f

# 리소스 사용량
docker stats

# 상태 확인
docker-compose ps
```

### 고급 모니터링 (Prometheus + Grafana)

```bash
# 모니터링 스택 시작
docker-compose -f docker-compose.monitoring.yml up -d

# Grafana 접속: http://localhost:3000 (admin/admin)
# Prometheus 접속: http://localhost:9090
```

---

## 🛡️ 리스크 관리

- **일일 손실 제한**: 설정값 도달 시 자동 거래 중단
- **서킷브레이커**: 비상 손실 시 봇 자동 정지
- **쿨다운**: 손실 후 일정 시간 거래 대기
- **최대 포지션 제한**: 동시 진입 포지션 수 제한
- **멱등성**: 중복 주문 방지

---

## 🔒 보안 체크리스트

- [x] 설정과 인증정보 분리 (`config/active/` vs `config/secrets/`)
- [x] `.gitignore`에 민감 정보 자동 제외
- [x] 환경변수 지원 (`${VAR_NAME}`)
- [ ] API 키에 출금 권한 없음 (거래만 가능)
- [ ] IP 화이트리스트 설정 (권장)

---

## 🐳 Docker

### 이미지 빌드

```bash
docker build -f infra/docker/Dockerfile -t trading-bot:latest .
```

### GitHub Container Registry

```bash
# 최신 버전 Pull
docker pull ghcr.io/uulab-official/trading:latest

# 특정 버전 Pull
docker pull ghcr.io/uulab-official/trading:v1.0.0

# 실행
docker run -d \
  -v $(pwd)/config:/app/config:ro \
  -v $(pwd)/data:/app/data \
  ghcr.io/uulab-official/trading:latest
```

---

## 🧪 로컬 개발

```bash
# 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 패키지 설치
pip install -e packages/core
pip install -e packages/data
pip install -e packages/strategy
pip install -e apps/trading-bot

# 테스트
pytest packages/ -v

# 로컬 실행
python apps/trading-bot/trading_bot/main.py
```

---

## 🚨 주의사항

1. **반드시 Paper Trading에서 먼저 테스트하세요** (최소 2주 권장)
2. **소액으로 시작하세요** (Live 모드 전환 시)
3. **로그를 주기적으로 확인하세요**
4. **백테스팅으로 전략을 검증하세요**
5. **리스크 관리 설정을 반드시 활성화하세요**

---

## 📄 라이선스

MIT License - 자유로운 상업용 사용 가능

자세한 내용은 [LICENSE](LICENSE) 파일을 참조하세요.

**면책 조항**: 이 소프트웨어는 교육 및 연구 목적으로 제공됩니다. 암호화폐 트레이딩에는 상당한 손실 위험이 있으며, 저자는 이 소프트웨어 사용으로 인한 재정적 손실에 대해 책임을 지지 않습니다.

---

## 🤝 기여

기여를 환영합니다! Issue와 Pull Request를 자유롭게 제출해 주세요.

---

## 📞 지원

- **GitHub Issues**: https://github.com/uulab-official/trading/issues
- **Documentation**: `/docs` 디렉토리
- **Release**: https://github.com/uulab-official/trading/releases

---

**Status**: Offline paper replay verified; live/Rust/legacy backtest unverified
**Version**: 1.0.0  
**Last Updated**: 2026-10-03
