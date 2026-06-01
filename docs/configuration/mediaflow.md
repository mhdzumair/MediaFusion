# MediaFlow Proxy

[MediaFlow Proxy](https://github.com/mhdzumair/mediaflow-proxy) is a companion service that proxies debrid and live streams. It is required for:

- **AceStream** playback
- **MPD/DRM** streams
- Protecting your debrid account from multi-device IP bans when sharing an instance

## Setup

MediaFlow Proxy is a separate service you deploy alongside MediaFusion. See the [MediaFlow Proxy repository](https://github.com/mhdzumair/mediaflow-proxy) for deployment instructions.

Once deployed, configure the connection in MediaFusion's user Configure page:

1. Open your MediaFusion instance → **Configure** → **External Services** → **MediaFlow**
2. Set **Proxy URL** to your MediaFlow base URL (e.g. `https://mediaflow.yourdomain.com`)
3. Set **API Password** to the `API_PASSWORD` you set in MediaFlow's environment

!!! warning "These are MediaFlow credentials, not MediaFusion credentials"
    The Proxy URL and API Password on the Configure page refer to your **MediaFlow** deployment, not your MediaFusion server.

## MediaFlow environment variables

These control the MediaFlow integration in MediaFusion's server configuration:

| Variable | Default | Description |
|---|---|---|
| `MEDIAFLOW_PROXY_URL` | `None` | Base URL of your MediaFlow Proxy instance |
| `MEDIAFLOW_PROXY_PUBLIC_IP` | `None` | Public IP of the proxy (used for multi-region setups) |

## AceStream via MediaFlow

AceStream streams require both MediaFlow Proxy and an AceEngine instance. See the [AceStream integration guide](../integrations/acestream.md) for full setup instructions.

## MPD/DRM streams

MPD streams with DRM support are routed through MediaFlow automatically when a proxy URL is configured. No additional server-side configuration is required beyond setting `MEDIAFLOW_PROXY_URL`.
