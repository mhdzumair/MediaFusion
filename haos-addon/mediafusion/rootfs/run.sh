#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

bashio::log.info "Starting MediaFusion Add-on..."

# Load configuration from Home Assistant
CONFIG_PATH=/data/options.json

# Read configuration values
HOST_URL=$(bashio::config 'host_url')
SECRET_KEY=$(bashio::config 'secret_key')
API_PASSWORD=$(bashio::config 'api_password')
ENABLE_VPN=$(bashio::config 'enable_vpn')
VPN_CONFIG=$(bashio::config 'vpn_config')
VPN_FAIL_CLOSED=$(bashio::config 'vpn_fail_closed' 'true')
CF_TUNNEL_ENABLED=$(bashio::config 'cloudflare_tunnel_enabled')
CF_TUNNEL_TOKEN=$(bashio::config 'cloudflare_tunnel_token')
POSTGRES_MAX_CONN=$(bashio::config 'postgres_max_connections' '20')
CACHE_TTL=$(bashio::config 'metadata_cache_ttl' '300')
ENABLE_PROWLARR=$(bashio::config 'enable_prowlarr')
PROWLARR_URL=$(bashio::config 'prowlarr_url')
PROWLARR_API_KEY=$(bashio::config 'prowlarr_api_key')
PREMIUMIZE_CLIENT_ID=$(bashio::config 'premiumize_client_id')
PREMIUMIZE_CLIENT_SECRET=$(bashio::config 'premiumize_client_secret')
LOG_LEVEL=$(bashio::config 'log_level' 'info')

# Validate required configuration
if [ -z "$SECRET_KEY" ]; then
    bashio::log.fatal "SECRET_KEY is required! Generate one with: openssl rand -hex 16"
    exit 1
fi

if [ ${#SECRET_KEY} -lt 32 ]; then
    bashio::log.fatal "SECRET_KEY must be at least 32 characters long!"
    exit 1
fi

# Create data directories
bashio::log.info "Setting up data directories..."
mkdir -p /data/postgres
mkdir -p /data/redis
mkdir -p /data/cache
mkdir -p /data/logs

# Start PostgreSQL
bashio::log.info "Starting PostgreSQL database..."
if [ ! -d "/data/postgres/base" ]; then
    bashio::log.info "Initializing PostgreSQL database..."
    initdb -D /data/postgres -U mediafusion --auth=trust
    echo "host all all 127.0.0.1/32 trust" >> /data/postgres/pg_hba.conf
    echo "listen_addresses = '127.0.0.1'" >> /data/postgres/postgresql.conf
    echo "max_connections = ${POSTGRES_MAX_CONN}" >> /data/postgres/postgresql.conf
    echo "shared_buffers = 128MB" >> /data/postgres/postgresql.conf
    echo "effective_cache_size = 256MB" >> /data/postgres/postgresql.conf
    echo "work_mem = 8MB" >> /data/postgres/postgresql.conf
fi

pg_ctl -D /data/postgres -l /data/logs/postgres.log start
sleep 3

# Create database if it doesn't exist
if ! psql -U mediafusion -lqt | cut -d \| -f 1 | grep -qw mediafusion; then
    bashio::log.info "Creating mediafusion database..."
    createdb -U mediafusion mediafusion
fi

# Start Redis
bashio::log.info "Starting Redis cache..."
redis-server \
    --dir /data/redis \
    --dbfilename dump.rdb \
    --save 300 1 \
    --save 60 100 \
    --maxmemory 256mb \
    --maxmemory-policy allkeys-lru \
    --daemonize yes \
    --logfile /data/logs/redis.log

# Setup VPN if enabled
if [ "$ENABLE_VPN" = "true" ]; then
    bashio::log.info "VPN is enabled, setting up WireGuard..."
    /vpn-setup.sh "$VPN_CONFIG" "$VPN_FAIL_CLOSED"
fi

# Setup Cloudflare Tunnel if enabled
if [ "$CF_TUNNEL_ENABLED" = "true" ]; then
    if [ -z "$CF_TUNNEL_TOKEN" ]; then
        bashio::log.warning "Cloudflare Tunnel enabled but no token provided!"
    else
        bashio::log.info "Starting Cloudflare Tunnel..."
        /cloudflare-setup.sh "$CF_TUNNEL_TOKEN" &
    fi
fi

# Set environment variables for MediaFusion
export HOST_URL="${HOST_URL}"
export SECRET_KEY="${SECRET_KEY}"
export API_PASSWORD="${API_PASSWORD}"
export POSTGRES_URI="postgresql+asyncpg://mediafusion@localhost/mediafusion"
export REDIS_URL="redis://localhost:6379"
export METADATA_CACHE_TTL="${CACHE_TTL}"
export LOGGING_LEVEL="${LOG_LEVEL^^}"
export POSTER_HOST_URL="${HOST_URL}"

# Prowlarr configuration
if [ "$ENABLE_PROWLARR" = "true" ] && [ -n "$PROWLARR_URL" ]; then
    export PROWLARR_URL="${PROWLARR_URL}"
    export PROWLARR_API_KEY="${PROWLARR_API_KEY}"
    bashio::log.info "Prowlarr integration enabled at ${PROWLARR_URL}"
fi

# Premiumize configuration
if [ -n "$PREMIUMIZE_CLIENT_ID" ]; then
    export PREMIUMIZE_OAUTH_CLIENT_ID="${PREMIUMIZE_CLIENT_ID}"
    export PREMIUMIZE_OAUTH_CLIENT_SECRET="${PREMIUMIZE_CLIENT_SECRET}"
    bashio::log.info "Premiumize OAuth configured"
fi

# Additional MediaFusion settings for privacy
export ENABLE_ANALYTICS="False"
export ADULT_CONTENT_FILTER_ENABLED="True"
export IS_PUBLIC_INSTANCE="False"

# Run database migrations
bashio::log.info "Running database migrations..."
cd /app
alembic upgrade head || {
    bashio::log.error "Database migration failed!"
    exit 1
}

# Start Dramatiq worker in background (for async tasks)
bashio::log.info "Starting background task worker..."
dramatiq api.task -p 1 -t 2 > /data/logs/dramatiq.log 2>&1 &

# Wait for services to be ready
sleep 2

# Start MediaFusion API
bashio::log.info "Starting MediaFusion API on port 8000..."
bashio::log.info "Access your MediaFusion instance at: ${HOST_URL}"
bashio::log.info "Stremio manifest URL: ${HOST_URL}/manifest.json"

# Production server with limited workers for resource-constrained systems
exec gunicorn api.main:app \
    -w ${WORKERS} \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --max-requests 300 \
    --max-requests-jitter 50 \
    --access-logfile /data/logs/access.log \
    --error-logfile /data/logs/error.log \
    --log-level "${LOG_LEVEL}"
