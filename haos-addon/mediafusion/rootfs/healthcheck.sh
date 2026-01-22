#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

# Check if MediaFusion API is responding
if curl -f -s http://localhost:8000/health > /dev/null 2>&1; then
    exit 0
else
    bashio::log.error "Health check failed - MediaFusion API not responding"
    exit 1
fi
