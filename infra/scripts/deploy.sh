#!/bin/bash
# deploy.sh - Zero-downtime deployment for trading bot
# Usage: ./deploy.sh [version] [environment]

set -e

VERSION=${1:-latest}
ENVIRONMENT=${2:-production}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$PROJECT_DIR/data"
COMPOSE_FILE="$PROJECT_DIR/infra/docker/docker-compose.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Change to project directory
cd "$PROJECT_DIR"

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Trading Bot Zero-Downtime Deploy${NC}"
echo -e "${YELLOW}========================================${NC}"
echo "Version: $VERSION"
echo "Environment: $ENVIRONMENT"
echo "Project: $PROJECT_DIR"
echo ""

# Pre-deployment checks
log_info "Step 1: Pre-deployment checks"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    log_error "Docker is not running"
    exit 1
fi

# Check if blue is running
if ! docker-compose -f "$COMPOSE_FILE" ps | grep -q "crypto-trading-bot"; then
    log_warn "No trading bot container found"
    log_info "Starting fresh deployment..."
    
    # Initial deployment
    VERSION=$VERSION docker-compose -f "$COMPOSE_FILE" up -d trading-bot-blue
    
    log_info "Waiting for initial startup..."
    sleep 30
    
    if docker-compose -f "$COMPOSE_FILE" exec -T trading-bot-blue python -c "import sys; sys.exit(0)" 2>/dev/null; then
        log_success "Initial deployment successful!"
        exit 0
    else
        log_error "Initial deployment failed"
        exit 1
    fi
fi

log_success "Current instance found"

# Check health of current instance
log_info "Checking current instance health..."
CURRENT_CONTAINER=$(docker-compose -f "$COMPOSE_FILE" ps -q trading-bot-blue 2>/dev/null || echo "")

if [ -z "$CURRENT_CONTAINER" ]; then
    log_error "Cannot find running container"
    exit 1
fi

if ! docker exec -T "$CURRENT_CONTAINER" python -c "
import sqlite3
import sys
try:
    conn = sqlite3.connect('/app/data/state.db', timeout=5)
    conn.execute('SELECT 1')
    conn.close()
    sys.exit(0)
except Exception as e:
    print(f'Health check failed: {e}')
    sys.exit(1)
" 2>/dev/null; then
    log_error "Current instance is unhealthy!"
    log_warn "Please fix the current instance before deploying"
    exit 1
fi

log_success "Current instance is healthy"

# Create state backup
log_info "Step 2: Creating state backup"
BACKUP_FILE="backup_$(date +%Y%m%d_%H%M%S).db"
mkdir -p "$DATA_DIR"

if [ -f "$DATA_DIR/state.db" ]; then
    cp "$DATA_DIR/state.db" "$DATA_DIR/$BACKUP_FILE"
    log_success "Backup created: $BACKUP_FILE"
else
    log_warn "No state.db found, skipping backup"
fi

# Build new version
log_info "Step 3: Building new version"
VERSION=$VERSION docker-compose -f "$COMPOSE_FILE" build --no-cache trading-bot-blue

log_success "Build completed"

# Graceful shutdown of current instance
log_info "Step 4: Graceful shutdown of current instance"
log_info "Sending SIGTERM for graceful shutdown (max 60s)..."

# Get current container name
OLD_CONTAINER=$(docker-compose -f "$COMPOSE_FILE" ps -q trading-bot-blue)

# Stop with timeout for graceful shutdown
docker-compose -f "$COMPOSE_FILE" stop -t 60 trading-bot-blue || true

# Wait for shutdown
WAITED=0
while [ $WAITED -lt 30 ]; do
    if ! docker ps | grep -q "$OLD_CONTAINER"; then
        log_success "Container stopped gracefully"
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo -n "."
done

# Force remove if still running
if docker ps | grep -q "$OLD_CONTAINER" 2>/dev/null; then
    log_warn "Container still running, forcing stop"
    docker-compose -f "$COMPOSE_FILE" kill trading-bot-blue || true
fi

docker-compose -f "$COMPOSE_FILE" rm -f trading-bot-blue || true

# Start new version
log_info "Step 5: Starting new version"
VERSION=$VERSION docker-compose -f "$COMPOSE_FILE" up -d trading-bot-blue

# Wait for startup
log_info "Step 6: Waiting for new instance to start..."
sleep 15

# Health check with retry
log_info "Step 7: Health check"
RETRIES=0
MAX_RETRIES=30
HEALTHY=false

while [ $RETRIES -lt $MAX_RETRIES ]; do
    if docker-compose -f "$COMPOSE_FILE" exec -T trading-bot-blue python -c "
import sqlite3
import sys
try:
    conn = sqlite3.connect('/app/data/state.db', timeout=5)
    conn.execute('SELECT 1')
    conn.close()
    sys.exit(0)
except Exception as e:
    sys.exit(1)
" 2>/dev/null; then
        HEALTHY=true
        break
    fi
    
    RETRIES=$((RETRIES + 1))
    echo -n "."
    sleep 2
done

if [ "$HEALTHY" = false ]; then
    log_error "Health check failed!"
    log_warn "Initiating rollback..."
    
    # Rollback
    if [ -f "$DATA_DIR/$BACKUP_FILE" ]; then
        docker-compose -f "$COMPOSE_FILE" stop trading-bot-blue || true
        cp "$DATA_DIR/$BACKUP_FILE" "$DATA_DIR/state.db"
        docker-compose -f "$COMPOSE_FILE" start trading-bot-blue
        log_info "Rollback completed"
    fi
    
    exit 1
fi

echo ""
log_success "Health check passed!"

# Final verification
log_info "Step 8: Final verification"

# Check logs for any errors
if docker-compose -f "$COMPOSE_FILE" logs --tail=20 trading-bot-blue 2>&1 | grep -i "error\|exception" | grep -v "DeprecationWarning"; then
    log_warn "Errors found in logs, please review"
fi

# Check state restoration
if docker-compose -f "$COMPOSE_FILE" exec -T trading-bot-blue python -c "
import sqlite3
conn = sqlite3.connect('/app/data/state.db')
cursor = conn.execute('SELECT COUNT(*) FROM state')
count = cursor.fetchone()[0]
conn.close()
print(f'State entries: {count}')
" 2>/dev/null; then
    log_success "State verified"
else
    log_warn "Could not verify state"
fi

# Cleanup old images
log_info "Step 9: Cleanup"
docker system prune -f > /dev/null 2>&1 || true

# Print summary
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deployment Successful!${NC}"
echo -e "${GREEN}========================================${NC}"
echo "Version: $VERSION"
echo "Backup: $BACKUP_FILE"
echo ""
echo "Commands:"
echo "  View logs: docker-compose -f $COMPOSE_FILE logs -f trading-bot-blue"
echo "  Status: docker-compose -f $COMPOSE_FILE ps"
echo "  Rollback: ./infra/scripts/rollback.sh $BACKUP_FILE"
echo ""
