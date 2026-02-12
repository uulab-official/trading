#!/bin/bash
# quickstart.sh - Trading Bot Quick Start Script
# Usage: ./quickstart.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Trading Bot Quick Start${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

# Check prerequisites
log_info "Checking prerequisites..."

if ! command -v python3 &> /dev/null; then
    log_error "Python 3 is not installed"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    log_warn "Docker is not installed. Only local setup will be available."
    DOCKER_AVAILABLE=false
else
    DOCKER_AVAILABLE=true
    log_success "Docker found"
fi

if ! command -v docker-compose &> /dev/null; then
    log_warn "Docker Compose is not installed"
    COMPOSE_AVAILABLE=false
else
    COMPOSE_AVAILABLE=true
    log_success "Docker Compose found"
fi

log_success "Python found: $(python3 --version)"

# Check if in correct directory
if [ ! -f "pyproject.toml" ] || [ ! -d "packages" ]; then
    log_error "Please run this script from the trading-monorepo directory"
    exit 1
fi

echo ""
log_info "Step 1: Setting up Python environment..."

# Create virtual environment if not exists
if [ ! -d "venv" ]; then
    python3 -m venv venv
    log_success "Virtual environment created"
else
    log_info "Virtual environment already exists"
fi

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip -q

# Install packages
log_info "Installing packages..."
pip install -e packages/core -q
pip install -e packages/data -q
pip install -e packages/strategy -q
pip install -e apps/trading-bot -q

log_success "Packages installed"

echo ""
log_info "Step 2: Initializing configuration..."

# Initialize config if not exists
if [ ! -f "config/active/bot_config.yml" ]; then
    python -m trading_core.config_cli init
    log_success "Configuration initialized"
else
    log_info "Configuration already exists"
fi

echo ""
log_info "Step 3: Configuration Guide"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""
echo "1. Edit your API keys:"
echo "   nano config/secrets/secrets.yml"
echo ""
echo "2. Edit bot configuration:"
echo "   nano config/active/bot_config.yml"
echo ""
echo "3. Validate configuration:"
echo "   python -m trading_core.config_cli validate"
echo ""

if [ "$DOCKER_AVAILABLE" = true ] && [ "$COMPOSE_AVAILABLE" = true ]; then
    echo "4. Start with Docker (Recommended):"
    echo "   docker-compose -f infra/docker/docker-compose.yml up -d"
    echo ""
    echo "   Or start locally:"
    echo "   python apps/trading-bot/trading_bot/main.py"
else
    echo "4. Start locally:"
    echo "   python apps/trading-bot/trading_bot/main.py"
fi

echo ""
echo "5. View logs:"
if [ "$DOCKER_AVAILABLE" = true ] && [ "$COMPOSE_AVAILABLE" = true ]; then
    echo "   docker-compose -f infra/docker/docker-compose.yml logs -f"
else
    echo "   tail -f logs/trading-bot.log"
fi

echo ""
echo -e "${YELLOW}Important:${NC}"
echo "- Start with PAPER TRADING mode (mode: paper)"
echo "- Use Binance Testnet for initial testing"
echo "- Read PRODUCTION_CHECKLIST.md before going live"
echo ""

# Ask if user wants to edit config now
read -p "Do you want to edit configuration files now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    log_info "Opening secrets.yml..."
    if command -v nano &> /dev/null; then
        nano config/secrets/secrets.yml
    elif command -v vim &> /dev/null; then
        vim config/secrets/secrets.yml
    else
        log_warn "No editor found. Please edit config/secrets/secrets.yml manually"
    fi
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Quick Start Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Documentation:"
echo "  - CUSTOMER_SETUP.md - Detailed setup guide"
echo "  - PRODUCTION_CHECKLIST.md - Before going live"
echo "  - TELEGRAM_SETUP.md - Telegram notifications"
echo ""
