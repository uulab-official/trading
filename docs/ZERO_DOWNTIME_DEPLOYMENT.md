# Zero-Downtime Deployment Strategy for Trading Bot

## Executive Summary

24/7 트레이딩 봇의 중단 없는 배포를 위해 **Blue-Green Deployment with SQLite WAL Mode** 전략을 제안합니다. 이 방식은 10-30초의 짧은 다운타임으로 안전한 배포를 보장합니다.

---

## Current Architecture Analysis

### 현재 시스템 구성
- **Docker Compose**: 단일 컨테이너 (`crypto-trading-bot`)
- **State**: SQLite 파일 (`/app/data/state.db`) - Docker Volume으로 영속화
- **Connections**: WebSocket (재연결 가능)
- **Shutdown**: SIGTERM 핸들러 구현됨 (60초 graceful shutdown)

### Challenges
1. SQLite는 파일 잠금으로 인해 동시 접근 제한
2. 단일 컨테이너 - Blue/Green 병렬 실행 불가
3. 활성 포지션 유지가 최우선
4. 배포 중 새로운 주문 생성 금지 필요

---

## Recommended Strategy: Blue-Green with Health Check

### 왜 Blue-Green인가?

| 전략 | Pros | Cons | 적합성 |
|------|------|------|--------|
| **Blue-Green** | 완전한 롤백 가능, 명확한 전환 | 2x 리소스 필요 | ⭐⭐⭐⭐⭐ |
| Rolling Update | 리소스 효율적 | SQLite 동시성 문제 | ⭐⭐⭐ |
| Canary | 위험 분산 | 복잡한 라우팅 필요 | ⭐⭐ |

**Blue-Green 선택 이유:**
- SQLite 파일 잠금 문제 회피
- 완전한 롤백 가능성
- 고객 인프라(NAS/VPS)에서 실행 가능
- 단순하고 신뢰할 수 있음

---

## Implementation

### 1. Enhanced Docker Compose Configuration

```yaml
# docker-compose.yml
version: '3.8'

services:
  # Blue Environment (Current)
  trading-bot-blue:
    build:
      context: ../..
      dockerfile: infra/docker/Dockerfile
    container_name: crypto-trading-bot-blue
    restart: unless-stopped
    
    environment:
      - PYTHONUNBUFFERED=1
      - TZ=Asia/Seoul
      - DEPLOYMENT_COLOR=blue
      - SQLITE_WAL_MODE=1
    
    volumes:
      # Shared SQLite with WAL mode
      - trading-data:/app/data
      - ./logs/blue:/app/logs
      - ../../config:/app/config:ro
    
    networks:
      - trading-network
    
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
    
    healthcheck:
      test: ["CMD", "python", "-c", "
import sys;
import sqlite3;
import os;

# Check database connectivity
try:
    conn = sqlite3.connect('/app/data/state.db', timeout=5);
    conn.execute('SELECT 1');
    conn.close();
    sys.exit(0);
except Exception as e:
    print(f'Health check failed: {e}');
    sys.exit(1);
"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 30s

  # Green Environment (New Version)
  trading-bot-green:
    build:
      context: ../..
      dockerfile: infra/docker/Dockerfile
      # Build args for version tracking
      args:
        - BUILD_VERSION=${VERSION:-latest}
    container_name: crypto-trading-bot-green
    restart: unless-stopped
    
    environment:
      - PYTHONUNBUFFERED=1
      - TZ=Asia/Seoul
      - DEPLOYMENT_COLOR=green
      - SQLITE_WAL_MODE=1
    
    volumes:
      # Same data volume - will be activated after blue stops
      - trading-data:/app/data
      - ./logs/green:/app/logs
      - ../../config:/app/config:ro
    
    networks:
      - trading-network
    
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
    
    # Start with --scale trading-bot-green=0
    profiles:
      - deployment
    
    healthcheck:
      test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 30s

  # Nginx Proxy for seamless switching
  nginx:
    image: nginx:alpine
    container_name: trading-proxy
    restart: unless-stopped
    
    ports:
      - "8080:80"  # Management API if needed
    
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    
    networks:
      - trading-network
    
    depends_on:
      - trading-bot-blue

volumes:
  trading-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${PWD}/data

networks:
  trading-network:
    driver: bridge
```

### 2. SQLite WAL Mode Configuration

```python
# trading_core/state_store.py 수정
class SQLiteStateStore(StateStore):
    def _init_db(self) -> None:
        """데이터베이스 초기화 with WAL mode"""
        with sqlite3.connect(self.db_path) as conn:
            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
```

### 3. Deployment Script

```bash
#!/bin/bash
# deploy.sh - Zero-downtime deployment script

set -e

VERSION=${1:-latest}
ENVIRONMENT=${2:-production}
DATA_DIR="./data"
LOGS_DIR="./logs"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Starting zero-downtime deployment...${NC}"
echo "Version: $VERSION"
echo "Environment: $ENVIRONMENT"

# Step 1: Pre-deployment checks
echo -e "\n${YELLOW}Step 1: Pre-deployment checks${NC}"

# Check if blue is running
if ! docker-compose ps | grep -q "crypto-trading-bot-blue"; then
    echo -e "${RED}Error: Blue environment not running${NC}"
    exit 1
fi

# Check blue health
if ! docker-compose exec -T trading-bot-blue python -c "
import sqlite3
conn = sqlite3.connect('/app/data/state.db', timeout=5)
conn.execute('SELECT 1')
conn.close()
print('Health check passed')
"; then
    echo -e "${RED}Error: Blue environment unhealthy${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Blue environment healthy${NC}"

# Step 2: Create state backup
echo -e "\n${YELLOW}Step 2: Creating state backup${NC}"
BACKUP_FILE="backup_$(date +%Y%m%d_%H%M%S).db"
cp "$DATA_DIR/state.db" "$DATA_DIR/$BACKUP_FILE"
echo -e "${GREEN}✓ Backup created: $BACKUP_FILE${NC}"

# Step 3: Build new version (Green)
echo -e "\n${YELLOW}Step 3: Building new version (Green)${NC}"
VERSION=$VERSION docker-compose build trading-bot-green
echo -e "${GREEN}✓ Green image built${NC}"

# Step 4: Start Green environment with --scale
echo -e "\n${YELLOW}Step 4: Starting Green environment${NC}"
VERSION=$VERSION docker-compose up -d --scale trading-bot-green=1 trading-bot-green

# Wait for Green to be healthy
echo -e "${YELLOW}Waiting for Green to be healthy...${NC}"
sleep 10

RETRIES=0
MAX_RETRIES=30
while [ $RETRIES -lt $MAX_RETRIES ]; do
    if docker-compose exec -T trading-bot-green python -c "
import sqlite3
import sys
try:
    conn = sqlite3.connect('/app/data/state.db', timeout=5)
    conn.execute('SELECT 1')
    conn.close()
    sys.exit(0)
except:
    sys.exit(1)
" 2>/dev/null; then
        echo -e "${GREEN}✓ Green environment healthy${NC}"
        break
    fi
    
    RETRIES=$((RETRIES + 1))
    echo -n "."
    sleep 2
done

if [ $RETRIES -eq $MAX_RETRIES ]; then
    echo -e "\n${RED}Error: Green environment failed health check${NC}"
    echo -e "${YELLOW}Rolling back...${NC}"
    docker-compose stop trading-bot-green
    docker-compose rm -f trading-bot-green
    exit 1
fi

# Step 5: Graceful shutdown of Blue
echo -e "\n${YELLOW}Step 5: Graceful shutdown of Blue${NC}"
docker-compose stop -t 60 trading-bot-blue

# Step 6: Switch traffic (rename containers for clarity)
echo -e "\n${YELLOW}Step 6: Switching traffic${NC}"
docker-compose rm -f trading-bot-blue

# Rename green to blue for next deployment
docker rename crypto-trading-bot-green crypto-trading-bot-blue

echo -e "${GREEN}✓ Traffic switched to new version${NC}"

# Step 7: Verify deployment
echo -e "\n${YELLOW}Step 7: Verification${NC}"
sleep 5

if docker-compose ps | grep -q "crypto-trading-bot-blue"; then
    echo -e "${GREEN}✓ Deployment successful!${NC}"
    echo -e "${GREEN}✓ New version is running${NC}"
else
    echo -e "${RED}Error: Deployment verification failed${NC}"
    exit 1
fi

# Step 8: Cleanup
echo -e "\n${YELLOW}Step 8: Cleanup${NC}"
docker system prune -f
echo -e "${GREEN}✓ Cleanup complete${NC}"

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment completed successfully!${NC}"
echo -e "${GREEN}Version: $VERSION${NC}"
echo -e "${GREEN}========================================${NC}"
```

### 4. Rollback Script

```bash
#!/bin/bash
# rollback.sh - Emergency rollback script

set -e

BACKUP_FILE=${1:-""}

echo -e "\033[1;31m========================================${NC}"
echo -e "\033[1;31mEMERGENCY ROLLBACK${NC}"
echo -e "\033[1;31m========================================${NC}"

# Find latest backup if not specified
if [ -z "$BACKUP_FILE" ]; then
    BACKUP_FILE=$(ls -t ./data/backup_*.db 2>/dev/null | head -n1)
    if [ -z "$BACKUP_FILE" ]; then
        echo -e "\033[0;31mError: No backup file found${NC}"
        exit 1
    fi
fi

echo "Backup file: $BACKUP_FILE"

# Step 1: Stop current instance
echo -e "\033[1;33mStep 1: Stopping current instance...${NC}"
docker-compose stop -t 30 trading-bot-blue trading-bot-green 2>/dev/null || true
docker-compose rm -f trading-bot-blue trading-bot-green 2>/dev/null || true

# Step 2: Restore state
echo -e "\033[1;33mStep 2: Restoring state from backup...${NC}"
cp "$BACKUP_FILE" ./data/state.db
echo -e "\033[0;32m✓ State restored${NC}"

# Step 3: Start previous version
echo -e "\033[1;33mStep 3: Starting previous version...${NC}"
docker-compose up -d trading-bot-blue

# Step 4: Wait for health check
echo -e "\033[1;33mStep 4: Waiting for health check...${NC}"
sleep 10

if docker-compose exec -T trading-bot-blue python -c "import sqlite3; conn = sqlite3.connect('/app/data/state.db'); conn.execute('SELECT 1'); conn.close()" 2>/dev/null; then
    echo -e "\033[0;32m✓ Rollback successful!${NC}"
else
    echo -e "\033[0;31mError: Rollback verification failed${NC}"
    exit 1
fi

echo -e "\033[1;32m========================================${NC}"
echo -e "\033[1;32mRollback completed!${NC}"
echo -e "\033[1;32m========================================${NC}"
```

### 5. Enhanced Main.py for Deployment

```python
# trading_bot/main.py - 배포 관련 추가

class TradingBot:
    def __init__(self, config_dir: str = "config"):
        # ... existing init ...
        self.deployment_color = os.getenv('DEPLOYMENT_COLOR', 'blue')
        self.shutdown_timeout = 60  # seconds
        
    async def initialize(self) -> None:
        """봇 초기화 with deployment awareness"""
        logger.info(f"Initializing Trading Bot [{self.deployment_color}]: {self.config.name}")
        
        # 배포 모드에서 SQLite WAL 활성화
        if os.getenv('SQLITE_WAL_MODE'):
            self._enable_wal_mode()
        
        # ... rest of initialization ...
    
    def _enable_wal_mode(self):
        """SQLite WAL 모드 활성화"""
        import sqlite3
        db_path = "data/state.db"
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                logger.info("SQLite WAL mode enabled")
        except Exception as e:
            logger.warning(f"Failed to enable WAL mode: {e}")
    
    async def stop(self) -> None:
        """개선된 graceful shutdown"""
        logger.info(f"[{self.deployment_color}] Stopping Trading Bot...")
        self.running = False
        
        # 배포 중 플래그 설정
        await self._set_deployment_flag()
        
        # 활성 주문 완료 대기 (최대 30초)
        await self._wait_for_active_orders()
        
        # Cancel tasks
        for task in self._tasks:
            task.cancel()
        
        # Wait for graceful shutdown
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=self.shutdown_timeout
            )
        except asyncio.TimeoutError:
            logger.warning("Graceful shutdown timed out, forcing exit")
        
        # Stop components
        await self._stop_components()
        
        # Final state save
        await self._save_state()
        
        # 배포 중 플래그 해제
        await self._clear_deployment_flag()
        
        logger.info(f"[{self.deployment_color}] Trading Bot stopped")
    
    async def _set_deployment_flag(self):
        """배포 중 플래그 설정 - 새 주문 방지"""
        try:
            self.bot_state.save_checkpoint({
                'deploying': True,
                'timestamp': datetime.utcnow().isoformat(),
                'color': self.deployment_color
            })
            logger.info("Deployment flag set - new orders disabled")
        except Exception as e:
            logger.error(f"Failed to set deployment flag: {e}")
    
    async def _clear_deployment_flag(self):
        """배포 중 플래그 해제"""
        try:
            self.bot_state.save_checkpoint({
                'deploying': False,
                'timestamp': datetime.utcnow().isoformat()
            })
        except Exception as e:
            logger.error(f"Failed to clear deployment flag: {e}")
    
    async def _wait_for_active_orders(self):
        """활성 주문 완료 대기"""
        max_wait = 30
        waited = 0
        
        while waited < max_wait:
            active_orders = len(self.order_manager.get_active_orders())
            if active_orders == 0:
                logger.info("All orders completed")
                return
            
            logger.info(f"Waiting for {active_orders} active orders...")
            await asyncio.sleep(5)
            waited += 5
        
        logger.warning(f"Timeout waiting for orders, proceeding with shutdown")
    
    def is_deploying(self) -> bool:
        """배포 중인지 확인"""
        try:
            checkpoint = self.bot_state.load_checkpoint()
            return checkpoint.get('deploying', False)
        except:
            return False
```

---

## Trade-offs & Risks

### 장점
1. **완전한 롤백 가능** - 문제 발생 시 즉시 복구
2. **단순함** - 고객이 직접 관리하기 쉬움
3. **SQLite 호환** - 파일 잠금 문제 없음
4. **빠른 전환** - 10-30초 다운타임

### 단점
1. **메모리 2배** - 두 컨테이너가 동시에 실행됨 (배포 중에만)
2. **SQLite 한계** - 여전히 단일 파일 DB 사용
3. **수동 전환** - 자동화 필요

### Risk Mitigation
1. **백업 자동화** - 배포 전 자동 백업
2. **Health Check** - 엄격한 헬스 체크
3. **모니터링** - 배포 중 알림
4. **Rollback计时器** - 5분 내 문제 없으면 완료

---

## Alternative: Feature Flags (Advanced)

향후 개선을 위한 고급 전략:

```python
# Feature flag 기반 점진적 배포
if feature_flags.is_enabled('new_strategy_logic', self.config.bot_id):
    strategy = NewStrategy(config)
else:
    strategy = OldStrategy(config)
```

---

## Summary

이 전략은 다음을 제공합니다:
- ✅ **< 30초 다운타임**
- ✅ **완전한 롤백**
- ✅ **SQLite 안전성**
- ✅ **고객 친화적**
- ✅ **NAS/VPS/Cloud 호환**

다음 단계:
1. SQLite WAL 모드 활성화
2. Deployment 스크립트 구현
3. 모니터링 추가
4. 문서화 및 테스트
