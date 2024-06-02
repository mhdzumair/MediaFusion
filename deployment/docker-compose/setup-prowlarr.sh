#!/bin/bash

# Ensure the script exits on failure
set -e

# Ensure the script is run from the correct directory
cd "$(dirname "$0")"

# Set the repo root directory
REPO_ROOT="$(pwd)/../.."

# Generate PROWLARR_API_KEY and add to .env file
PROWLARR_API_KEY=$(openssl rand -hex 16)
echo "PROWLARR_API_KEY=$PROWLARR_API_KEY" >> .env

# Stop & delete Prowlarr container if it's running
docker-compose rm -sf prowlarr

# delete the volume if it exists
docker volume rm -f docker-compose_prowlarr-config

# Ensure the volume is available
docker volume create docker-compose_prowlarr-config

# Copy the configuration file to the volume
docker run --rm -v "$REPO_ROOT/resources/xml/prowlarr-config.xml:/prowlarr-config/config.xml" -v docker-compose_prowlarr-config:/config alpine /bin/sh -c "
  cp /prowlarr-config/config.xml /config/config.xml;
  sed -i 's/\$PROWLARR_API_KEY/'"$PROWLARR_API_KEY"'/g' /config/config.xml;
  chmod 664 /config/config.xml;
  echo 'Prowlarr config setup complete.';
"

# pull the latest images
docker-compose pull prowlarr flaresolverr

# Start Prowlarr and FlareSolverr containers
docker-compose up -d prowlarr flaresolverr

# Function to handle curl requests
handle_curl() {
  skip_on_failure=$1
  shift
  response=$(curl -s -o response.txt -w "%{http_code}" "$@")
  if [[ $response -ge 200 && $response -lt 300 ]]; then
    rm response.txt
  else
    echo "Request failed with status code $response"
    cat response.txt
    rm response.txt
    if [[ "$skip_on_failure" != "true" ]]; then
      exit 1
    fi
  fi
}

# Wait for Prowlarr to be ready
echo "Waiting for Prowlarr to be ready..."
until curl -s -o /dev/null -w "%{http_code}" -H "X-API-KEY: $PROWLARR_API_KEY" http://127.0.0.1:9696/api/v1/health | grep -q '^2'; do
  sleep 5
done

# Create tag "flaresolverr"
handle_curl false -X POST -H 'Content-Type: application/json' -H "X-API-KEY: $PROWLARR_API_KEY" --data-raw '{"label":"flaresolverr"}' 'http://127.0.0.1:9696/api/v1/tag'

# Create FlareSolverr proxy using the JSON file
PROXY_DATA=$(cat "$REPO_ROOT/resources/json/prowlarr_indexer_proxy.json")
PROXY_DATA=$(echo "$PROXY_DATA" | sed "s#\\\$FLARESOLVERR_HOST#$FLARESOLVERR_HOST#g")
handle_curl false -X POST -H 'Content-Type: application/json' -H "X-API-KEY: $PROWLARR_API_KEY" --data-raw "$PROXY_DATA" 'http://127.0.0.1:9696/api/v1/indexerProxy?'

# Configure indexers using the JSON file
INDEXERS=$(jq -c '.[]' "$REPO_ROOT/resources/json/prowlarr-indexers.json")
echo "$INDEXERS" | while read -r indexer; do
  indexer_name=$(echo "$indexer" | jq -r '.name')
  echo "Adding indexer named: $indexer_name"

  # Check for cardigannCaptcha field
  if echo "$indexer" | jq -e '.fields[] | select(.name == "cardigannCaptcha")' > /dev/null; then
    echo "Indexer $indexer_name requires captcha"
    handle_curl true -X POST -H "Content-Type: application/json" -H "X-API-KEY: $PROWLARR_API_KEY" -d "$indexer" "http://127.0.0.1:9696/api/v1/indexer"
    handle_curl true -X POST -H "Content-Type: application/json" -H "X-API-KEY: $PROWLARR_API_KEY" --data-raw "$indexer" "http://127.0.0.1:9696/api/v1/indexer/action/checkCaptcha"
  fi

  handle_curl true -X POST -H "Content-Type: application/json" -H "X-API-KEY: $PROWLARR_API_KEY" -d "$indexer" "http://127.0.0.1:9696/api/v1/indexer"
done

echo "Indexers setup complete."
