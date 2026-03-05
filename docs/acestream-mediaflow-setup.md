# AceStream + MediaFlow Proxy Setup

This guide explains how to configure AceStream playback in MediaFusion.

AceStream streams in MediaFusion are played through a separate
[MediaFlow Proxy](https://github.com/mhdzumair/mediaflow-proxy) service.

## What the MediaFusion fields mean

In `Configure -> External Services -> MediaFlow`:

- **Proxy URL**: Base URL of your MediaFlow Proxy instance
  (example: `http://your-server:8888`)
- **API Password**: The `API_PASSWORD` value configured in your MediaFlow deployment

Important: these values are for **MediaFlow**, not your MediaFusion server env.

## Prerequisites

1. A running MediaFlow Proxy instance.
2. A running AceEngine instance reachable by MediaFlow.
3. Access to edit MediaFlow environment variables.

## 1) Configure MediaFlow

Set these values in your MediaFlow environment:

- `API_PASSWORD=<your-strong-password>`
- `ENABLE_ACESTREAM=true`
- `ACESTREAM_HOST=<host-or-container-name-for-aceengine>`
- `ACESTREAM_PORT=6878`

Then restart MediaFlow Proxy.

## 2) Configure MediaFusion profile

1. Open the MediaFusion Configure page.
2. Go to `External Services -> MediaFlow`.
3. Set:
   - `Proxy URL` to your MediaFlow base URL.
   - `API Password` to the same value as MediaFlow `API_PASSWORD`.
4. Go to the `AceStream` section and enable AceStream streams.
5. Save your configuration.

## 3) Verify playback

1. Open content that has AceStream streams.
2. Start playback using the MediaFlow route.
3. If playback works, setup is complete.

## Troubleshooting

- **401/403 or auth errors**: `API_PASSWORD` mismatch between MediaFusion profile and MediaFlow.
- **Timeout or connection refused**: `Proxy URL` is wrong or MediaFlow is not reachable.
- **AceStream fails but MediaFlow works**:
  - Verify `ENABLE_ACESTREAM=true`.
  - Verify `ACESTREAM_HOST` and `ACESTREAM_PORT`.
  - Verify MediaFlow can reach AceEngine on the configured host/port.

