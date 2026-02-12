# SaaS Architecture for Trading Bot

## Executive Summary

단일 사용자 어플라이언스에서 **멀티 테넌트 SaaS**로 전환하는 완전한 아키텍처 설계

---

## 1. 멀티 테넌시 모델 선택

### 추천: Hybrid Model (D)

```
┌─────────────────────────────────────────────────────────────┐
│                     SaaS Platform                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              API Gateway / Load Balancer             │    │
│  │         (Auth, Rate Limiting, Routing)              │    │
│  └────────────────────┬────────────────────────────────┘    │
│                       │                                      │
│  ┌────────────────────▼────────────────────────────────┐    │
│  │              Shared Services                        │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌───────────┐   │    │
│  │  │   User      │  │  Billing    │  │  Admin    │   │    │
│  │  │  Service    │  │  Service    │  │ Dashboard │   │    │
│  │  └─────────────┘  └─────────────┘  └───────────┘   │    │
│  │                                                    │    │
│  │  ┌─────────────────────────────────────────────┐   │    │
│  │  │      PostgreSQL (Shared Metadata)           │   │    │
│  │  │  - Users, Subscriptions, API Keys           │   │    │
│  │  └─────────────────────────────────────────────┘   │    │
│  └────────────────────────────────────────────────────┘    │
│                       │                                      │
│  ┌────────────────────▼────────────────────────────────┐    │
│  │         Trading Engine Pool                         │    │
│  │                                                      │    │
│  │  ┌──────────────┐  ┌──────────────┐                 │    │
│  │  │  User A      │  │  User B      │  ...            │    │
│  │  │  Container   │  │  Container   │                 │    │
│  │  │              │  │              │                 │    │
│  │  │ ┌──────────┐ │  │ ┌──────────┐ │                 │    │
│  │  │ │  Bot     │ │  │ │  Bot     │ │                 │    │
│  │  │ │  Process │ │  │ │  Process │ │                 │    │
│  │  │ └──────────┘ │  │ └──────────┘ │                 │    │
│  │  │              │  │              │                 │    │
│  │  │ ┌──────────┐ │  │ ┌──────────┐ │                 │    │
│  │  │ │  SQLite  │ │  │ │  SQLite  │ │                 │    │
│  │  │ │  (State) │ │  │ │  (State) │ │                 │    │
│  │  │ └──────────┘ │  │ └──────────┘ │                 │    │
│  │  └──────────────┘  └──────────────┘                 │    │
│  │                                                      │    │
│  │  Resource Limits: CPU/Memory per container          │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**왜 Hybrid Model인가?**

| 모델 | 설명 | 장점 | 단점 | 적합성 |
|------|------|------|------|--------|
| **A. Shared DB** | tenant_id 컬럼 | 간단, 리소스 효율 | 보안 복잡, 격리 어려움 | ❌ 트레이딩 격리 어려움 |
| **B. DB per Tenant** | PostgreSQL 스키마 | 좋은 격리 | 많은 DB 연결 | ⚠️ 리소스 많음 |
| **C. Shared Nothing** | VM/컨테이너 완전 분리 | 완벽한 격리 | 비용 높음 | ⚠️ 고비용 |
| **D. Hybrid** | 메타데이터 공유 + 트레이딩 격리 | 균형 | 복잡 | ✅ **최적** |

**Hybrid Model 선택 이유:**
1. 사용자 메타데이터는 공유 (효율적)
2. 실제 트레이딩은 격리 (안전)
3. 리소스 제한 가능 (공정 사용)
4. 기존 코드 재사용 (SQLite 유지)

---

## 2. 데이터베이스 아키텍처

### 2.1 Shared Metadata Database (PostgreSQL)

```sql
-- users 테이블
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    subscription_tier VARCHAR(50) DEFAULT 'free',
    subscription_expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- user_api_keys 테이블
CREATE TABLE user_api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    key_hash VARCHAR(255) NOT NULL,
    name VARCHAR(100),
    permissions JSONB DEFAULT '[]',
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP
);

-- subscriptions 테이블
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    tier VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'active',
    started_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP,
    payment_provider VARCHAR(50),
    payment_id VARCHAR(255)
);

-- user_trading_configs 테이블 (설정 저장)
CREATE TABLE user_trading_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    config JSONB NOT NULL,  -- 사용자 트레이딩 설정
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- user_resources 테이블 (리소스 사용량)
CREATE TABLE user_resources (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    max_strategies INTEGER DEFAULT 3,
    max_positions INTEGER DEFAULT 10,
    cpu_limit VARCHAR(50) DEFAULT '0.5',  -- 0.5 cores
    memory_limit VARCHAR(50) DEFAULT '512Mi',
    current_cpu_usage FLOAT DEFAULT 0,
    current_memory_usage FLOAT DEFAULT 0,
    last_updated TIMESTAMP DEFAULT NOW()
);

-- audit_logs 테이블 (보안 감사)
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    action VARCHAR(100) NOT NULL,
    details JSONB,
    ip_address INET,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_api_keys_user ON user_api_keys(user_id);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id, created_at);
```

### 2.2 Per-User Trading Data (SQLite in Container)

각 사용자 컨테이너 내에 SQLite 유지:
```
/user-data/
├── trading.db          # 포트폴리오, 주문, 상태
├── logs/
│   └── trading.log     # 거래 로그
└── cache/              # 임시 데이터
```

**동기화 전략:**
```python
# 주기적으로 공유 DB에 메타데이터 동기화
class DataSync:
    async def sync_to_shared_db(self, user_id: str):
        """사용자 트레이딩 데이터를 공유 DB로 동기화"""
        # 1. SQLite에서 요약 데이터 읽기
        summary = await self.get_trading_summary()
        
        # 2. 공유 PostgreSQL에 저장
        await self.shared_db.execute(
            """
            INSERT INTO user_trading_stats (user_id, total_trades, 
                total_pnl, last_updated)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                total_trades = $2,
                total_pnl = $3,
                last_updated = NOW()
            """,
            user_id, summary['trades'], summary['pnl']
        )
```

---

## 3. 서비스 아키텍처

### 3.1 마이크로서비스 구조

```
┌─────────────────────────────────────────────────────┐
│                   API Gateway                        │
│  (Kong / Nginx / Traefik)                           │
│  - Authentication                                  │
│  - Rate Limiting                                   │
│  - Routing                                         │
└──────────────┬──────────────────────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
┌───▼────┐          ┌────▼─────┐
│  Auth  │          │  User    │
│Service │          │ Service  │
└───┬────┘          └────┬─────┘
    │                     │
┌───▼────────────────────▼─────┐
│       Shared PostgreSQL      │
└──────────────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
┌───▼────┐          ┌────▼──────┐
│Billing │          │  Trading  │
│Service │          │  Manager  │
└───┬────┘          └────┬──────┘
    │                     │
┌───▼────┐          ┌────▼──────┐
│Stripe  │          │Kubernetes │
│API     │          │  Cluster  │
└────────┘          └───────────┘
```

### 3.2 주요 서비스

**1. API Gateway**
```yaml
# kong.yml
services:
  - name: user-service
    url: http://user-service:8000
    routes:
      - name: user-routes
        paths:
          - /api/v1/users
    plugins:
      - name: jwt
        config:
          key_claim_name: iss
          secret_is_base64: false
      - name: rate-limiting
        config:
          minute: 60
          policy: redis
```

**2. User Service**
```python
# user_service/main.py
from fastapi import FastAPI, Depends
from auth import get_current_user

app = FastAPI()

@app.get("/api/v1/users/me")
async def get_current_user_info(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "subscription": user.subscription_tier,
        "resources": await get_user_resources(user.id)
    }

@app.put("/api/v1/users/config")
async def update_trading_config(
    config: TradingConfig,
    user: User = Depends(get_current_user)
):
    # 설정 검증
    await validate_config(config, user.subscription_tier)
    
    # 저장
    await save_user_config(user.id, config)
    
    # 트레이딩 엔진에 알림
    await notify_trading_engine(user.id, "config_updated")
    
    return {"status": "updated"}
```

**3. Trading Manager Service**
```python
# trading_manager/main.py
class TradingManager:
    """사용자 트레이딩 인스턴스 관리"""
    
    def __init__(self):
        self.k8s = KubernetesClient()
        self.user_pods = {}  # user_id -> pod_name
    
    async def start_trading(self, user_id: str):
        """사용자 트레이딩 시작"""
        # 1. 사용자 설정 가져오기
        config = await self.get_user_config(user_id)
        
        # 2. 리소스 제한 확인
        resources = await self.get_user_resources(user_id)
        
        # 3. Kubernetes Pod 생성
        pod = await self.k8s.create_pod(
            name=f"trading-bot-{user_id}",
            image="trading-bot/engine:latest",
            resources={
                "limits": {
                    "cpu": resources.cpu_limit,
                    "memory": resources.memory_limit
                }
            },
            env={
                "USER_ID": user_id,
                "DATABASE_PATH": f"/data/{user_id}/trading.db",
                "CONFIG": json.dumps(config)
            },
            volumes=[
                {
                    "name": "user-data",
                    "persistentVolumeClaim": {
                        "claimName": f"pvc-{user_id}"
                    }
                }
            ]
        )
        
        self.user_pods[user_id] = pod.metadata.name
        
        return {"status": "started", "pod": pod.metadata.name}
    
    async def stop_trading(self, user_id: str):
        """사용자 트레이딩 중지"""
        pod_name = self.user_pods.get(user_id)
        if pod_name:
            await self.k8s.delete_pod(pod_name)
            del self.user_pods[user_id]
        
        return {"status": "stopped"}
    
    async def get_trading_status(self, user_id: str):
        """사용자 트레이딩 상태"""
        pod_name = self.user_pods.get(user_id)
        if not pod_name:
            return {"status": "stopped"}
        
        # Pod 상태 확인
        pod = await self.k8s.get_pod(pod_name)
        
        # 트레이딩 엔진 상태 확인
        status = await self.query_trading_engine(user_id, "status")
        
        return {
            "status": "running" if pod.status.phase == "Running" else "pending",
            "pod_status": pod.status.to_dict(),
            "trading": status
        }
```

---

## 4. 인증 및 보안

### 4.1 JWT 기반 인증

```python
# auth/jwt_handler.py
import jwt
from datetime import datetime, timedelta
from typing import Optional

class AuthManager:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key
    
    def create_access_token(self, user_id: str, tier: str) -> str:
        """JWT 액세스 토큰 생성"""
        payload = {
            "sub": user_id,
            "tier": tier,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(hours=24),
            "type": "access"
        }
        return jwt.encode(payload, self.secret_key, algorithm="HS256")
    
    def create_api_key(self, user_id: str, name: str) -> str:
        """API 키 생성 (트레이딩용)"""
        api_key = secrets.token_urlsafe(32)
        
        # 해시하여 저장
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        
        # DB에 저장
        await db.execute(
            "INSERT INTO user_api_keys (user_id, key_hash, name) VALUES ($1, $2, $3)",
            user_id, key_hash, name
        )
        
        # 사용자에게는 원본 반환 (한 번만)
        return api_key
    
    async def verify_api_key(self, api_key: str) -> Optional[User]:
        """API 키 검증"""
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        
        result = await db.fetchrow(
            """
            SELECT u.* FROM users u
            JOIN user_api_keys k ON u.id = k.user_id
            WHERE k.key_hash = $1 AND k.expires_at > NOW()
            """,
            key_hash
        )
        
        if result:
            # last_used 업데이트
            await db.execute(
                "UPDATE user_api_keys SET last_used_at = NOW() WHERE key_hash = $1",
                key_hash
            )
            return User(**result)
        
        return None
```

### 4.2 권한 관리 (RBAC)

```python
# auth/permissions.py
from enum import Enum

class Permission(Enum):
    # Trading permissions
    TRADING_READ = "trading:read"
    TRADING_WRITE = "trading:write"
    TRADING_DELETE = "trading:delete"
    
    # Strategy permissions
    STRATEGY_CREATE = "strategy:create"
    STRATEGY_BACKTEST = "strategy:backtest"
    
    # Admin permissions
    USER_MANAGE = "user:manage"
    BILLING_MANAGE = "billing:manage"

# 티어별 권한
TIER_PERMISSIONS = {
    "free": [
        Permission.TRADING_READ,
        Permission.STRATEGY_CREATE,
    ],
    "basic": [
        Permission.TRADING_READ,
        Permission.TRADING_WRITE,
        Permission.STRATEGY_CREATE,
        Permission.STRATEGY_BACKTEST,
    ],
    "pro": [
        Permission.TRADING_READ,
        Permission.TRADING_WRITE,
        Permission.TRADING_DELETE,
        Permission.STRATEGY_CREATE,
        Permission.STRATEGY_BACKTEST,
    ],
    "enterprise": list(Permission)  # 모든 권한
}

def check_permission(user: User, permission: Permission) -> bool:
    """사용자 권한 확인"""
    return permission in TIER_PERMISSIONS.get(user.subscription_tier, [])
```

---

## 5. API 설계

### 5.1 REST API

```yaml
# API Specification
openapi: 3.0.0
info:
  title: Trading Bot SaaS API
  version: 1.0.0

paths:
  # Authentication
  /auth/register:
    post:
      summary: Register new user
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                email: { type: string }
                password: { type: string }
      responses:
        201:
          description: User created
          
  /auth/login:
    post:
      summary: Login
      responses:
        200:
          content:
            application/json:
              schema:
                type: object
                properties:
                  access_token: { type: string }
                  refresh_token: { type: string }

  # Trading
  /api/v1/trading/status:
    get:
      summary: Get trading status
      security:
        - bearerAuth: []
      responses:
        200:
          content:
            application/json:
              schema:
                type: object
                properties:
                  status: { type: string, enum: [running, stopped] }
                  portfolio:
                    type: object
                    properties:
                      total_value: { type: number }
                      balance: { type: number }
                      positions: { type: array }

  /api/v1/trading/start:
    post:
      summary: Start trading
      security:
        - bearerAuth: []
      responses:
        200:
          description: Trading started

  /api/v1/trading/stop:
    post:
      summary: Stop trading
      security:
        - bearerAuth: []
      responses:
        200:
          description: Trading stopped

  # Strategies
  /api/v1/strategies:
    get:
      summary: List strategies
      security:
        - bearerAuth: []
      responses:
        200:
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: '#/components/schemas/Strategy'

    post:
      summary: Create strategy
      security:
        - bearerAuth: []
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Strategy'
      responses:
        201:
          description: Strategy created

  # Backtesting
  /api/v1/backtest:
    post:
      summary: Run backtest
      security:
        - bearerAuth: []
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                strategy_id: { type: string }
                symbol: { type: string }
                start_date: { type: string, format: date }
                end_date: { type: string, format: date }
      responses:
        202:
          description: Backtest queued
          content:
            application/json:
              schema:
                type: object
                properties:
                  job_id: { type: string }

  /api/v1/backtest/{job_id}:
    get:
      summary: Get backtest results
      security:
        - bearerAuth: []
      responses:
        200:
          content:
            application/json:
              schema:
                type: object
                properties:
                  status: { type: string }
                  results:
                    type: object
                    properties:
                      total_return: { type: number }
                      sharpe_ratio: { type: number }
                      max_drawdown: { type: number }
```

### 5.2 WebSocket (실시간 데이터)

```javascript
// 클라이언트 예시
const ws = new WebSocket('wss://api.tradingbot.io/ws', [], {
  headers: { Authorization: 'Bearer ' + token }
});

ws.onopen = () => {
  // 구독
  ws.send(JSON.stringify({
    action: 'subscribe',
    channel: 'portfolio'
  }));
  
  ws.send(JSON.stringify({
    action: 'subscribe',
    channel: 'trades'
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  if (data.channel === 'portfolio') {
    updatePortfolioUI(data.payload);
  } else if (data.channel === 'trades') {
    addTradeToHistory(data.payload);
  }
};
```

---

## 6. 인프라스트럭처

### 6.1 Kubernetes 구성

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: trading-bot-saas

---
# k8s/postgresql.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: trading-bot-saas
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
      - name: postgres
        image: postgres:15
        env:
        - name: POSTGRES_DB
          value: tradingbot
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: username
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: password
        volumeMounts:
        - name: postgres-storage
          mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
  - metadata:
      name: postgres-storage
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 50Gi

---
# k8s/trading-engine-rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: trading-manager
  namespace: trading-bot-saas

---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: trading-manager
  namespace: trading-bot-saas
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "create", "delete"]
- apiGroups: [""]
  resources: ["persistentvolumeclaims"]
  verbs: ["get", "list", "create", "delete"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: trading-manager
  namespace: trading-bot-saas
subjects:
- kind: ServiceAccount
  name: trading-manager
  namespace: trading-bot-saas
roleRef:
  kind: Role
  name: trading-manager
  apiGroup: rbac.authorization.k8s.io
```

### 6.2 리소스 제한

```yaml
# k8s/trading-pod-template.yaml
apiVersion: v1
kind: Pod
metadata:
  name: trading-bot-{user_id}
  namespace: trading-bot-saas
  labels:
    app: trading-bot
    user_id: {user_id}
spec:
  serviceAccountName: trading-bot
  containers:
  - name: trading-engine
    image: trading-bot/engine:latest
    resources:
      requests:
        cpu: "100m"
        memory: "256Mi"
      limits:
        cpu: "500m"        # 티어별 조정
        memory: "512Mi"    # 티어별 조정
    env:
    - name: USER_ID
      value: {user_id}
    - name: DATABASE_PATH
      value: /data/trading.db
    volumeMounts:
    - name: user-data
      mountPath: /data
  volumes:
  - name: user-data
    persistentVolumeClaim:
      claimName: pvc-{user_id}
```

---

## 7. 빌링 및 구독

### 7.1 Stripe 통합

```python
# billing/stripe_integration.py
import stripe

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

class BillingManager:
    async def create_subscription(self, user_id: str, price_id: str):
        """구독 생성"""
        user = await self.get_user(user_id)
        
        # Stripe Customer 생성
        customer = stripe.Customer.create(
            email=user.email,
            metadata={'user_id': user_id}
        )
        
        # 구독 생성
        subscription = stripe.Subscription.create(
            customer=customer.id,
            items=[{'price': price_id}],
            metadata={'user_id': user_id}
        )
        
        # DB에 저장
        await db.execute(
            """
            INSERT INTO subscriptions 
            (user_id, tier, stripe_customer_id, stripe_subscription_id, status)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, 
            self.get_tier_from_price(price_id),
            customer.id,
            subscription.id,
            'active'
        )
        
        return subscription
    
    async def handle_webhook(self, payload: dict, sig: str):
        """Stripe webhook 처리"""
        event = stripe.Webhook.construct_event(
            payload, sig, os.getenv('STRIPE_WEBHOOK_SECRET')
        )
        
        if event['type'] == 'invoice.payment_succeeded':
            subscription = event['data']['object']
            user_id = subscription['metadata']['user_id']
            
            # 구독 갱신
            await self.renew_subscription(user_id)
            
        elif event['type'] == 'invoice.payment_failed':
            subscription = event['data']['object']
            user_id = subscription['metadata']['user_id']
            
            # 구독 일시 중지
            await self.suspend_subscription(user_id)
```

### 7.2 사용량 기반 과금 (선택)

```python
# billing/usage_tracking.py
class UsageTracker:
    async def track_trade(self, user_id: str):
        """거래 추적"""
        await redis.incr(f"usage:{user_id}:trades")
        
    async def get_monthly_usage(self, user_id: str) -> dict:
        """월간 사용량 조회"""
        trades = await redis.get(f"usage:{user_id}:trades") or 0
        
        return {
            "trades": int(trades),
            "cpu_hours": await self.get_cpu_hours(user_id),
            "data_transfer": await self.get_data_transfer(user_id)
        }
```

---

## 8. 구현 로드맵

### Phase 1: Core Infrastructure (4주)
- [ ] Kubernetes 클러스터 설정
- [ ] PostgreSQL 데이터베이스 구축
- [ ] API Gateway (Kong) 설정
- [ ] 기본 인증 서비스 구현

### Phase 2: Trading Engine (3주)
- [ ] Trading Manager 서비스
- [ ] Per-user 컨테이너 오케스트레이션
- [ ] 기존 코드 컨테이너화
- [ ] 리소스 제한 구현

### Phase 3: API & Frontend (4주)
- [ ] REST API 구현
- [ ] WebSocket 서버
- [ ] React 웹 대시보드
- [ ] 사용자 설정 UI

### Phase 4: Billing & Admin (2주)
- [ ] Stripe 통합
- [ ] 구독 관리
- [ ] Admin 대시보드
- [ ] 사용량 모니터링

### Phase 5: Security & Hardening (2주)
- [ ] 보안 감사
- [ ] DDoS 방어
- [ ] 데이터 암호화
- [ ] Compliance (GDPR 등)

**총 예상: 15주 (3.5개월)**

---

## 9. 예상 비용

### 인프라 (월간, 1000 사용자 기준)

| 구성 요소 | 인스턴스 | 비용/월 |
|-----------|----------|---------|
| Kubernetes Control Plane | 3x t3.medium | $150 |
| PostgreSQL | db.r5.large | $250 |
| Redis | cache.r5.large | $100 |
| Trading Pods | 1000x shared | $500 |
| Load Balancer | ALB | $50 |
| Storage | 500GB | $50 |
| **총계** | | **~$1,100** |

**사용자당 비용: ~$1.10/월**

### 수익 모델

| 티어 | 가격 | 타겟 마진 |
|------|------|-----------|
| Free | $0 | -$1.10 (손해) |
| Basic | $29/월 | $27.90 |
| Pro | $99/월 | $97.90 |
| Enterprise | $499/월 | $497.90 |

**손익분기점**: 50개 Pro 구독 또는 200개 Basic 구독

---

## 10. 트레이드오프

### 장점
- ✅ 중앙 관리로 운영 용이
- ✅ 자동 업데이트 가능
- ✅ 구독 기반 안정적 수익
- ✅ 확장 용이
- ✅ 사용자 데이터 백업/복구 관리

### 단점
- ⚠️ 높은 인프라 비용
- ⚠️ 보안 책임 증가 (사용자 자금)
- ⚠️ 24/7 운영 필요
- ⚠️ 규제 리스크 (금융 서비스)
- ⚠️ 개발 복잡도 증가

---

## 결론

SaaS 전환은 **상당한 투자**가 필요하지만:

1. **안정적 수익 모델** (구독)
2. **중앙 관리**로 지원 비용 감소
3. **빠른 업데이트** 배포
4. **데이터 기반** 개선 가능

**권장 순서:**
1. **Phase 1-2** (7주): MVP SaaS
2. **베타 테스트**: 10-50 사용자
3. **Phase 3-5** (8주): 완성도 높은 제품
4. **정식 출시**

**즉시 시작할 Phase를 선택하세요:**
- Phase 1: Infrastructure
- Phase 2: Trading Engine  
- Phase 3: API/Frontend
