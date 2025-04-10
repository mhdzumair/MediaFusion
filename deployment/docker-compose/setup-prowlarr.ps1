# Ensure the script exits on failure for critical operations
$ErrorActionPreference = "Stop"

# Set the repo root directory
$REPO_ROOT = Resolve-Path (Join-Path $PSScriptRoot "..\..")

# Function to handle REST API requests with optional error suppression
function Invoke-ProwlarrApi {
    param (
        [string]$Uri,
        [string]$Method = "GET",
        [string]$Body,
        [switch]$SuppressError
    )

    $headers = @{
        "Content-Type" = "application/json"
        "X-API-KEY" = $env:PROWLARR_API_KEY
    }

    try {
        $params = @{
            Uri = $Uri
            Method = $Method
            Headers = $headers
            ContentType = "application/json"
        }
        if ($Body) { $params.Body = $Body }

        $response = Invoke-RestMethod @params
        return $response
    } catch {
        $errorMessage = "API request failed: $($_.Exception.Message)"
        if ($_.Exception.Response) {
            $errorMessage += "Status code: $($_.Exception.Response.StatusCode)"
            $errorMessage += "Response content: $($_.ErrorDetails.Message)"
        }

        if ($SuppressError) {
            Write-Warning $errorMessage
            return $null
        } else {
            Write-Error $errorMessage
            throw
        }
    }
}

# Function to wait for Prowlarr to be ready
function Wait-ProwlarrReady {
    $maxAttempts = 60
    $attempt = 0
    $url = "http://localhost:9696/api/v1/health"

    Write-Output "Waiting for Prowlarr to be ready..."
    while ($attempt -lt $maxAttempts) {
        try {
            $response = Invoke-ProwlarrApi -Uri $url
            if ($response) {
                Write-Output "Prowlarr is ready!"
                return
            }
        } catch {
            $attempt++
            Write-Output ("Attempt {0} of {1}: Prowlarr is not ready. Retrying in 5 seconds..." -f $attempt, $maxAttempts)
            Start-Sleep -Seconds 5
        }
    }
    throw "Prowlarr did not become ready in time."
}

# Main script execution
try {
    # Generate PROWLARR_API_KEY and add to .env file
    $env:PROWLARR_API_KEY = [guid]::NewGuid().ToString("N")
    Add-Content -Path .env -Value "PROWLARR_API_KEY=$env:PROWLARR_API_KEY"

    # Docker operations
    docker compose rm -sf prowlarr
    docker volume rm -f docker-compose_prowlarr-config
    docker volume create docker-compose_prowlarr-config

    # Copy the configuration file to a temporary location
    $tempConfigPath = Join-Path $env:TEMP "prowlarr-config-temp.xml"
    Copy-Item "$REPO_ROOT/resources/xml/prowlarr-config.xml" $tempConfigPath

    # Replace the API key placeholder in the temporary file
    (Get-Content $tempConfigPath) -replace '\$PROWLARR_API_KEY', $env:PROWLARR_API_KEY `
    -replace '\$PROWLARR__POSTGRES_USER', $env:PROWLARR__POSTGRES_USER `
    -replace '\$PROWLARR__POSTGRES_PASSWORD', $env:PROWLARR__POSTGRES_PASSWORD `
    -replace '\$PROWLARR__POSTGRES_PORT', $env:PROWLARR__POSTGRES_PORT `
    -replace '\$PROWLARR__POSTGRES_HOST', $env:PROWLARR__POSTGRES_HOST `
    -replace '\$PROWLARR__POSTGRES_MAIN_DB', $env:PROWLARR__POSTGRES_MAIN_DB `
    -replace '\$PROWLARR__POSTGRES_LOG_DB', $env:PROWLARR__POSTGRES_LOG_DB | Set-Content $tempConfigPath

    # Copy the modified configuration file to the Docker volume
    docker run --rm `
        -v "${tempConfigPath}:/prowlarr-config-temp.xml" `
        -v "docker-compose_prowlarr-config:/config" `
        alpine /bin/sh -c "cp /prowlarr-config-temp.xml /config/config.xml && chmod 664 /config/config.xml && echo 'Prowlarr config setup complete.'"

    # Remove the temporary file
    Remove-Item $tempConfigPath

    # Pull and start containers
    docker compose pull prowlarr flaresolverr
    docker compose up -d prowlarr flaresolverr

    # Wait for Prowlarr to be ready
    Wait-ProwlarrReady

    # Create tag "flaresolverr"
    Invoke-ProwlarrApi -Uri 'http://localhost:9696/api/v1/tag' -Method "POST" -Body '{"label":"flaresolverr"}' -SuppressError

    # Create FlareSolverr proxy
    $proxyData = Get-Content -Raw -Path "$REPO_ROOT/resources/json/prowlarr_indexer_proxy.json"

    # Replace the FlareSolverr host placeholder
    $proxyData = $proxyData -replace '\$FLARESOLVERR_HOST', 'http://flaresolverr:8191'

    Invoke-ProwlarrApi -Uri 'http://localhost:9696/api/v1/indexerProxy' -Method "POST" -Body $proxyData -SuppressError

    # Configure indexers
    $indexers = Get-Content -Raw -Path "$REPO_ROOT/resources/json/prowlarr-indexers.json" | ConvertFrom-Json
    foreach ($indexer in $indexers) {
        Write-Output "Adding indexer: $($indexer.name)"
        $indexerJson = $indexer | ConvertTo-Json -Depth 10

        if ($indexer.fields | Where-Object { $_.name -eq "cardigannCaptcha" }) {
            Write-Output "Indexer $($indexer.name) requires captcha."
            Invoke-ProwlarrApi -Uri "http://localhost:9696/api/v1/indexer" -Method "POST" -Body $indexerJson -SuppressError
            Invoke-ProwlarrApi -Uri "http://localhost:9696/api/v1/indexer/action/checkCaptcha" -Method "POST" -Body $indexerJson -SuppressError
        } else {
            Invoke-ProwlarrApi -Uri "http://localhost:9696/api/v1/indexer" -Method "POST" -Body $indexerJson -SuppressError
        }
    }

    Write-Output "Indexers setup complete."
} catch {
    Write-Error "A critical error occurred: $_"
    exit 1
}