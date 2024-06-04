#!/bin/sh

# Ensure the script exits on failure
set -e

# Function to handle curl requests
handle_curl() {
  skip_on_failure=$1
  shift
  status_code=$(curl -s -o response.txt -w "%{http_code}" "$@")
  if [ "$status_code" -ge 200 ] && [ "$status_code" -lt 300 ]; then
    rm -f response.txt
  else
    echo "Request failed with status code $status_code"
    if [ -f response.txt ]; then
      cat response.txt
      rm -f response.txt
    fi
    if [ "$skip_on_failure" != "true" ]; then
      exit 1
    fi
  fi
}

# Function to retry curl requests until success with exponential backoff
retry_curl() {
  url=$1
  output_file=$2
  shift 2
  retries=0
  while true; do
    status_code=$(curl -s -o "$output_file" -w "%{http_code}" "$url" "$@")
    if [ "$status_code" -ge 200 ] && [ "$status_code" -lt 300 ]; then
      echo "Request successful"
      break
    fi
    echo "Request failed with status code $status_code. Retrying in $((2**retries)) seconds..."
    sleep $((2**retries))
    retries=$((retries + 1))
  done
}

# Wait for Prowlarr to be ready
echo "Waiting for Prowlarr to be ready..."
until [ "$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-KEY: $PROWLARR_API_KEY" http://localhost:9696/api/v1/health)" -eq 200 ]; do
  echo "Prowlarr is not ready yet. Retrying in 5 seconds..."
  sleep 5
done

# Create tag "flaresolverr"
handle_curl false -X POST -H 'Content-Type: application/json' -H "X-API-KEY: $PROWLARR_API_KEY" --data-raw '{"label":"flaresolverr"}' 'http://localhost:9696/api/v1/tag'

# Create FlareSolverr proxy using the JSON file
retry_curl https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/json/prowlarr_indexer_proxy.json /config/prowlarr_indexer_proxy.json
PROXY_DATA=$(cat /config/prowlarr_indexer_proxy.json)
PROXY_DATA=$(echo "$PROXY_DATA" | sed "s#\\\$FLARESOLVERR_HOST#$FLARESOLVERR_HOST#g")
handle_curl false -X POST -H 'Content-Type: application/json' -H "X-API-KEY: $PROWLARR_API_KEY" --data-raw "$PROXY_DATA" 'http://localhost:9696/api/v1/indexerProxy?'

# Configure indexers using the JSON file
retry_curl https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/json/prowlarr-indexers.json /config/prowlarr-indexers.json
INDEXERS=$(jq -c '.[]' /config/prowlarr-indexers.json)
echo "$INDEXERS" | while read -r indexer; do
  indexer_name=$(echo "$indexer" | jq -r '.name')
  echo "Adding indexer named: $indexer_name"

  # Check for cardigannCaptcha field
  if echo "$indexer" | jq -e '.fields[] | select(.name == "cardigannCaptcha")' > /dev/null; then
    echo "Indexer $indexer_name requires captcha"
    handle_curl false -X POST -H "Content-Type: application/json" -H "X-API-KEY: $PROWLARR_API_KEY" --data-raw "$indexer" "http://localhost:9696/api/v1/indexer/action/checkCaptcha"
  fi

  handle_curl true -X POST -H "Content-Type: application/json" -H "X-API-KEY: $PROWLARR_API_KEY" -d "$indexer" "http://localhost:9696/api/v1/indexer"
done

echo "Indexers setup complete."
