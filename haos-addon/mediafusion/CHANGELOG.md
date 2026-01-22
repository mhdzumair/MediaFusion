## Version 4.3.35 (2026-01-22)

### Initial Release

This is the first release of the MediaFusion Home Assistant OS add-on.

**Features:**
- ✅ Full MediaFusion 4.3.35 support
- ✅ PostgreSQL 16 database with optimizations for low-memory systems
- ✅ Redis caching for improved performance
- ✅ Cloudflare Tunnel integration for secure remote access
- ✅ WireGuard VPN support with split-tunneling
- ✅ VPN fail-closed mode for privacy
- ✅ Dramatiq background worker for async tasks
- ✅ Prowlarr integration support
- ✅ Premiumize OAuth support
- ✅ Optimized for Intel MacBook Air (amd64)
- ✅ Persistent storage in `/data`
- ✅ Comprehensive logging with privacy controls
- ✅ Health check monitoring
- ✅ Auto-restart on failure

**Architecture Support:**
- amd64 (Intel/AMD 64-bit)

**Debrid Services Supported:**
- Real-Debrid
- AllDebrid
- Premiumize

**Configuration Options:**
- Host URL configuration
- Secret key encryption
- Optional API password protection
- Configurable PostgreSQL connection pool
- Metadata cache TTL control
- Optional Prowlarr integration
- Log level control

**Security Features:**
- No privileged mode required
- Supervisor-safe operation
- VPN kill switch (fail-closed mode)
- Split-tunnel routing (HA and NAS traffic stays local)
- Minimal logging for privacy
- Cloudflare Tunnel support (no port forwarding needed)

**Known Limitations:**
- amd64 architecture only (optimized for Intel MacBook Air)
- Requires external debrid service subscription
- VPN requires WireGuard configuration

**Resource Usage:**
- Memory: ~300-500MB (typical)
- CPU: ~1-10% (typical, spikes during searches)
- Disk: ~200-650MB for data

**Documentation:**
- Full setup guide included
- Cloudflare Tunnel tutorial
- VPN configuration guide
- Family sharing instructions
- Troubleshooting section

---

## Upcoming Features

Planned for future releases:

- ARM64 support (Raspberry Pi)
- Built-in Prowlarr option
- Jellyfin/Plex integration
- Advanced caching options
- Automatic debrid service failover
- Metrics/monitoring dashboard
- Backup/restore functionality
