#!/bin/bash
# test-trading.sh - Paper Trading Test Script
# 실제 트레이딩 전 Paper Trading 테스트용 스크립트

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[TEST]${NC} $1"; }
log_success() { echo -e "${GREEN}[PASS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[FAIL]${NC} $1"; }

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Trading Bot Paper Trading Test${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

# 현재 디렉토리 확인
if [ ! -f "docker-compose.yml" ] && [ ! -f "infra/docker/docker-compose.yml" ]; then
    log_error "Please run from trading-monorepo directory"
    exit 1
fi

COMPOSE_FILE="infra/docker/docker-compose.yml"

echo "Step 1: Configuration Check"
echo "==========================="

# 설정 파일 확인
if [ ! -f "config/active/bot_config.yml" ]; then
    log_error "Bot config not found. Run: python -m trading_core.config_cli init"
    exit 1
fi

# Paper Trading 모드 확인
if grep -q "mode: paper" config/active/bot_config.yml; then
    log_success "Paper Trading mode enabled"
else
    log_warn "Not in Paper Trading mode!"
    echo "Please set mode: paper in config/active/bot_config.yml"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Sandbox 모드 확인
if grep -q "sandbox: true" config/active/bot_config.yml; then
    log_success "Sandbox mode enabled"
else
    log_warn "Sandbox mode not enabled - using real exchange!"
fi

echo ""
echo "Step 2: Settings Review"
echo "======================="

# 설정 표시
echo "Current configuration:"
grep -E "mode:|sandbox:|max_daily_loss|max_positions" config/active/bot_config.yml | head -10

echo ""
echo "Strategies enabled:"
grep -E "name:|type:|symbol:|enabled: true" config/active/bot_config.yml | head -20

echo ""
read -p "Start Paper Trading test? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 0
fi

echo ""
echo "Step 3: Starting Paper Trading"
echo "==============================="

# 컨테이너 중지 (있으면)
docker-compose -f $COMPOSE_FILE down 2>/dev/null || true

# 새로 시작
docker-compose -f $COMPOSE_FILE up -d

log_success "Trading bot started"
echo ""
echo "Monitoring logs for 30 seconds..."
echo "Press Ctrl+C to stop monitoring and continue"
echo ""

# 30초 모니터링
timeout 30 docker-compose -f $COMPOSE_FILE logs -f || true

echo ""
echo "Step 4: Status Check"
echo "===================="

# 상태 확인
sleep 5
CONTAINER_STATUS=$(docker-compose -f $COMPOSE_FILE ps -q | xargs docker inspect -f '{{.State.Status}}' 2>/dev/null || echo "unknown")

if [ "$CONTAINER_STATUS" == "running" ]; then
    log_success "Container is running"
else
    log_error "Container is not running properly"
    docker-compose -f $COMPOSE_FILE logs --tail=50
    exit 1
fi

echo ""
echo "Step 5: Test Monitoring Commands"
echo "================================="

echo ""
echo "Useful commands:"
echo "  View logs:     docker-compose -f $COMPOSE_FILE logs -f"
echo "  Check status:  docker-compose -f $COMPOSE_FILE ps"
echo "  Stop bot:      docker-compose -f $COMPOSE_FILE down"
echo "  View config:   python -m trading_core.config_cli show"
echo ""

echo "Test Duration Recommendation:"
echo "============================="
echo ""
echo "Minimum test periods:"
echo "  - 1 hour:   Basic connectivity check"
echo "  - 1 day:    Strategy signal generation"
echo "  - 1 week:   Performance validation"
echo "  - 2 weeks:  Recommended before live trading"
echo ""

echo "What to check:"
echo "=============="
echo "1. Logs show no errors"
echo "2. WebSocket connections stay open"
echo "3. Strategies generate signals"
echo "4. Orders are placed (and rejected in paper mode)"
echo "5. No memory leaks or crashes"
echo ""

echo "Live Trading Transition:"
echo "========================"
echo "When ready for live trading:"
echo "1. Edit config/active/bot_config.yml"
echo "2. Change mode: paper → live"
echo "3. Set sandbox: true → false"
echo "4. Add real API keys to config/secrets/secrets.yml"
echo "5. Reduce position sizes"
echo "6. Start with small capital"
echo ""

log_success "Paper Trading test setup complete!"
echo ""
echo "Current Status:"
docker-compose -f $COMPOSE_FILE ps
echo ""
echo "Logs:"
echo "  docker-compose -f $COMPOSE_FILE logs -f"
echo ""
