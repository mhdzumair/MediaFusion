#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

CF_TOKEN="$1"

if [ -z "$CF_TOKEN" ]; then
    bashio::log.error "Cloudflare Tunnel token not provided!"
    exit 1
fi

bashio::log.info "Starting Cloudflare Tunnel..."

# Run cloudflared tunnel
exec cloudflared tunnel run \
    --token "${CF_TOKEN}" \
    --url http://localhost:8000 \
    --no-autoupdate \
    --metrics localhost:9090 \
    > /data/logs/cloudflared.log 2>&1
