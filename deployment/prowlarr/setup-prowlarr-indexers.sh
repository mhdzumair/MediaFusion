#!/bin/bash

# Ensure the script exits on failure
set -e

# Ensure the script is run from the correct directory
cd "$(dirname "$0")"

# Set the repo root directory
REPO_ROOT="$(pwd)/../.."

# Load the environment variables
source "$REPO_ROOT/.env"

# Ensure the required environment variables are set
if [[ -z "$PROWLARR_API_KEY" ]]; then
  echo "PROWLARR_API_KEY is not set. Please set it in the .env file."
  exit 1
fi

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
handle_curl true -X POST -H 'Content-Type: application/json' -H "X-API-KEY: $PROWLARR_API_KEY" --data-raw '{"label":"flaresolverr"}' 'http://127.0.0.1:9696/api/v1/tag'

# Create FlareSolverr proxy using the JSON file
PROXY_DATA=$(cat "$REPO_ROOT/resources/json/prowlarr_indexer_proxy.json")
PROXY_DATA=$(echo "$PROXY_DATA" | sed "s#\\\$FLARESOLVERR_HOST#$FLARESOLVERR_HOST#g")
handle_curl true -X POST -H 'Content-Type: application/json' -H "X-API-KEY: $PROWLARR_API_KEY" --data-raw "$PROXY_DATA" 'http://127.0.0.1:9696/api/v1/indexerProxy?'

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
