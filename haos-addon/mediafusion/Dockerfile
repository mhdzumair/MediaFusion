ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20
FROM $BUILD_FROM

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install system dependencies
RUN apk add --no-cache \
    python3=~3.12 \
    py3-pip \
    postgresql16-client \
    redis \
    wireguard-tools \
    iptables \
    ip6tables \
    cloudflared \
    curl \
    bash \
    tzdata \
    ca-certificates \
    libpq \
    libxml2 \
    libxslt \
    libffi \
    openssl \
    && ln -sf /usr/bin/python3 /usr/bin/python

# Install uv for fast Python package management
RUN pip3 install --no-cache-dir uv==0.5.11

# Set working directory
WORKDIR /app

# Copy Python dependencies
COPY pyproject.toml ./

# Install Python dependencies using uv (much faster than pip)
RUN uv pip install --system --no-cache \
    fastapi==0.115.12 \
    uvicorn[standard]==0.34.0 \
    gunicorn==23.0.0 \
    sqlmodel==0.0.27 \
    alembic==1.17.2 \
    asyncpg==0.31.0 \
    redis[hiredis]==5.2.1 \
    dramatiq[redis]==1.18.0 \
    pydantic==2.10.5 \
    pydantic-settings==2.7.1 \
    httpx==0.28.1 \
    requests==2.32.3 \
    python-dateutil==2.9.0.post0 \
    pytz==2024.2 \
    pycryptodome==3.21.0 \
    bencodepy==0.9.5 \
    beautifulsoup4==4.12.3 \
    lxml==5.3.0 \
    thefuzz==0.22.1 \
    tenacity==9.0.0 \
    ratelimit==2.2.1 \
    diskcache==5.6.3 \
    psutil==6.1.1 \
    && rm -rf /root/.cache

# Copy application code
COPY api/ ./api/
COPY db/ ./db/
COPY mediafusion_scrapy/ ./mediafusion_scrapy/
COPY scrapers/ ./scrapers/
COPY streaming_providers/ ./streaming_providers/
COPY utils/ ./utils/
COPY resources/ ./resources/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Copy add-on scripts
COPY haos-addon/mediafusion/rootfs /

# Make scripts executable
RUN chmod +x /run.sh /healthcheck.sh /vpn-setup.sh /cloudflare-setup.sh

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    WORKERS=2

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD /healthcheck.sh

# Expose port
EXPOSE 8000

# Run script
CMD ["/run.sh"]
