#!/bin/bash

# Trading Bot Setup Script for NAS/Linux

echo "🚀 Setting up Crypto Trading Bot..."

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p data logs

# Check if .env exists
if [ ! -f apps/trading-bot/.env ]; then
    echo "⚠️  Warning: .env file not found!"
    echo "📝 Creating from .env.example..."
    cp apps/trading-bot/.env.example apps/trading-bot/.env
    echo "⚠️  Please edit apps/trading-bot/.env with your API keys!"
fi

# Build Docker image
echo "🐳 Building Docker image..."
cd infra/docker
docker-compose build

# Check if build succeeded
if [ $? -eq 0 ]; then
    echo "✅ Build successful!"
    echo ""
    echo "🎯 Quick Start:"
    echo "   1. Edit apps/trading-bot/.env with your API keys"
    echo "   2. Review apps/trading-bot/config/bot.yml"
    echo "   3. Run: docker-compose -f infra/docker/docker-compose.yml up -d"
    echo ""
    echo "📊 Monitoring:"
    echo "   Logs: docker logs -f crypto-trading-bot"
    echo "   Stop: docker-compose -f infra/docker/docker-compose.yml down"
else
    echo "❌ Build failed!"
    exit 1
fi
