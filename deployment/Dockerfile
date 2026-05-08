# Frontend build stage
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY clients/frontend/package*.json ./
RUN npm ci
COPY clients/frontend/ ./
RUN npm run build

# Rust build stage
FROM rust:1.87-slim-bookworm AS rust-builder
WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends pkg-config libssl-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Cache dependencies by building a dummy project first
COPY services/api/Cargo.toml services/api/Cargo.lock ./services/api/
RUN mkdir -p services/api/src && echo "fn main(){}" > services/api/src/main.rs && \
    echo "// placeholder" > services/api/src/lib.rs && \
    cd services/api && cargo build --release --bin mediafusion-api 2>/dev/null || true && \
    rm -rf services/api/src

# Build the real binary
COPY services/api/ ./services/api/
RUN cd services/api && cargo build --release --bin mediafusion-api

# Python builder stage
FROM python:3.13-slim-bookworm AS builder

WORKDIR /mediafusion

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl build-essential && \
    pip install --upgrade pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create the mediafusion user with a specified home directory
RUN groupadd -r mediafusion && \
    useradd --no-log-init -r -g mediafusion -m -d /home/mediafusion mediafusion

RUN chown -R mediafusion:mediafusion /mediafusion
USER mediafusion

# Set the PATH environment variable to include the local bin directory
ENV PATH="/home/mediafusion/.local/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Install uv and Python dependencies
COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /uvx /bin/
RUN --mount=type=cache,target=/mediafusion/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --compile-bytecode

# Runtime stage
FROM python:3.13-slim-bookworm AS runtime

WORKDIR /mediafusion

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    postgresql-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create the mediafusion user with a specified home directory
RUN groupadd -r mediafusion && \
    useradd --no-log-init -r -g mediafusion -m -d /home/mediafusion mediafusion

# Copy the Rust binary from the rust-builder stage
COPY --from=rust-builder --chown=mediafusion:mediafusion \
    /build/services/api/target/release/mediafusion-api \
    /usr/local/bin/mediafusion-api

# Copy the Python environment and other necessary files from the builder stage
COPY --from=builder --chown=mediafusion:mediafusion /mediafusion/.venv /mediafusion/.venv

ENV PATH="/mediafusion/.venv/bin:$PATH"

COPY --chown=mediafusion:mediafusion . /mediafusion

# Copy the frontend build from the frontend-builder stage
COPY --from=frontend-builder --chown=mediafusion:mediafusion /app/frontend/dist /mediafusion/clients/frontend/dist

ARG VERSION
ENV VERSION=${VERSION}

USER mediafusion

EXPOSE 8000

CMD ["/mediafusion/deployment/startup.sh"]
