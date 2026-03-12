# Frontend build stage
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

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
ENV PLAYWRIGHT_BROWSERS_PATH="/mediafusion/.cache/ms-playwright"

# Install uv and Python dependencies
COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /uvx /bin/
RUN --mount=type=cache,target=/mediafusion/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --compile-bytecode
RUN uv run patchright install chromium

# Runtime stage
FROM python:3.13-slim-bookworm AS runtime

WORKDIR /mediafusion

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcairo2 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create the mediafusion user with a specified home directory
RUN groupadd -r mediafusion && \
    useradd --no-log-init -r -g mediafusion -m -d /home/mediafusion mediafusion

# Copy the Python environment and other necessary files from the builder stage
COPY --from=builder --chown=mediafusion:mediafusion /mediafusion/.venv /mediafusion/.venv
COPY --from=builder --chown=mediafusion:mediafusion /mediafusion/.cache /mediafusion/.cache

ENV PATH="/mediafusion/.venv/bin:$PATH"
ENV PLAYWRIGHT_BROWSERS_PATH="/mediafusion/.cache/ms-playwright"

COPY --chown=mediafusion:mediafusion . /mediafusion

# Copy the frontend build from the frontend-builder stage
COPY --from=frontend-builder --chown=mediafusion:mediafusion /app/frontend/dist /mediafusion/frontend/dist

ARG VERSION
ENV VERSION=${VERSION}

USER mediafusion

EXPOSE 8000
EXPOSE 9191

CMD ["/mediafusion/deployment/startup.sh"]
