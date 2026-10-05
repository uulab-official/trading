# Trading Bot Monorepo

암호화폐 자동매매 개발 모노레포 · 현재 검증된 개인 체험은 오프라인 합성 데이터 재생

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
