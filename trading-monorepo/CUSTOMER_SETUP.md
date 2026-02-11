# Trading Bot - Customer Setup Guide

## 🚀 Quick Start

### 1. 설치

```bash
# 저장소 클론
git clone <your-repo-url>
cd trading-monorepo

# Python 3.9+ 확인
python3 --version

# 가상환경 생성
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 패키지 설치
pip install -e packages/core
pip install -e packages/data
pip install -e packages/strategy
pip install -e apps/trading-bot
```

### 2. 설정 초기화

```bash
# 설정 파일 초기화
python -m trading_core.config_cli init
```

이 명령은 다음 파일을 생성합니다:
- `config/active/bot_config.yml` - 봇 설정
- `config/secrets/secrets.yml` - API 키 및 비밀 (⚠️ 절대 Git에 커밋하지 마세요!)

### 3. API 키 설정

1. **Binance API 키 생성**:
   - [Binance](https://www.binance.com) 로그인
   - API Management → Create API
   - 권한: **Enable Reading** + **Enable Spot & Margin Trading** (출금은 비활성화 권장)

2. **secrets.yml 수정**:
   ```bash
   nano config/secrets/secrets.yml
   ```

   ```yaml
   binance:
     api_key: "YOUR_ACTUAL_API_KEY"
     api_secret: "YOUR_ACTUAL_API_SECRET"
   ```

### 4. 텔레그램 설정 (선택)

```bash
# 텔레그램 봇 설정
python -m trading_core.config_cli telegram-setup
```

### 5. 설정 검증

```bash
# 설정 검증
python -m trading_core.config_cli validate
```

### 6. 봇 실행

```bash
# Paper Trading (모의투자) - 권장
python apps/trading-bot/trading_bot/main.py

# 또는
python -m trading_bot
```

---

## ⚙️ 설정 파일 구조

### bot_config.yml

```yaml
bot:
  id: "my-trading-bot"
  name: "My Trading Bot"
  version: "1.0.0"
  mode: paper  # paper 또는 live

exchange:
  id: binance
  sandbox: true  # true: 테스트넷, false: 실제 거래

risk:
  max_daily_loss_pct: 3.0
  max_positions: 2
  risk_per_trade_pct: 1.0

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

### secrets.yml

⚠️ **이 파일은 절대 Git에 커밋하지 마세요!**

```yaml
binance:
  api_key: "YOUR_API_KEY"
  api_secret: "YOUR_API_SECRET"

telegram:
  bot_token: "YOUR_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"
```

---

## 📋 사용 가능한 전략

### 1. RSI Strategy
```yaml
type: rsi
params:
  period: 14
  oversold: 30
  overbought: 70
```

### 2. Moving Average Cross
```yaml
type: ma_cross
params:
  fast_period: 50
  slow_period: 200
```

### 3. Breakout Strategy
```yaml
type: breakout
params:
  lookback_period: 20
  breakout_threshold: 2.0
```

---

## 🛡️ 리스크 관리 설정

| 설정 | 설명 | 기본값 | 권장값 |
|------|------|--------|--------|
| `max_daily_loss_pct` | 일일 최대 손실 한도 | 3.0 | 2-5 |
| `max_positions` | 동시 최대 포지션 수 | 2 | 2-5 |
| `risk_per_trade_pct` | 거래당 리스크 | 1.0 | 0.5-2 |
| `circuit_breaker_loss_pct` | 서킷브레이커 트리거 | 5.0 | 5-10 |

---

## 📊 모니터링

### 로그 확인
```bash
# 실시간 로그
tail -f logs/trading-bot.log

# 오류만 확인
grep ERROR logs/trading-bot.log
```

### 텔레그램 명령어
- `/status` - 봇 상태 확인
- `/portfolio` - 포트폴리오 조회
- `/positions` - 포지션 목록
- `/stop` - 봇 중지

---

## 🚨 중요 안내사항

1. **반드시 Paper Trading부터 시작하세요**
   - `mode: paper`로 설정하여 테스트
   - 충분한 기간(최소 1-2주) 테스트 후 live 전환

2. **소액으로 시작하세요**
   - Live 모드 전환 시 소액으로 시작
   - 성능 검증 후 점진적으로 증액

3. **API 키 보안**
   - `config/secrets/`는 `.gitignore`에 포함됨
   - API 키는 절대 공개 저장소에 업로드하지 마세요
   - IP 화이트리스트 설정 권장

4. **정기적인 모니터링**
   - 로그 정기 확인
   - 전략 성능 모니터링
   - 시장 상황 변화에 따른 전략 조정

---

## 🆘 문제 해결

### 설정 오류
```bash
# 설정 검증
python -m trading_core.config_cli validate

# 설정 초기화
python -m trading_core.config_cli init
```

### 텔레그램 연결 문제
```bash
# 텔레그램 테스트
python -m trading_core.config_cli test-telegram
```

### 패키지 설치 문제
```bash
# 패키지 재설치
pip install --force-reinstall -e packages/core
```

---

## 📞 지원

문제 발생 시:
1. 로그 확인: `logs/trading-bot.log`
2. 설정 검증: `python -m trading_core.config_cli validate`
3. 문서 확인: `docs/TELEGRAM_SETUP.md`
