# MediaFusion Home Assistant Add-on Repository

Custom Home Assistant add-on repository for MediaFusion - a debrid-based streaming provider for Stremio.

## About MediaFusion

MediaFusion is a powerful streaming aggregator that:
- Scrapes public torrent indexers (like Torrentio)
- Resolves all torrents through debrid services (Real-Debrid, AllDebrid, Premiumize)
- Provides instant streaming without any torrenting from your IP
- Integrates seamlessly with Stremio

## Available Add-ons

### MediaFusion
![Version](https://img.shields.io/badge/version-4.3.35-blue.svg)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green.svg)

Debrid-based streaming provider optimized for Home Assistant OS on Intel MacBook Air.

**Features:**
- 🚫 No torrenting - debrid services only
- 🔒 VPN support with split-tunneling
- ☁️ Cloudflare Tunnel integration
- 💾 Metadata caching for better performance
- 🔑 API password protection
- 🎯 Optimized for low-resource systems

[Open add-on documentation](mediafusion/DOCS.md)

## Installation

1. **Add this repository to Home Assistant:**
   - Go to **Supervisor** → **Add-on Store** → **⋮** → **Repositories**
   - Add: `https://github.com/YOUR-USERNAME/haos-mediafusion-addon`

2. **Install MediaFusion add-on:**
   - Find "MediaFusion" in the add-on store
   - Click **INSTALL**
   - Configure and start

3. **Detailed installation guide:**
   - See [INSTALL.md](mediafusion/INSTALL.md)

## Requirements

- Home Assistant OS (Supervisor)
- amd64 architecture (Intel/AMD 64-bit)
- Debrid service account (Real-Debrid, AllDebrid, or Premiumize)
- ~500MB available memory
- ~1GB available storage

## Documentation

- [Installation Guide](mediafusion/INSTALL.md) - Step-by-step setup
- [Configuration Guide](mediafusion/DOCS.md) - Detailed configuration options
- [Changelog](mediafusion/CHANGELOG.md) - Version history

## Quick Start

1. Install add-on
2. Generate secret key: `openssl rand -hex 16`
3. Configure:
   ```yaml
   host_url: "http://homeassistant.local:8000"
   secret_key: "YOUR_GENERATED_KEY"
   ```
4. Start add-on
5. Access at `http://homeassistant.local:8000`
6. Add to Stremio: `http://homeassistant.local:8000/manifest.json`

## Features Comparison

| Feature | MediaFusion Add-on | External Docker |
|---------|-------------------|-----------------|
| HAOS Integration | ✅ Native | ❌ Manual |
| Supervisor Safe | ✅ Yes | N/A |
| Auto-start | ✅ Yes | ❌ Manual |
| Watchdog | ✅ Built-in | ❌ Manual |
| Configuration UI | ✅ Yes | ❌ ENV files |
| Logs Integration | ✅ Native | ❌ Docker logs |
| VPN Split-tunnel | ✅ Yes | ❌ Complex |
| Cloudflare Tunnel | ✅ Built-in | ❌ Separate container |

## Architecture Support

| Architecture | Supported | Tested |
|-------------|-----------|--------|
| amd64 | ✅ Yes | ✅ MacBook Air |
| armv7 | ❌ No | - |
| aarch64 | 🔄 Planned | - |

## Debrid Services

MediaFusion supports these debrid services:

### Real-Debrid (Recommended)
- Most popular choice
- Excellent reliability
- ~€16 for 6 months
- [Get Real-Debrid](https://real-debrid.com)

### AllDebrid
- Good alternative
- Competitive pricing ~€3/month
- [Get AllDebrid](https://alldebrid.com)

### Premiumize
- Premium option
- Built-in VPN
- ~€10/month
- [Get Premiumize](https://www.premiumize.me)

**Note:** You need an active subscription to one of these services to use MediaFusion.

## Optional Integrations

### Cloudflare Tunnel
Securely expose MediaFusion to family members remotely without port forwarding.

**Benefits:**
- No exposed ports
- Free SSL/TLS
- DDoS protection
- Hidden IP address

### WireGuard VPN
Route MediaFusion traffic through a VPN while keeping Home Assistant and NAS traffic local.

**Benefits:**
- Extra privacy layer
- ISP traffic obfuscation
- Split-tunnel routing
- Fail-closed option

### Prowlarr
Advanced indexer management for better search results.

**Benefits:**
- 500+ indexer support
- Private tracker integration
- Health monitoring
- Automatic rate limiting

## Use Case

This add-on is designed for:

✅ **UK-based family use** - Small private streaming setup
✅ **MacBook Air deployment** - Optimized for resource-constrained hardware
✅ **Privacy-conscious users** - VPN support and minimal logging
✅ **Debrid-only streaming** - No torrenting from your IP
✅ **Cloudflare Tunnel users** - Secure remote access

## Legal Notice

MediaFusion with debrid services is designed for legal streaming:

- ✅ No torrenting or seeding from your IP
- ✅ All content resolution via debrid providers
- ✅ Private family use only

**Important:** Using debrid services to access copyrighted content may be legally questionable in your jurisdiction. This software is provided for educational purposes. Consult local laws and use responsibly.

## Support

- **Issues:** [GitHub Issues](https://github.com/YOUR-USERNAME/haos-mediafusion-addon/issues)
- **MediaFusion Project:** [GitHub](https://github.com/mhdzumair/MediaFusion)
- **Home Assistant Community:** [Forum](https://community.home-assistant.io/)

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Credits

- **MediaFusion**: Created by [mhdzumair](https://github.com/mhdzumair)
- **Home Assistant Add-on**: Community contribution
- **Base Image**: Home Assistant base images

## License

This add-on repository is licensed under the MIT License.

MediaFusion itself is licensed under the MIT License - see the [MediaFusion repository](https://github.com/mhdzumair/MediaFusion) for details.

---

**Made with ❤️ for the Home Assistant community**
