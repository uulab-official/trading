# Web Dashboard 사용 가이드

## 🌐 개요

트레이딩 봇을 실시간으로 모니터링할 수 있는 웹 대시보드를 제공합니다.

**특징:**
- ✅ 코어(트레이딩 엔진)와 분리된 독립 서비스
- ✅ 코어 배포 시에도 웹 대시보드는 중단되지 않음
- ✅ 실시간 WebSocket으로 1초 단위 업데이트
- ✅ 모바일/태블릿/PC 어디서나 접속 가능
- ✅ 읽기 전용으로 안전 (실수로 설정 변경 불가)

---

## 🏗️ 아키텍처

```
Docker Compose
├── trading-bot (코어)     - 배포 시 재시작됨
│   └── 상태를 SQLite에 저장
│
└── web-dashboard (웹)     - 항상 실행됨 (중단 없음)
    └── SQLite를 읽기 전용으로 마운트
```

**배포 시 흐름:**
1. `./deploy.sh` 실행
2. trading-bot 컨테이너 중지 → 새 버전 시작
3. web-dashboard는 그대로 유지 (중단 없음)
4. 웹 브라우저에서 새로고침만 하면 새로운 데이터 표시

---

## 🚀 시작하기

### 1. 실행

```bash
# 웹 대시보드 포함하여 시작
docker-compose -f infra/docker/docker-compose.yml up -d

# 확인
docker-compose ps
# trading-bot      running
# web-dashboard    running
```

### 2. 접속

```
http://localhost:8080
또는
http://<서버IP>:8080
```

**모바일에서도 접속:**
- 같은 WiFi에 연결된 경우 서버 IP로 접속
- 외부에서 접속하려면 포트포워딩 또는 VPN 필요

### 3. 확인사항

웹 대시보드 화면에서 확인할 수 있는 것:
- **실시간 상태**: 봇 실행/중지 상태
- **포트폴리오**: 총 자산, 수익률
- **포지션**: 현재 보유 중인 포지션 목록
- **PnL**: 실현/미실현 손익
- **거래 내역**: 최근 체결된 거래

---

## 📱 화면 구성

### 메인 대시보드

```
┌─────────────────────────────────────────────────────────────┐
│ 🤖 Trading Bot Dashboard        Status: ● Running 12:34:56 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Total Balance│  │   Total PnL  │  │ Open Positions│     │
│  │   $10,234.56 │  │   +$234.56 ▲ │  │      2        │     │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Open Positions                                        │  │
│  ├──────────┬──────┬────────┬───────────┬────────┬───────┤  │
│  │ Symbol   │ Side │  Size  │   Entry   │ Current│  PnL  │  │
│  ├──────────┼──────┼────────┼───────────┼────────┼───────┤  │
│  │ BTC/USDT │ LONG │ 0.05   │ $42,000   │$43,200 │+$60  │  │
│  │ ETH/USDT │ LONG │ 0.5    │ $2,800    │$2,750  │-$25  │  │
│  └──────────┴──────┴────────┴───────────┴────────┴───────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                                                      ● Connected
```

### 상태 표시

| 색상 | 의미 |
|------|------|
| 🟢 초록색 | 봇 실행 중 (Running) |
| 🔴 빨간색 | 봇 중지 (Stopped) |
| 🟡 노란색 | 연결 끊김 (WebSocket disconnect) |

---

## ⚡ Zero-Downtime 배포

### 배포 시에도 웹 계속 사용 가능

```bash
# 1. 배포 전 - 웹 대시보드 확인
open http://localhost:8080
# → "Running" 상태 확인

# 2. 배포 실행
./infra/scripts/deploy.sh v1.1.0
# → trading-bot만 재시작
# → web-dashboard는 계속 실행

# 3. 배포 후 - 웹에서 새로고침
# 브라우저에서 F5 또는 새로고침 버튼
# → 새 버전의 데이터 표시
```

**중요:**
- 배포 중에도 웹 대시보드는 계속 볼 수 있음
- 다만 배포 중 10-30초간 데이터가 업데이트되지 않음
- 새로고침하면 새로운 데이터 표시

---

## 🔧 고급 설정

### 외부에서 접속하기

**방법 1: 포트포워딩 (집 공유기)**
```
공유기 설정 → 포트포워딩
외부 8080 → 낶부 <서버IP>:8080
```

**방법 2: ngrok (임시)**
```bash
# ngrok 설치 후
ngrok http 8080

# 출력된 URL로 어디서나 접속
# https://xxxx.ngrok.io
```

**방법 3: VPN (안전)**
- Tailscale, ZeroTier 등 설치
- VPN 네트워크 내에서 접속

### 비밀번호 보호 (기본 인증 추가)

```yaml
# docker-compose.yml에 추가
services:
  web-dashboard:
    environment:
      - AUTH_USERNAME=admin
      - AUTH_PASSWORD=yourpassword
```

---

## 🐛 문제 해결

### 웹 대시보드가 안 열림

```bash
# 1. 컨테이너 상태 확인
docker-compose ps

# 2. 로그 확인
docker-compose logs web-dashboard

# 3. 포트 충돌 확인
lsof -i :8080

# 4. 재시작
docker-compose restart web-dashboard
```

### 데이터가 안 보임

```bash
# 1. trading-bot이 실행 중인지 확인
docker-compose ps

# 2. SQLite 파일 확인
ls -la data/state.db

# 3. 권한 확인
docker-compose exec web-dashboard ls -la /app/data/

# 4. 읽기 권한 부여 (필요시)
chmod 644 data/state.db
```

### 실시간 업데이트 안 됨

**원인:** WebSocket 연결 문제

**해결:**
1. 브라우저 새로고침 (F5)
2. 다른 브라우저 시도 (Chrome, Firefox, Safari)
3. 방화벽 확인 (WebSocket 포트 8080 허용)

---

## 📊 REST API

웹 대시보드는 REST API도 제공합니다:

```bash
# 현재 상태 조회
curl http://localhost:8080/api/status

# 거래 내역 조회
curl http://localhost:8080/api/history?limit=10
```

**응답 예시:**
```json
{
  "status": "running",
  "timestamp": "2026-02-12T12:34:56",
  "portfolio": {
    "balance": 10000.00,
    "total_pnl": 234.56
  },
  "positions": [
    {
      "symbol": "BTC/USDT",
      "side": "long",
      "size": 0.05,
      "entry_price": 42000.00
    }
  ]
}
```

---

## 🔄 WebSocket 실시간 연결

JavaScript 예시:
```javascript
const ws = new WebSocket('ws://localhost:8080/ws');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Portfolio:', data.data.portfolio);
  console.log('Positions:', data.data.positions);
};
```

---

## 📝 요약

| 기능 | 설명 |
|------|------|
| **접속** | http://localhost:8080 |
| **실시간** | 1초 단위 WebSocket 업데이트 |
| **배포** | 코어 재시작해도 웹은 중단 없음 |
| **데이터** | SQLite 읽기 전용으로 안전 |
| **API** | REST + WebSocket 제공 |

**다음 단계:**
- [PERSONAL_TRADING_GUIDE.md](PERSONAL_TRADING_GUIDE.md) - 실제 트레이딩 시작하기
