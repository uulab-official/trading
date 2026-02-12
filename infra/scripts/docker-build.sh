#!/bin/bash
set -e

echo "Building Trading Server Docker image..."

cd /Users/bonjin/Documents/workspace/uulab/trading

# Build Docker image
docker build -f infra/docker/Dockerfile -t trading-server:latest .

echo "Build complete!"
echo ""
echo "To run locally:"
echo "  docker-compose -f infra/docker/docker-compose.yml up -d"
echo ""
echo "To view logs:"
echo "  docker-compose -f infra/docker/docker-compose.yml logs -f"
echo ""
echo "To stop:"
echo "  docker-compose -f infra/docker/docker-compose.yml down"
