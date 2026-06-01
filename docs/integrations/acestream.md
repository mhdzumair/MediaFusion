# AceStream Integration

AceStream streams in MediaFusion are played through [MediaFlow Proxy](https://github.com/mhdzumair/mediaflow-proxy). You need three things:

1. A running **AceEngine** instance
2. A running **MediaFlow Proxy** instance (configured to reach AceEngine)
3. MediaFusion configured with your MediaFlow Proxy URL

## Prerequisites

- A deployed MediaFlow Proxy. See [MediaFlow Proxy](../configuration/mediaflow.md) for setup.
- An AceEngine instance reachable by MediaFlow (on the same host, Docker network, or remote).

## Step 1: Configure MediaFlow Proxy

Set these environment variables in your **MediaFlow Proxy** deployment:

```bash
API_PASSWORD=your_mediaflow_password
ENABLE_ACESTREAM=true
ACESTREAM_HOST=aceengine   # hostname/container name of AceEngine
ACESTREAM_PORT=6878
```

Restart MediaFlow Proxy after changing these values.

## Step 2: Configure MediaFusion

1. Open your MediaFusion instance → **Configure** → **External Services** → **MediaFlow**
2. Set **Proxy URL** to your MediaFlow base URL (e.g. `https://mediaflow.yourdomain.com`)
3. Set **API Password** to the `API_PASSWORD` you set in MediaFlow

!!! warning "These are MediaFlow credentials"
    The Proxy URL and API Password fields refer to your **MediaFlow** deployment, not your MediaFusion server.

## Step 3: Enable AceStream sources

In MediaFusion → **Configure** → **Catalogs**, enable:

- **AceStream** catalog

Background AceStream scraping can be controlled via:

```bash
IS_SCRAP_FROM_ACESTREAM_BACKGROUND=true
ACESTREAM_BACKGROUND_SEARCH_API_KEY=your_api_key  # optional
```

## Testing

After configuration, search for an AceStream-sourced channel in Stremio. The stream URL will be proxied through MediaFlow → AceEngine.

If streams don't play:
- Check that AceEngine is running and accessible from MediaFlow at the configured host/port
- Verify `ENABLE_ACESTREAM=true` is set in MediaFlow's environment
- Check MediaFlow logs for connection errors
