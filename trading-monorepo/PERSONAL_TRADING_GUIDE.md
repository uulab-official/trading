# 나만을 위한 실전 트레이딩 가이드

> ⚠️ **경고**: 이 문서는 개인 사용 목적으로 작성되었습니다. 상업적 배포를 위해서는 추가적인 테스트와 검증이 필요합니다.

---

## 🎯 목표

**2주 Paper Trading → 소액 Live Trading → 자본 확대**

---

## Phase 1: Paper Trading (2주)

### 1.1 준비사항

```bash
# 1. 저장소 클론
git clone https://github.com/uulab-official/trading.git
cd trading/trading-monorepo

# 2. 설정 초기화
python -m trading_core.config_cli init

# 3. Paper Trading 설정 확인
nano config/active/bot_config.yml
```

### 1.2 Paper Trading 설정

```yaml
bot:
  id: "my-test-bot"
  name: "Test Bot"
  mode: paper              # ← 반드시 paper
  timezone: "Asia/Seoul"

exchange:
  id: binance
  sandbox: true            # ← 테스트넷 사용
  testnet: true
  api_key: "YOUR_TESTNET_API_KEY"
  api_secret: "YOUR_TESTNET_SECRET"
  default_type: spot

risk:
  max_daily_loss_pct: 5.0  # 테스트용으로 넉넉하게
  max_daily_trades: 20
  max_position_size_pct: 50.0
  max_positions: 3
  risk_per_trade_pct: 5.0
  max_leverage: 1.0
  default_stop_loss_pct: 3.0
  default_take_profit_pct: 6.0
  circuit_breaker_loss_pct: 10.0

telegram:
  enabled: true            # 알림 받기 위해 활성화
  bot_token: "YOUR_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"
  notifications:
    trade: true
    error: true
    daily_report: true
    position: true

strategies:
  # 테스트용으로 1-2개만 활성화
  - name: "BTC_RSI_Test"
    enabled: true
    type: rsi
    symbol: "BTC/USDT"
    timeframe: "1h"
    params:
      period: 14
      oversold: 30
      overbought: 70
```

### 1.3 테스트넷 API 키 발급

1. [Binance Testnet](https://testnet.binance.vision/) 접속
2. GitHub로 로그인
3. "Generate API Key" 클릭
4. `config/secrets/secrets.yml`에 저장

### 1.4 Paper Trading 실행

```bash
# 자동 테스트 스크립트 실행
./test-trading.sh

# 또는 수동으로
docker-compose -f infra/docker/docker-compose.yml up -d

# 로그 모니터링
docker-compose -f infra/docker/docker-compose.yml logs -f
```

### 1.5 확인사항 (2주간)

**매일 확인:**
- [ ] 봇이 24시간 중단 없이 실행 중
- [ ] 에러 로그 없음
- [ ] 전략이 신호를 생성함
- [ ] 주문이 정상적으로 체결됨 (Paper mode)
- [ ] Telegram 알림 수신

**매주 확인:**
- [ ] 수익률 추이 분석
- [ ] 리스크 관리 작동 확인 (손절/익절)
- [ ] 전략 파라미터 조정 필요성 검토

**2주 후 결정:**
- 수익률 > 0% → Live Trading 진행
- 수익률 < 0% → 전략 수정 또는 파라미터 조정
- 버그 발생 → 수정 후 재테스트

---

## Phase 2: 소액 Live Trading (1-2주)

### 2.1 준비사항

**필요한 것:**
- Binance 실제 계정 (Verified)
- 테스트 완료된 전략
- **잃어도 되는 돈** (최소 $100-$500)

**절대 하지 말 것:**
- ❌ 생활비 투자
- ❌ 대출금 투자
- ❌ 전재산 투자
- ❌ 레버리지 사용

### 2.2 Live Trading 설정

```yaml
bot:
  mode: live               # ← paper에서 live로 변경
  
exchange:
  sandbox: false           # ← 테스트넷 비활성화
  testnet: false
  api_key: "YOUR_LIVE_API_KEY"
  api_secret: "YOUR_LIVE_SECRET"

risk:
  max_daily_loss_pct: 2.0  # ← 보수적으로 조정
  max_daily_trades: 5      # ← 처음에는 적게
  max_position_size_pct: 20.0  # ← 포지션도 작게
  max_positions: 1         # ← 하나만
  risk_per_trade_pct: 1.0  # ← 1% 리스크
  default_stop_loss_pct: 2.0
  default_take_profit_pct: 4.0
  circuit_breaker_loss_pct: 5.0

strategies:
  # Paper Trading에서 검증된 전략만 1개
  - name: "BTC_RSI_Live"
    enabled: true
    type: rsi
    symbol: "BTC/USDT"
    timeframe: "1h"
    params:
      period: 14
      oversold: 30
      overbought: 70
```

### 2.3 Live Trading 실행

```bash
# 설정 검증
python -m trading_core.config_cli validate

# 백업 생성
cp config/active/bot_config.yml config/active/bot_config.yml.paper
cp -r data data.paper.backup

# 새로 시작
docker-compose -f infra/docker/docker-compose.yml down
docker-compose -f infra/docker/docker-compose.yml up -d

# 로그 모니터링 (첫 1시간 집중)
docker-compose -f infra/docker/docker-compose.yml logs -f
```

### 2.4 1주일간 모니터링

**처음 24시간:**
- 30분마다 로그 확인
- 첫 거래가 정상적으로 체결되는지 확인
- 손절/익절 주문이 걸리는지 확인

**일일 체크리스트:**
- [ ] 봇 상태 확인
- [ ] 거래 내역 확인 (Binance 거래소에서)
- [ ] 손익 계산
- [ ] 리스크 한도 준수

**1주일 후 평가:**
- 총 거래 횟수: ___
- 승률: ___%
- 총 수익률: ___%
- 최대 낙폭: ___%

---

## Phase 3: 자본 확대 (2-4주 후)

### 3.1 확대 기준

**모두 만족 시 자본 확대:**
- [ ] 2주 연속 수익
- [ ] 승률 50% 이상
- [ ] 최대 낙폭 10% 미만
- [ ] 리스크 관리 정상 작동
- [ ] 정신적 스트레스 없음

### 3.2 단계별 확대

```
Week 1-2:  $100  (현재)
Week 3-4:  $300  (x3)
Week 5-6:  $500  (x5)
Week 7-8:  $1000 (x10)
Month 3+:  자본의 10-20%만 투자
```

### 3.3 추가 전략 고려

**첫 전략이 안정화된 후:**
```yaml
strategies:
  # 기존 전략
  - name: "BTC_RSI"
    enabled: true
    type: rsi
    symbol: "BTC/USDT"
    timeframe: "1h"
  
  # 추가 전략 1
  - name: "ETH_MA"
    enabled: true
    type: ma_cross
    symbol: "ETH/USDT"
    timeframe: "4h"
    params:
      fast_period: 50
      slow_period: 200
  
  # 추가 전략 2
  - name: "SOL_Breakout"
    enabled: false  # 처음엔 비활성화, 검증 후 활성화
    type: breakout
    symbol: "SOL/USDT"
    timeframe: "2h"
```

---

## 🚨 위급 상황 대처

### 봇 중지가 필요한 경우

```bash
# 정상 중지 (권장)
docker-compose -f infra/docker/docker-compose.yml down

# 강제 중지 (비상시)
docker-compose -f infra/docker/docker-compose.yml kill
```

### 롤백이 필요한 경우

```bash
# Paper Trading 설정으로 복구
cp config/active/bot_config.yml.paper config/active/bot_config.yml

# 이전 데이터로 복구
rm -rf data
cp -r data.paper.backup data

# 재시작
docker-compose -f infra/docker/docker-compose.yml up -d
```

---

## 📊 성공 기준

### 최소 성공 기준 (3개월)

- [ ] 봇이 90% 이상 가동 시간 유지
- [ ] 월간 수익률 +5% 이상
- [ ] 최대 낙폭 -10% 미만
- [ ] 리스크 관리 100% 작동
- [ ] 스트레스 없는 자동화

### 이정표

| 기간 | 목표 | 확인사항 |
|------|------|----------|
| Week 1 | Paper Trading | 신호 생성, 오류 없음 |
| Week 2 | Paper 결과 분석 | 수익률 확인 |
| Week 3 | $100 Live | 첫 실전 거래 |
| Week 4 | $100 결과 | 수익/손실 분석 |
| Month 2 | $300 확대 | 안정성 확인 |
| Month 3 | $500 확대 | 복리 효과 시작 |
| Month 6 | 자본 10% | 본격 자동화 |

---

## 💡 팁

### 전략 최적화

```bash
# 백테스팅으로 파라미터 최적화
python -m trading_backtest optimize \
  --symbol BTC/USDT \
  --strategy rsi \
  --param-grid '{"period": [10, 14, 20], "oversold": [25, 30, 35]}'
```

### 로그 분석

```bash
# 에러만 보기
docker-compose logs | grep ERROR

# 거래 내역 보기
docker-compose logs | grep "Position\|Trade"

# 일일 리포트
docker-compose logs | grep "Daily Report"
```

### 성능 모니터링

```bash
# Docker 리소스 사용량
docker stats

# 디스크 사용량
du -sh data/
du -sh logs/
```

---

## 📝 일지 작성

**매일 기록:**
```
Date: 2026-02-12
Mode: Paper
Trades: 3
Win/Loss: 2/1
PnL: +$12.50
Notes: RSI 신호 잘 작동, 손절이 아쉬움
```

**주간 회고:**
```
Week: 1
Total PnL: +$45.30
Win Rate: 60%
Max Drawdown: -3%
Improvements: 손절을 2%에서 1.5%로 조정
```

---

## 🔗 관련 문서

- [CUSTOMER_SETUP.md](CUSTOMER_SETUP.md) - 상세 설치 가이드
- [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) - 실전 체크리스트
- [ZERO_DOWNTIME_DEPLOYMENT.md](docs/ZERO_DOWNTIME_DEPLOYMENT.md) - 배포 가이드

---

**시작일:** _______________

**초기 자본:** $____________

**목표:** 월 ___% 수익

**각오:** ________________________________
