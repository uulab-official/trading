# Trading Bot Monorepo

24/7 암호화폐 자동매매 시스템 - Monorepo 구조

## 📁 구조

```
trading-monorepo/
├── packages/                    # 공유 패키지 (코어 모듈)
│   ├── core/                   # 거래소, 주문, 리스크, 포트폴리오
│   ├── data/                   # WebSocket, 캔들, 인디케이터
│   ├── strategy/               # 전략 인터페이스 및 베이스
│   └── monitor/                # 텔레그램, 로깅, 알림
│
├── apps/                        # 실행 가능한 애플리케이션
│   └── trading-bot/            # 실제 매매 봇
│
├── infra/                       # 인프라 설정
│   ├── docker/                 # Docker Compose, Dockerfile
│   └── scripts/                # 배포 스크립트
│
└── docs/                        # 문서

```

## 🏗 아키텍처 원칙

1. **단방향 의존성**: apps → packages (packages는 서로 독립)
2. **인터페이스 기반**: core는 추상화, 구현체는 주입
3. **상태 외부화**: SQLite/Redis로 상태 관리 (재시작 복구)
4. **전략 플러그인**: strategy 패키지만 수정해서 전략 교체

## 🚀 빠른 시작

### 1. 설정

```bash
# 저장소 클론
git clone <repo-url>
cd trading-monorepo

# 환경 설정
cp apps/trading-bot/.env.example apps/trading-bot/.env
# .env 파일에 API 키 설정 (Binance Testnet 권장)
```

### 2. NAS/Linux 서버 설치

```bash
# 자동 설치
./infra/scripts/setup.sh

# 수동 설치
docker-compose -f infra/docker/docker-compose.yml build
docker-compose -f infra/docker/docker-compose.yml up -d
```

### 3. 로그 확인

```bash
# 실시간 로그
docker logs -f crypto-trading-bot

# 상태 확인
docker ps
docker-compose -f infra/docker/docker-compose.yml ps
```

## 📦 패키지 설계

### Core Package (`packages/core`)

| 모듈 | 설명 |
|------|------|
| `exchange.py` | CCXT 기반 거래소 추상화 |
| `order_manager.py` | 주문 관리 (멱등성 보장) |
| `risk_manager.py` | 리스크 관리 (일일 손실 제한 등) |
| `portfolio.py` | 포지션 및 잔고 관리 |
| `state_store.py` | SQLite 상태 영속성 |

### Data Package (`packages/data`)

| 모듈 | 설명 |
|------|------|
| `candle.py` | OHLCV 캔들 데이터 관리 |
| `websocket_client.py` | 실시간 WebSocket 연결 |
| `indicator.py` | 기술적 인디케이터 (RSI, MACD, BB 등) |

### Strategy Package (`packages/strategy`)

| 전략 | 설명 |
|------|------|
| `RSIStrategy` | RSI 과매수/과매도 전략 |
| `MACrossStrategy` | 이동평균선 교차 전략 |
| `BreakoutStrategy` | 저항/지지선 돌파 전략 |

## ⚙️ 설정 가이드

### 환경 변수 (`.env`)

```bash
EXCHANGE_API_KEY=your_binance_api_key
EXCHANGE_API_SECRET=your_binance_api_secret
```

### 봇 설정 (`config/bot.yml`)

```yaml
# 리스크 관리
risk:
  max_daily_loss_pct: 3.0      # 일일 최대 손실 3%
  max_positions: 2             # 최대 동시 포지션 2개
  risk_per_trade_pct: 1.0      # 거래당 리스크 1%
  max_leverage: 1              # 현물 거래 (레버리지 1)

# 전략 설정
strategies:
  - name: "BTC_MA_Cross"
    type: "ma_cross"
    symbol: "BTC/USDT"
    timeframe: "4h"
    enabled: true
    fast_period: 50
    slow_period: 200
```

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

# 로컬 실행
python apps/trading-bot/trading_bot/main.py
```

## 📊 모니터링

- **로그 파일**: `logs/trading-bot.log`
- **상태 DB**: `data/bot_state.db` (SQLite)
- **Docker 상태**: `docker stats crypto-trading-bot`

## ⚠️ 리스크 관리 기능

1. **일일 손실 제한**: 설정값 도달 시 거래 중단
2. **서킷브레이커**: 비상 손실 시 봇 자동 정지
3. **쿨다운**: 손실 후 일정 시간 거래 대기
4. **최대 포지션 제한**: 동시 진입 포지션 수 제한
5. **멱등성**: 중복 주문 방지

## 🔒 보안 체크리스트

- [ ] `.env` 파일에 API 키 저장
- [ ] `.env`가 `.gitignore`에 포함됨
- [ ] Binance Testnet으로 먼저 테스트
- [ ] IP 화이트리스트 설정
- [ ] API 키 권한 제한 (거래만, 출금 불가)

## 🚨 주의사항

1. **시작 전 반드시 Testnet에서 테스트**
2. **소액으로 시작하여 검증**
3. **로그를 주기적으로 확인**
4. **전략은 과거 데이터로 백테스트 권장**

## 📚 문서

- [아키텍처 문서](docs/architecture.md)
- [전략 가이드](docs/strategies.md)
- [API 문서](docs/api.md)

## 🔧 지원

이슈 발생 시:
1. 로그 확인: `docker logs crypto-trading-bot`
2. 상태 확인: `docker-compose -f infra/docker/docker-compose.yml ps`
3. 재시작: `docker-compose -f infra/docker/docker-compose.yml restart`
