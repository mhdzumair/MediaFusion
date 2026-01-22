# MediaFusion for Home Assistant OS

MediaFusion is a powerful debrid-based streaming provider that integrates with Stremio. This Home Assistant add-on allows you to run MediaFusion locally on your HAOS instance with full support for Cloudflare Tunnels and optional VPN routing.

## About

MediaFusion scrapes public torrent indexers (similar to Torrentio) and resolves all torrents through debrid services like Real-Debrid, AllDebrid, or Premiumize. **No torrenting or seeding happens from your IP address** - everything is resolved through debrid services.

This add-on is optimized for:
- UK-based family use
- Low-risk legal operation (debrid-only)
- Resource-constrained hardware (MacBook Air)
- Cloudflare Tunnel integration
- Optional VPN routing (without breaking Home Assistant or NAS access)

## Features

- **Debrid-only operation**: All torrents resolved through Real-Debrid/AllDebrid/Premiumize
- **No torrenting**: No peer-to-peer connections from your IP
- **Metadata caching**: Optional 5-minute cache for improved performance
- **Cloudflare Tunnel support**: Secure remote access for family members
- **Optional VPN**: Route MediaFusion traffic through WireGuard (Home Assistant and NAS traffic stays local)
- **VPN fail-closed**: Optional kill switch to stop traffic if VPN fails
- **Lightweight**: Optimized for Intel MacBook Air (amd64)
- **Privacy-focused**: Minimal logging, no analytics

## Installation

### 1. Add Custom Repository

1. Navigate to **Supervisor** → **Add-on Store** → **⋮ (three dots)** → **Repositories**
2. Add this repository URL: `https://github.com/YOUR-USERNAME/haos-mediafusion-addon`
3. Refresh the add-on store

### 2. Install the Add-on

1. Find "MediaFusion" in the add-on store
2. Click **INSTALL**
3. Wait for installation to complete

### 3. Configure the Add-on

Before starting, you **must** configure the add-on:

1. Go to **Configuration** tab
2. Set required values:
   - `host_url`: Your MediaFusion URL (e.g., `http://homeassistant.local:8000`)
   - `secret_key`: Generate with `openssl rand -hex 16` (must be 32+ characters)
   - `api_password`: Optional password to protect your instance

3. Click **SAVE**

### 4. Start the Add-on

1. Click **START**
2. Check the **Log** tab to ensure it started successfully
3. Enable **Start on boot** and **Watchdog** for reliability

## Quick Start

Once running, access MediaFusion at: `http://homeassistant.local:8000`

### For Stremio Users

1. Open Stremio
2. Go to **⚙️ Settings** → **Addons**
3. Add this manifest URL: `http://homeassistant.local:8000/manifest.json`
4. Configure your debrid service (Real-Debrid, AllDebrid, or Premiumize)
5. Start streaming!

## Configuration

See the **Documentation** tab for detailed configuration options including:
- Cloudflare Tunnel setup
- VPN configuration
- Prowlarr integration
- Resource optimization
- Family sharing setup

## Support

For issues or questions:
- Check the **Log** tab for errors
- Review the **Documentation** tab
- Visit: https://github.com/mhdzumair/MediaFusion

## Legal Notice

This add-on is designed for use with **debrid services only**. No torrenting or peer-to-peer connections are made from your IP address. All content resolution happens through your chosen debrid provider (Real-Debrid, AllDebrid, or Premiumize).

**Use responsibly and in accordance with your local laws.**
