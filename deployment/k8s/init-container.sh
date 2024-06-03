#!/bin/sh

# Ensure the script exits on failure
set -e

# Wait for the config file to be downloaded and configured
until curl -o /config/config.xml https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/xml/prowlarr-config.xml; do
  echo "Failed to download config file. Retrying..."
  sleep 3
done

# Replace placeholder with actual API key
sed -i 's/\$PROWLARR_API_KEY/'"$PROWLARR_API_KEY"'/g' /config/config.xml
chmod 664 /config/config.xml
echo "Prowlarr config setup complete."

# Check FlareSolverr health
echo "Waiting for FlareSolverr to be ready..."
until curl -s -o /dev/null -w "%{http_code}" "$FLARESOLVERR_HOST/health" | grep -q '^2'; do
  echo "FlareSolverr is not ready. Retrying..."
  sleep 5
done