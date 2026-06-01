# Streaming Providers

Configure which streaming providers are available to users on your instance.

## Supported providers

| Provider | Variable prefix | Type | Cost |
|---|---|---|---|
| Direct P2P | — | Torrent | Free |
| Real-Debrid | — | Debrid | Premium |
| AllDebrid | — | Debrid | Premium |
| Premiumize | `PREMIUMIZE_OAUTH_` | Debrid | Premium |
| Debrid-Link | — | Debrid | Premium |
| Torbox | — | Debrid | Free quota / Premium |
| OffCloud | — | Debrid | Free quota / Premium |
| PikPak | — | Cloud | Free quota / Premium |
| Seedr.cc | — | Cloud | Free quota / Premium |
| qBittorrent WebDAV | — | Self-hosted | Free |
| StremThru | — | Interface | — |
| EasyDebrid | — | Debrid | — |

## Disabling providers

Remove specific providers from the Configure UI entirely:

```bash
DISABLED_PROVIDERS='["realdebrid","alldebrid"]'
```

Valid values: `p2p`, `realdebrid`, `seedr`, `debridlink`, `alldebrid`, `offcloud`, `pikpak`, `torbox`, `premiumize`, `qbittorrent`, `stremthru`, `easydebrid`, `debrider`

## Provider limits

| Variable | Default | Description |
|---|---|---|
| `MAX_STREAMING_PROVIDERS_PER_PROFILE` | `5` | Maximum number of providers a user can configure in one profile |

## Premiumize OAuth

Premiumize requires OAuth credentials (obtained free from [premiumize.me/registerclient](https://www.premiumize.me/registerclient)):

```bash
PREMIUMIZE_OAUTH_CLIENT_ID=your_client_id
PREMIUMIZE_OAUTH_CLIENT_SECRET=your_client_secret
```

## Provider signup links

Customize referral/signup links shown in the Configure UI. These are appended to the built-in defaults:

```bash
PROVIDER_SIGNUP_LINKS='{"realdebrid":["https://real-debrid.com/?id=9999999"],"alldebrid":["https://alldebrid.com/?uid=custom"]}'
```

The UI randomly picks one link per provider per display.

## StremThru cache storage

| Variable | Default | Description |
|---|---|---|
| `STORE_STREMTHRU_MAGNET_CACHE` | `false` | Store resolved magnet links in StremThru cache |

## Rust service port

| Variable | Default | Description |
|---|---|---|
| `STREAM_RS_PORT` | `8000` | Port the Rust API server listens on |
