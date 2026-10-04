# Multi-stage build for Rust trading server
FROM rust:1.99.0-slim-trixie@sha256:01dd4f9c24801cfc8ba9cf8a5dd6dcca451cd17d1ae73574edc22591de6e6816 AS builder

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
COPY apps/server/tests apps/server/tests

# Test and build the Rust server from the committed dependency lock.
WORKDIR /app/apps/server
RUN cargo test --locked && cargo build --release --locked

# Match the builder's Debian release for glibc/OpenSSL runtime compatibility.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libssl3t64 \
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
