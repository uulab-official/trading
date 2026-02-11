# Multi-stage build for production
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim as production

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Create directories
RUN mkdir -p /app/data /app/logs

# Copy source code
COPY packages/ /app/packages/
COPY apps/trading-bot/ /app/apps/trading-bot/

# Install packages in development mode
RUN pip install --no-cache-dir \
    -e /app/packages/core \
    -e /app/packages/data \
    -e /app/packages/strategy \
    -e /app/apps/trading-bot

# Set environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run the bot
WORKDIR /app/apps/trading-bot
CMD ["python", "-m", "trading_bot.main"]
