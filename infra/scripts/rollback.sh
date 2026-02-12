#!/bin/bash
# rollback.sh - Emergency rollback script
# Usage: ./rollback.sh [backup_file]

set -e

BACKUP_FILE=${1:-""}
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

echo -e "${RED}========================================${NC}"
echo -e "${RED}     EMERGENCY ROLLBACK${NC}"
echo -e "${RED}========================================${NC}"
echo ""

# Find latest backup if not specified
if [ -z "$BACKUP_FILE" ]; then
    echo -e "${YELLOW}No backup file specified, finding latest...${NC}"
    BACKUP_FILE=$(ls -t "$DATA_DIR"/backup_*.db 2>/dev/null | head -n1)
    
    if [ -z "$BACKUP_FILE" ]; then
        echo -e "${RED}Error: No backup file found in $DATA_DIR${NC}"
        echo "Usage: $0 [backup_file]"
        exit 1
    fi
    
    echo -e "${BLUE}Using latest backup: $(basename $BACKUP_FILE)${NC}"
fi

if [ ! -f "$BACKUP_FILE" ]; then
    # Try relative to data dir
    if [ -f "$DATA_DIR/$BACKUP_FILE" ]; then
        BACKUP_FILE="$DATA_DIR/$BACKUP_FILE"
    else
        echo -e "${RED}Error: Backup file not found: $BACKUP_FILE${NC}"
        exit 1
    fi
fi

echo "Backup file: $BACKUP_FILE"
echo "Data directory: $DATA_DIR"
echo ""

read -p "Are you sure you want to rollback? This will stop the current instance. (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Rollback cancelled"
    exit 0
fi

# Change to project directory
cd "$PROJECT_DIR"

# Step 1: Stop current instance
echo ""
echo -e "${YELLOW}Step 1: Stopping current instance...${NC}"
docker-compose -f "$COMPOSE_FILE" stop -t 30 trading-bot-blue 2>/dev/null || true
docker-compose -f "$COMPOSE_FILE" kill trading-bot-blue 2>/dev/null || true
docker-compose -f "$COMPOSE_FILE" rm -f trading-bot-blue 2>/dev/null || true
echo -e "${GREEN}✓ Instance stopped${NC}"

# Step 2: Restore state
echo ""
echo -e "${YELLOW}Step 2: Restoring state from backup...${NC}"

# Create current state backup before rollback
if [ -f "$DATA_DIR/state.db" ]; then
    CURRENT_BACKUP="current_before_rollback_$(date +%Y%m%d_%H%M%S).db"
    cp "$DATA_DIR/state.db" "$DATA_DIR/$CURRENT_BACKUP"
    echo -e "${BLUE}Current state backed up to: $CURRENT_BACKUP${NC}"
fi

# Restore backup
cp "$BACKUP_FILE" "$DATA_DIR/state.db"
echo -e "${GREEN}✓ State restored from backup${NC}"

# Step 3: Start instance
echo ""
echo -e "${YELLOW}Step 3: Starting instance...${NC}"
docker-compose -f "$COMPOSE_FILE" up -d trading-bot-blue

# Step 4: Wait for startup
echo ""
echo -e "${YELLOW}Step 4: Waiting for startup...${NC}"
sleep 15

# Step 5: Health check
echo ""
echo -e "${YELLOW}Step 5: Health check...${NC}"
RETRIES=0
MAX_RETRIES=20

while [ $RETRIES -lt $MAX_RETRIES ]; do
    if docker-compose -f "$COMPOSE_FILE" exec -T trading-bot-blue python -c "
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
        echo ""
        echo -e "${GREEN}========================================${NC}"
        echo -e "${GREEN}     Rollback Successful!${NC}"
        echo -e "${GREEN}========================================${NC}"
        echo ""
        echo "Restored from: $BACKUP_FILE"
        echo "Current state: $DATA_DIR/state.db"
        echo ""
        echo "Commands:"
        echo "  View logs: docker-compose -f $COMPOSE_FILE logs -f trading-bot-blue"
        echo "  Status: docker-compose -f $COMPOSE_FILE ps"
        echo ""
        exit 0
    fi
    
    RETRIES=$((RETRIES + 1))
    echo -n "."
    sleep 2
done

echo ""
echo -e "${RED}========================================${NC}"
echo -e "${RED}     Rollback Failed!${NC}"
echo -e "${RED}========================================${NC}"
echo ""
echo -e "${YELLOW}The rollback did not complete successfully.${NC}"
echo -e "${YELLOW}Please check the logs and state manually.${NC}"
echo ""
echo "Commands:"
echo "  View logs: docker-compose -f $COMPOSE_FILE logs trading-bot-blue"
echo "  Check state: ls -la $DATA_DIR/"
echo ""
exit 1
