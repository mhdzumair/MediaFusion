# Ensure the script exits on failure
$ErrorActionPreference = "Stop"

# Ensure the script is run from the correct directory
Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Definition)

# Set the repo root directory
$REPO_ROOT = (Get-Location).Path + "\..\.."

# Generate PROWLARR_API_KEY and add to .env file
$PROWLARR_API_KEY = [guid]::NewGuid().ToString("N")
Add-Content -Path .env -Value "PROWLARR_API_KEY=$PROWLARR_API_KEY"

# Stop & delete Prowlarr container if it's running
docker-compose rm -sf prowlarr

# delete the existing volume if it exists
docker volume rm -f docker-compose_prowlarr-config

# Ensure the volume is available
docker volume create docker-compose_prowlarr-config

# Copy the configuration file to the volume
docker run --rm -v "$REPO_ROOT/resources/xml/prowlarr-config.xml:/prowlarr-config/config.xml" -v docker-compose_prowlarr-config:/config alpine /bin/sh -c "
  cp /prowlarr-config/config.xml /config/config.xml;
  sed -i 's/\$PROWLARR_API_KEY/'"$Env:PROWLARR_API_KEY"'/g' /config/config.xml;
  chmod 664 /config/config.xml;
  echo 'Prowlarr config setup complete.';
"

# pull the latest images
docker-compose pull prowlarr flaresolverr

# Start Prowlarr and FlareSolverr containers
docker-compose up -d prowlarr flaresolverr

# Function to handle curl requests
function Invoke-Curl {
    param (
        [string]$Uri,
        [string]$Method = "GET",
        [string]$ContentType = "application/json",
        [string]$ApiKey,
        [string]$Body,
        [bool]$SkipOnFailure = $false
    )

    $headers = @{
        "Content-Type" = $ContentType
        "X-API-KEY" = $ApiKey
    }

    try {
        $response = Invoke-RestMethod -Uri $Uri -Method $Method -Headers $headers -Body $Body
        return $response
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        Write-Output "Request to $Uri failed with status code $statusCode"
        Write-Output $_.Exception.Response.Content
        if (-not $SkipOnFailure) {
            exit 1
        }
    }
}

# Wait for Prowlarr to be ready
Write-Output "Waiting for Prowlarr to be ready..."
while (-not (Invoke-RestMethod -Uri "http://127.0.0.1:9696/api/v1/health" -Headers @{ "X-API-KEY" = $Env:PROWLARR_API_KEY } -ErrorAction SilentlyContinue)) {
    Start-Sleep -Seconds 5
}

# Create tag "flaresolverr"
Invoke-Curl -Uri 'http://127.0.0.1:9696/api/v1/tag' -Method "POST" -ApiKey $Env:PROWLARR_API_KEY -Body '{"label":"flaresolverr"}'

# Create FlareSolverr proxy using the JSON file
$proxyData = Get-Content -Raw -Path "$REPO_ROOT/resources/json/prowlarr_indexer_proxy.json"
$proxyData = $proxyData -replace '\$FLARESOLVERR_HOST', $Env:FLARESOLVERR_HOST
Invoke-Curl -Uri 'http://127.0.0.1:9696/api/v1/indexerProxy?' -Method "POST" -ApiKey $Env:PROWLARR_API_KEY -Body $proxyData

# Configure indexers using the JSON file
$indexers = Get-Content -Raw -Path "$REPO_ROOT/resources/json/prowlarr-indexers.json" | ConvertFrom-Json
foreach ($indexer in $indexers) {
    $indexerName = $indexer.name
    Write-Output "Adding indexer named: $indexerName"

    # Check for cardigannCaptcha field
    if ($indexer.fields | Where-Object { $_.name -eq "cardigannCaptcha" }) {
        Write-Output "Indexer $indexerName requires captcha."
        Invoke-Curl -Uri "http://127.0.0.1:9696/api/v1/indexer" -Method "POST" -ApiKey $Env:PROWLARR_API_KEY -Body ($indexer | ConvertTo-Json) -SkipOnFailure $true
        Invoke-Curl -Uri "http://127.0.0.1:9696/api/v1/indexer/action/checkCaptcha" -Method "POST" -ApiKey $Env:PROWLARR_API_KEY -Body ($indexer | ConvertTo-Json) -SkipOnFailure $true
    }

    Invoke-Curl -Uri "http://127.0.0.1:9696/api/v1/indexer" -Method "POST" -ApiKey $Env:PROWLARR_API_KEY -Body ($indexer | ConvertTo-Json) -SkipOnFailure $true
}

Write-Output "Indexers setup complete."
