# 거래소 API 명세서

## 선정 거래소: Binance (바이낸스)

### 선택 이유
1. **유동성**: 세계 최대 암호화폐 거래소, 높은 유동성
2. **수수료**: 현물 0.1% (BNB 할인 시 0.075%)
3. **API 안정성**: CCXT 지원 우수, WebSocket 안정적
4. **테스트넷**: Paper trading 가능한 testnet 제공
5. **한국 접근성**: 제한적이지만 VPN 없이 API 사용 가능

---

## API 설정

### 1. API 키 발급

```bash
# Binance Testnet (테스트용)
URL: https://testnet.binance.vision/
- 테스트용 자동 입금
- 실제 돈 없이 테스트 가능
- 실시간 시장 데이터

# Binance Live (실전용)
URL: https://www.binance.com/en/my/settings/api-management
- IP 화이트리스트 필수
- 출금 권한 제거
- 거래 권한만 부여
```

### 2. 권한 설정

| 권한 | 테스트넷 | 실전 | 비고 |
|------|----------|------|------|
| Reading | ✅ | ✅ | 잔고/주문 조회 |
| Spot Trading | ✅ | ✅ | 현물 거래 |
| Margin Trading | ❌ | ❌ | 사용 안함 |
| Futures | ❌ | ❌ | 현물만 사용 |
| Withdrawal | ❌ | ❌ | 절대 사용 안함 |

### 3. IP 화이트리스트
- 서버 IP 고정 필요
- AWS/GCP는 탄력적 IP 사용
- NAS는 DDNS + IP 업데이트 자동화

---

## Rate Limits

### REST API
```
Weight per IP: 1200/분
- Order placement: 1 weight
- Order cancellation: 1 weight
- Query order: 2 weight
- Account info: 10 weight
```

### WebSocket
```
Connection limit: 300/5분
Stream limit: 1024 streams/connection
```

### 처리 전략
```python
# CCXT는 기본적으로 rate limit 처리
exchange = ccxt.binance({
    'enableRateLimit': True,  # 필수
    'options': {
        'defaultType': 'spot',
    }
})
```

---

## API 엔드포인트

### 현물 거래 (Spot)

#### 1. 시장 데이터 (Public)
```
GET /api/v3/ticker/24hr        # 24시간 티커
GET /api/v3/klines             # 캔들스틱 데이터
GET /api/v3/depth              # 오더북
GET /api/v3/ticker/price       # 현재가
GET /api/v3/exchangeInfo       # 거래소 정보
```

#### 2. 계정 (Private)
```
GET /api/v3/account            # 계정 정보
GET /api/v3/myTrades           # 거래 내역
GET /api/v3/openOrders         # 미체결 주문
GET /api/v3/allOrders          # 전체 주문
```

#### 3. 거래 (Private)
```
POST /api/v3/order             # 주문 생성
DELETE /api/v3/order           # 주문 취소
GET /api/v3/order              # 주문 조회
```

### WebSocket Streams

#### Individual Symbol Streams
```
wss://stream.binance.com:9443/ws/<symbol>@ticker     # 24hr 티커
wss://stream.binance.com:9443/ws/<symbol>@depth      # 오더북
wss://stream.binance.com:9443/ws/<symbol>@kline_1h   # 1시간 캔들
wss://stream.binance.com:9443/ws/<symbol>@trade      # 실시간 체결
```

#### Combined Streams
```
wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/ethusdt@ticker
```

---

## 주문 타입

### 1. MARKET (시장가)
```python
# 즉시 체결, 현재가로 실행
order = exchange.create_market_buy_order('BTC/USDT', amount=0.001)
```

### 2. LIMIT (지정가)
```python
# 특정 가격에 주문
order = exchange.create_limit_buy_order(
    'BTC/USDT', 
    amount=0.001,
    price=50000
)
```

### 3. STOP_LOSS / TAKE_PROFIT
```python
# 스탑 주문 (선물에서만, 현물은 LIMIT 사용)
# 현물에서는 주문 후 모니터링으로 구현
```

---

## 수수료 구조

### 현물 거래
| 유형 | Maker | Taker |
|------|-------|-------|
| 일반 | 0.1% | 0.1% |
| BNB 할인 | 0.075% | 0.075% |
| VIP 1 (30일 거래액 50BTC+) | 0.09% | 0.1% |

### 입출금
- 입금: 무료
- 출금: 네트워크 수수료만 부과

---

## 최소 주문 금액

| 심볼 | 최소 수량 | 최소 금액 |
|------|-----------|-----------|
| BTC/USDT | 0.00001 BTC | 10 USDT |
| ETH/USDT | 0.0001 ETH | 10 USDT |
| SOL/USDT | 0.01 SOL | 10 USDT |

---

## CCXT 설정 예시

```python
import ccxt

# 테스트넷
exchange = ccxt.binance({
    'apiKey': 'YOUR_TESTNET_API_KEY',
    'secret': 'YOUR_TESTNET_SECRET',
    'enableRateLimit': True,
    'sandbox': True,  # 테스트넷 모드
    'options': {
        'defaultType': 'spot',
        'adjustForTimeDifference': True,
    }
})

# 실전
exchange = ccxt.binance({
    'apiKey': 'YOUR_LIVE_API_KEY',
    'secret': 'YOUR_LIVE_SECRET',
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
        'adjustForTimeDifference': True,
    }
})

# 마켓 로드
exchange.load_markets()

# 잔고 조회
balance = exchange.fetch_balance()
print(balance['USDT']['free'])  # 사용 가능 USDT

# 티커 조회
ticker = exchange.fetch_ticker('BTC/USDT')
print(ticker['last'])  # 현재가

# 과거 데이터
ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1h', since=1640000000000, limit=100)
```

---

## 에러 처리

### 자주 발생하는 에러
```python
# Insufficient Balance
ccxt.InsufficientFunds: not enough balance

# Rate Limit
ccxt.RateLimitExceeded: rate limit exceeded

# Invalid Order
ccxt.InvalidOrder: precision is over the maximum defined

# Network
ccxt.NetworkError: connection timeout
```

### 재시도 전략
```python
import ccxt
from time import sleep

def create_order_with_retry(exchange, symbol, side, amount, max_retries=3):
    for i in range(max_retries):
        try:
            return exchange.create_market_order(symbol, side, amount)
        except ccxt.RateLimitExceeded:
            sleep(1)
        except ccxt.NetworkError:
            sleep(2)
        except Exception as e:
            raise e
    raise Exception("Max retries exceeded")
```

---

## 보안 체크리스트

- [ ] API 키는 `.env` 파일에만 저장
- [ ] `.env`는 `.gitignore`에 포함
- [ ] 출금 권한 제거
- [ ] IP 화이트리스트 설정
- [ ] Testnet으로 먼저 테스트
- [ ] API 키 정기적 교체 (3개월)
- [ ] 로그에 API 키 노출 금지

---

## 대안 거래소

### Bybit
- 수수료: 0.1% (현물)
- 장점: API 안정적, 한국 사용자 친화
- 단점: 유동성 Binance보다 낮음

### OKX
- 수수료: 0.08% (Maker), 0.1% (Taker)
- 장점: 다양한 상품, 수수료 경쟁력
- 단점: 인터페이스 복잡

### 결론
**1차: Binance (유동성 + 수수료)**
**2차: Bybit (백업용)**
