# MediaFusion Add-on Documentation

## Table of Contents

1. [Configuration Options](#configuration-options)
2. [Cloudflare Tunnel Setup](#cloudflare-tunnel-setup)
3. [VPN Configuration](#vpn-configuration)
4. [Debrid Service Setup](#debrid-service-setup)
5. [Family Sharing with Stremio](#family-sharing-with-stremio)
6. [Prowlarr Integration](#prowlarr-integration)
7. [Performance Optimization](#performance-optimization)
8. [Troubleshooting](#troubleshooting)
9. [Security & Privacy](#security--privacy)

## Configuration Options

### Required Settings

#### `host_url` (required)
The URL where MediaFusion will be accessible.

**Examples:**
- Local only: `http://homeassistant.local:8000`
- With Cloudflare Tunnel: `https://mediafusion.your-domain.com`
- Custom port: `http://192.168.1.100:8000`

#### `secret_key` (required)
A 32+ character secret key for encryption. Generate with:

```bash
openssl rand -hex 16
```

**Example:** `3f8a9c7e2d1b6f4a8c9e7d5b3a1f8e6c`

⚠️ **Never share this key or commit it to version control!**

### Optional Settings

#### `api_password` (optional)
Password to protect your MediaFusion instance. If set, API requests will require authentication.

**Recommended for:** Public-facing instances

#### `enable_vpn` (default: false)
Enable WireGuard VPN routing for MediaFusion traffic.

**Important:**
- Only MediaFusion traffic goes through VPN
- Home Assistant and NAS traffic stays on local network
- See [VPN Configuration](#vpn-configuration) for setup

#### `vpn_config` (required if VPN enabled)
Your WireGuard configuration file content.

#### `vpn_fail_closed` (default: true)
If VPN connection drops, stop all internet traffic from MediaFusion.

**Recommended:** Keep enabled for privacy

#### `cloudflare_tunnel_enabled` (default: false)
Enable Cloudflare Tunnel for secure remote access.

See [Cloudflare Tunnel Setup](#cloudflare-tunnel-setup) for details.

#### `cloudflare_tunnel_token` (required if CF Tunnel enabled)
Your Cloudflare Tunnel token from the Cloudflare dashboard.

#### `postgres_max_connections` (default: 20, range: 10-100)
Maximum PostgreSQL connections. Lower values use less memory.

**Recommendations:**
- MacBook Air: 20 (default)
- Low memory: 10
- High traffic: 40-60

#### `metadata_cache_ttl` (default: 300, range: 60-600 seconds)
How long to cache metadata (in seconds).

**Recommendations:**
- Faster searches: 300-600 seconds
- Lower memory: 60-120 seconds

#### `enable_prowlarr` (default: false)
Enable Prowlarr integration for advanced indexer management.

See [Prowlarr Integration](#prowlarr-integration) for setup.

#### `log_level` (default: info)
Logging verbosity. Options: `debug`, `info`, `warning`, `error`

**Recommendations:**
- Normal use: `info`
- Troubleshooting: `debug`
- Production: `warning`

## Cloudflare Tunnel Setup

Cloudflare Tunnel allows your family to securely access MediaFusion remotely without opening ports or exposing your IP.

### Step 1: Create a Cloudflare Tunnel

1. Go to [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/)
2. Navigate to **Access** → **Tunnels**
3. Click **Create a tunnel**
4. Choose **Cloudflared**
5. Name your tunnel (e.g., "mediafusion")
6. Copy the tunnel token (starts with `eyJ...`)

### Step 2: Configure the Tunnel

1. In Cloudflare, set:
   - **Service**: HTTP
   - **URL**: `localhost:8000`
   - **Public hostname**: Your desired subdomain (e.g., `mediafusion.yourdomain.com`)

2. Save the configuration

### Step 3: Add Token to Add-on

1. Open MediaFusion add-on configuration
2. Enable `cloudflare_tunnel_enabled`: `true`
3. Paste your token in `cloudflare_tunnel_token`
4. Update `host_url` to your public domain: `https://mediafusion.yourdomain.com`
5. Save and restart the add-on

### Step 4: Test Access

1. Visit your public URL: `https://mediafusion.yourdomain.com`
2. You should see the MediaFusion interface
3. Share this URL with family members

### Cloudflare Tunnel Benefits

✅ **No port forwarding** - No router configuration needed
✅ **No IP exposure** - Your home IP stays hidden
✅ **Free SSL/TLS** - Automatic HTTPS
✅ **DDoS protection** - Cloudflare's network protects you
✅ **Access control** - Optional authentication via Cloudflare Access

## VPN Configuration

MediaFusion supports WireGuard VPN to route its traffic through a VPN provider while keeping Home Assistant and NAS traffic local.

### Why Use a VPN?

While MediaFusion uses debrid services (no torrenting from your IP), a VPN adds an extra privacy layer:
- Hides your debrid API requests from your ISP
- Adds geographic flexibility
- Extra privacy for UK users concerned about ISP logging

### Supported VPN Providers

Any WireGuard-compatible provider:
- Mullvad
- IVPN
- ProtonVPN
- Windscribe
- Any provider offering WireGuard configs

### Step 1: Get Your WireGuard Config

1. Sign up with a VPN provider
2. Download your WireGuard configuration file (`wg0.conf`)
3. Open the file in a text editor

Example config:
```ini
[Interface]
PrivateKey = YOUR_PRIVATE_KEY
Address = 10.64.0.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = SERVER_PUBLIC_KEY
Endpoint = vpn.server.com:51820
AllowedIPs = 0.0.0.0/0
```

### Step 2: Configure Add-on

1. Open MediaFusion add-on configuration
2. Set `enable_vpn`: `true`
3. Paste your **entire** WireGuard config into `vpn_config`
4. Set `vpn_fail_closed`: `true` (recommended)
5. Save and restart

### Step 3: Verify VPN Connection

Check the add-on logs for:
```
[INFO] WireGuard VPN connected successfully
[INFO] VPN IP: 10.64.0.2
```

Test your VPN IP:
```bash
curl -s http://homeassistant.local:8000/health
```

### VPN Routing Behavior

The add-on uses **split tunneling**:

| Traffic Type | Route |
|-------------|-------|
| MediaFusion API calls | ➜ VPN |
| Debrid service requests | ➜ VPN |
| Indexer scraping | ➜ VPN |
| Home Assistant UI | ➜ Local network |
| NAS access | ➜ Local network |
| Other HAOS add-ons | ➜ Local network |

This ensures your Home Assistant and NAS remain fully accessible while MediaFusion traffic goes through the VPN.

### VPN Fail-Closed Mode

When `vpn_fail_closed: true`:
- If VPN drops, MediaFusion stops all internet traffic
- No requests leak to your real IP
- Add-on will attempt to reconnect automatically
- If reconnection fails repeatedly, check your VPN credentials

When `vpn_fail_closed: false`:
- If VPN drops, traffic continues on your real IP
- Less secure but more resilient
- **Not recommended for privacy-conscious users**

## Debrid Service Setup

MediaFusion requires a debrid service to resolve torrents. **This is how it stays legal and safe** - you never torrent directly.

### Supported Debrid Services

1. **Real-Debrid** (recommended)
   - Most popular
   - Great reliability
   - ~€16/6 months
   - [Get Real-Debrid](https://real-debrid.com/?id=1234567)

2. **AllDebrid**
   - Good alternative
   - ~€3/month
   - [Get AllDebrid](https://alldebrid.com/)

3. **Premiumize**
   - Premium option
   - Built-in VPN
   - ~€10/month
   - [Get Premiumize](https://www.premiumize.me/)

### Step 1: Sign Up for a Debrid Service

Choose one service above and create an account.

### Step 2: Configure in Stremio

Once MediaFusion is running:

1. Open Stremio
2. Add MediaFusion: `http://homeassistant.local:8000/manifest.json`
3. Click **Configure**
4. Choose your debrid service
5. Enter your API key (found in your debrid account settings)
6. Save

### Step 3: Test Streaming

1. Search for a movie or TV show in Stremio
2. MediaFusion will show available streams from your debrid service
3. Click to play - streaming starts immediately (no downloading)

### How It Works

```
You → Stremio → MediaFusion → Indexers (public torrents)
                     ↓
                Debrid Service (resolves torrent)
                     ↓
                Direct stream ← You
```

**Your IP never touches the torrent swarm.** All downloading happens on the debrid service's servers.

## Family Sharing with Stremio

Share MediaFusion with family members using Cloudflare Tunnel.

### Setup Instructions for Family

Send these instructions to family members:

---

**How to Set Up MediaFusion in Stremio**

1. **Install Stremio**
   - Download from: https://www.stremio.com/downloads
   - Available for Windows, Mac, Linux, Android, iOS

2. **Add MediaFusion Add-on**
   - Open Stremio
   - Go to **⚙️ Settings** → **Addons**
   - Scroll to bottom and click **Community Addons**
   - In the URL bar, enter: `https://mediafusion.yourdomain.com/manifest.json`
   - Click **Install**

3. **Configure Your Debrid Service**
   - Click **Configure** on the MediaFusion addon
   - Select your debrid service (Real-Debrid recommended)
   - Enter your API key from your debrid account
   - Click **Save**

4. **Start Streaming**
   - Search for any movie or TV show
   - MediaFusion streams will appear (labeled "MediaFusion")
   - Click to play!

---

### Multi-User Notes

- Each family member needs their own debrid account
- MediaFusion handles multiple concurrent users
- Metadata cache is shared (faster searches for everyone)

### Access Control

For extra security, set `api_password` in the add-on config. Family members will need this password when configuring the addon in Stremio.

## Prowlarr Integration

Prowlarr is an advanced indexer manager that expands MediaFusion's torrent sources.

### What is Prowlarr?

Prowlarr aggregates dozens of torrent indexers (both public and private) and provides a unified API for MediaFusion to search.

**Benefits:**
- Access to 500+ indexers
- Private tracker support
- Better search results
- Automatic indexer health monitoring

### Setup Instructions

#### Option 1: Prowlarr Add-on (Recommended for HAOS)

1. Install **Prowlarr** add-on from Home Assistant add-on store
2. Configure Prowlarr:
   - Open Prowlarr UI: `http://homeassistant.local:9696`
   - Add indexers (Settings → Indexers → Add Indexer)
   - Get API key (Settings → General → Security → API Key)

3. Configure MediaFusion add-on:
   ```yaml
   enable_prowlarr: true
   prowlarr_url: "http://homeassistant.local:9696"
   prowlarr_api_key: "YOUR_API_KEY"
   ```

#### Option 2: External Prowlarr

If running Prowlarr elsewhere:

```yaml
enable_prowlarr: true
prowlarr_url: "http://192.168.1.50:9696"
prowlarr_api_key: "YOUR_API_KEY"
```

### Recommended Indexers

Public (free):
- 1337x
- RARBG
- The Pirate Bay
- YTS
- EZTV

Private (require account):
- IPTorrents
- TorrentLeech
- BroadcasTheNet

Add as many as you like - Prowlarr handles rate limiting and health checks.

## Performance Optimization

MediaFusion is optimized for the Intel MacBook Air, but here are tips for best performance:

### Memory Optimization

MacBook Air typically has 8GB RAM. Recommended settings:

```yaml
postgres_max_connections: 20
metadata_cache_ttl: 300
workers: 2  # (automatically set)
```

If experiencing memory issues:
```yaml
postgres_max_connections: 10
metadata_cache_ttl: 120
```

### Disk Space

MediaFusion uses minimal disk space:
- PostgreSQL database: ~100-500MB
- Redis cache: ~50-100MB
- Logs: ~10-50MB

**Total: ~200-650MB**

Persistent data stored in `/data`:
```
/data/
├── postgres/     # Database files
├── redis/        # Cache files
├── cache/        # Metadata cache
└── logs/         # Application logs
```

### Network Performance

For best performance:
- Use wired Ethernet (if possible)
- Ensure good Wi-Fi signal
- Consider QoS rules for MediaFusion traffic

### CPU Usage

MediaFusion uses minimal CPU:
- Idle: ~1-2% CPU
- Active search: ~5-10% CPU
- Peak: ~15-20% CPU

The MacBook Air's dual-core Intel processor is more than sufficient.

### Database Maintenance

PostgreSQL auto-vacuums, but you can manually optimize:

```bash
# Access add-on terminal
psql -U mediafusion mediafusion -c "VACUUM ANALYZE;"
```

Run monthly for best performance.

## Troubleshooting

### Add-on Won't Start

**Check logs:**
1. Go to add-on **Log** tab
2. Look for error messages

**Common issues:**

❌ **"SECRET_KEY is required"**
- Solution: Generate and set `secret_key` in configuration

❌ **"Database migration failed"**
- Solution: Delete `/data/postgres` and restart (⚠️ loses data)

❌ **"Port 8000 already in use"**
- Solution: Check for other services using port 8000, change port in config

### Can't Access MediaFusion UI

**Local access test:**
```bash
curl http://localhost:8000/health
```

If this works but browser doesn't:
- Check firewall rules
- Try `http://homeassistant.local:8000`
- Try `http://[HAOS_IP]:8000`

### VPN Not Connecting

**Check VPN config:**
1. Verify WireGuard config is complete
2. Check endpoint is reachable
3. Verify private key is correct

**Test manually:**
```bash
# Access add-on terminal
wg show wg0
```

Should show handshake and transfer stats.

### Cloudflare Tunnel Issues

**Common problems:**

❌ **"Tunnel token invalid"**
- Solution: Generate new token in Cloudflare dashboard

❌ **"Connection timeout"**
- Solution: Check Cloudflare dashboard for tunnel status

❌ **502 Bad Gateway**
- Solution: Ensure MediaFusion is running (check logs)

### Slow Search Results

**Optimization steps:**

1. Increase cache TTL:
   ```yaml
   metadata_cache_ttl: 600  # 10 minutes
   ```

2. Enable Prowlarr for better indexer performance

3. Check debrid service status (sometimes they rate-limit)

### Database Issues

**Reset database** (⚠️ loses all data):

```bash
# Stop add-on
# Access terminal or SSH
rm -rf /data/postgres
# Start add-on
```

### Logs Too Large

**Reduce log verbosity:**
```yaml
log_level: warning
```

**Clear logs:**
```bash
rm -rf /data/logs/*
```

## Security & Privacy

### What MediaFusion Logs

**Logged (necessary for operation):**
- Search queries (cached for 5 minutes)
- Debrid service API calls
- Error messages

**NOT logged:**
- Your real IP (when using VPN)
- Streaming activity
- Personal information

### Log Privacy

Logs stored in `/data/logs/` contain:
- Access logs (IP addresses, timestamps, requests)
- Error logs (debug information)

**To minimize logging:**
```yaml
log_level: error
```

**To disable access logs** (edit Dockerfile):
```dockerfile
--access-logfile /dev/null
```

### Debrid Service Privacy

Your debrid service (Real-Debrid, etc.) logs:
- Downloaded torrents
- IP addresses
- API usage

**Debrid services are generally privacy-friendly:**
- Real-Debrid: Based in France, GDPR-compliant
- AllDebrid: Based in France, GDPR-compliant
- Premiumize: Based in Switzerland, strong privacy laws

### Network Security

**Recommendations:**

1. **Use Cloudflare Tunnel** (not port forwarding)
2. **Enable VPN** for extra privacy
3. **Set API password** to prevent unauthorized access
4. **Use HTTPS** (automatic with Cloudflare Tunnel)

### Legal Considerations (UK-Specific)

MediaFusion with debrid services is **low-risk** in the UK:

✅ **You are NOT torrenting** - no P2P activity from your IP
✅ **Debrid services are legal** - they download content, not you
✅ **No public upload** - you don't seed/share torrents
✅ **Private use** - small family use is lower risk

⚠️ **However:**
- Using debrid services to access copyrighted content is still legally gray
- ISPs may log traffic to debrid services (use VPN for privacy)
- This setup is for personal/family use only

**Disclaimer:** This information is for educational purposes. Consult a legal professional for advice specific to your situation.

### Best Practices

1. **Always use debrid services** (never direct torrenting)
2. **Enable VPN** for maximum privacy
3. **Use Cloudflare Tunnel** instead of exposing ports
4. **Set strong passwords** for debrid accounts
5. **Keep add-on updated** for security patches
6. **Regular backups** of `/data` directory
7. **Monitor logs** for suspicious activity

## Advanced Configuration

### Custom PostgreSQL Tuning

For advanced users, PostgreSQL can be tuned in `run.sh`:

```bash
echo "shared_buffers = 256MB" >> /data/postgres/postgresql.conf
echo "effective_cache_size = 512MB" >> /data/postgres/postgresql.conf
echo "work_mem = 16MB" >> /data/postgres/postgresql.conf
```

Restart add-on to apply changes.

### Redis Tuning

Redis is configured for low memory usage:
- Max memory: 256MB
- Eviction policy: allkeys-lru (least recently used)

To increase cache size, edit `run.sh`:
```bash
--maxmemory 512mb
```

### Multiple Debrid Accounts

MediaFusion supports multiple users with different debrid accounts:
- Each family member configures their own debrid API key in Stremio
- MediaFusion handles concurrent requests
- Metadata cache is shared (faster for everyone)

### Backup & Restore

**Backup:**
```bash
# Stop add-on
tar -czf mediafusion-backup.tar.gz /data
```

**Restore:**
```bash
# Stop add-on
tar -xzf mediafusion-backup.tar.gz -C /
# Start add-on
```

## Support & Community

- **MediaFusion GitHub**: https://github.com/mhdzumair/MediaFusion
- **Home Assistant Community**: https://community.home-assistant.io/
- **Issues**: Create an issue in the add-on repository

## Changelog

### Version 4.3.35
- Initial HAOS add-on release
- PostgreSQL 16 support
- Cloudflare Tunnel integration
- WireGuard VPN support
- Optimized for MacBook Air (amd64)

---

**Enjoy streaming with MediaFusion! 🎬**
