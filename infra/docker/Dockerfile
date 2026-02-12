# Multi-stage build for Rust trading server
FROM rustlang/rust:nightly-slim AS builder

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy Cargo files
COPY apps/server/Cargo.toml apps/server/Cargo.toml
COPY apps/server/Cargo.lock apps/server/Cargo.lock
COPY apps/server/src apps/server/src

# Build release binary
WORKDIR /app/apps/server
RUN cargo build --release --locked

# Runtime stage - use testing for newer glibc
FROM debian:testing-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libssl3 \
    ca-certificates \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Create data directory
RUN mkdir -p /app/data

# Copy binary from builder
COPY --from=builder /app/apps/server/target/release/trading-server /app/trading-server

# Copy config
COPY apps/server/config /app/config

# Expose ports
EXPOSE 8080 9000

# Set environment
ENV RUST_LOG=info
ENV RUST_BACKTRACE=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ./trading-server --help || exit 1

# Run master by default
CMD ["./trading-server", "master", "--config-path", "/app/config", "--web-port", "8080", "--worker-port", "9000"]
